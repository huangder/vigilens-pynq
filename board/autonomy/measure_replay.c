/* Synthetic offline/on-board replay only. No model, capture or rPPG claim.
 * Build with -I directory-containing-generated-vg_config.h and link vg_measure.
 * Text format: VIGILENS_REPLAY_V1 synthetic N, then N lines of 44 tokens:
 * id ts mean_luma q16 valid eyes_present visible left_present right_present
 * mouth_present yaw pitch, left eye p1..p6 x/y, right eye p1..p6 x/y,
 * mouth left/right/top/bottom x/y. All doubles must be finite.
 */
#include "measure.h"
#include "vg_config.h"
#include <ctype.h>
#include <errno.h>
#include <fenv.h>
#include <inttypes.h>
#include <locale.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Resource bounds for this diagnostic file parser, not measurement thresholds. */
#define LINE_SIZE 4096
#define MAX_RECORDS 100000u
#define FIELD_COUNT 44
static int line(FILE *file, char *buffer) {
    size_t n;
    if (!fgets(buffer,LINE_SIZE,file)) return 0;
    n=strlen(buffer);
    if (n && buffer[n-1]!='\n' && !feof(file)) return -1;
    return 1;
}
static int split(char *text, char **tokens, unsigned cap) {
    unsigned count=0;
    char *token=strtok(text," \t\r\n");
    while (token) {
        if (count==cap) return -1;
        tokens[count++]=token; token=strtok(NULL," \t\r\n");
    }
    return (int)count;
}
static int integer(const char *text, uint64_t max, uint64_t *out) {
    char *end;
    unsigned long long value;
    const char *p=text;
    if (!*p) return -1;
    while (*p) { if (!isdigit((unsigned char)*p++)) return -1; }
    errno=0; value=strtoull(text,&end,10);
    if (errno || *end || value>max) return -1;
    *out=(uint64_t)value; return 0;
}
static int number(const char *text, double *out) {
    char *end;
    double value;
    errno=0; value=strtod(text,&end);
    if (*end || end==text || !isfinite(value) || errno==ERANGE) return -1;
    *out=value; return 0;
}
static int record(char **f, vg_measure_input *i) {
    uint64_t value;
    double numbers[FIELD_COUNT]={0};
    unsigned k;
    vg_point *mouth[4]={&i->observation.mouth.left,&i->observation.mouth.right,
                        &i->observation.mouth.top,&i->observation.mouth.bottom};
    if (integer(f[0],UINT64_MAX,&i->frame_id)) return -1;
    for (k=1;k<FIELD_COUNT;++k) {
        if (k==3 || k==4 || k==5 || k==7 || k==8 || k==9) continue;
        if (number(f[k],&numbers[k])) return -1;
    }
    if (integer(f[3],UINT32_MAX,&value)) return -1;
    i->motion_q16=(uint32_t)value;
    if (integer(f[4],1,&value)) return -1;
    i->motion_valid=(int)value;
    if (integer(f[5],1,&value)) return -1;
    i->observation.eyes_present=(int)value;
    if (integer(f[7],1,&value)) return -1;
    i->observation.left.present=(int)value;
    if (integer(f[8],1,&value)) return -1;
    i->observation.right.present=(int)value;
    if (integer(f[9],1,&value)) return -1;
    i->observation.mouth.present=(int)value;
    i->ts=numbers[1]; i->mean_luma=numbers[2];
    i->observation.visible=numbers[6]; i->face.visible=numbers[6];
    i->face.yaw=numbers[10]; i->face.pitch=numbers[11];
    for (k=0;k<6;++k) {
        i->observation.left.p[k].x=numbers[12+2*k];
        i->observation.left.p[k].y=numbers[13+2*k];
        i->observation.right.p[k].x=numbers[24+2*k];
        i->observation.right.p[k].y=numbers[25+2*k];
    }
    for (k=0;k<4;++k) { mouth[k]->x=numbers[36+2*k]; mouth[k]->y=numbers[37+2*k]; }
    return 0;
}
static void string(const char *s) {
    const unsigned char *p=(const unsigned char *)s;
    putchar('"');
    for (;*p;++p) {
        if (*p=='"' || *p=='\\') { putchar('\\'); putchar(*p); }
        else if (*p<32) printf("\\u%04x",*p);
        else putchar(*p);
    }
    putchar('"');
}
static void output(unsigned record_id, const vg_measure_input *i, int rc, const vg_measure_result *r) {
    unsigned k;
    printf("{\"kind\":\"measure_replay\",\"synthetic\":true,\"config_sha256\":\"%s\",\"record\":%u,\"frame_id\":%" PRIu64 ",\"ts\":%.17g,\"rc\":%d",
           VG_CONFIG_SHA256,record_id,i->frame_id,i->ts,rc);
    if (!rc) {
        const vg_behavior_result *b=&r->behavior;
        const vg_quality_result *q=&r->quality;
        const vg_decision_result *d=&r->decision;
        printf(",\"behavior\":{\"ear_left\":%.17g,\"ear_right\":%.17g,\"blink_state\":",b->ear_left,b->ear_right);
        string(vg_eye_state_name(b->blink_state));
        printf(",\"blink_count\":%" PRIu64 ",\"blink_rate_per_min\":%.17g,\"perclos\":%.17g,\"long_close_count\":%" PRIu64 ",\"mar\":%.17g,\"yawn_count\":%" PRIu64 ",\"_dt\":%.17g}",
               b->blink_count,b->blink_rate_per_min,b->perclos,b->long_close_count,b->mar,b->yawn_count,b->dt);
        printf(",\"quality\":{\"light_score\":%.17g,\"motion_score\":%.17g,\"overall\":%.17g,\"_mean_luma\":%.17g,\"_motion_ratio\":%.17g}",
               q->light_score,q->motion_score,q->overall,q->mean_luma,q->motion_ratio);
        printf(",\"decision\":{\"status\":"); string(d->status);
        printf(",\"advice\":"); string(d->advice);
        printf(",\"reason\":"); string(d->reason);
        printf(",\"triggers\":[");
        for (k=0;k<d->trigger_count;++k) {
            const vg_decision_trigger *t=&d->triggers[k];
            if (k) putchar(',');
            printf("{\"rule\":"); string(t->rule);
            printf(",\"metric\":"); string(t->metric);
            printf(",\"verdict\":"); string(t->verdict);
            printf(",\"value\":%.17g,\"threshold\":%.17g}",t->value,t->threshold);
        }
        printf("]},\"vital\":{\"hr_bpm\":null,\"hr_conf\":null,\"rr_per_min\":null,\"rr_conf\":null}");
    }
    puts("}");
}
int main(int argc, char **argv) {
    FILE *file;
    vg_measure *engine;
    char buffer[LINE_SIZE], *fields[FIELD_COUNT];
    uint64_t count;
    unsigned k;
    int status=0;
    if (argc!=2) { fprintf(stderr,"usage: vg_measure_replay synthetic_fixture.txt\n"); return 2; }
    if (!setlocale(LC_NUMERIC,"C") || fesetround(FE_TONEAREST)) return 2;
    file=fopen(argv[1],"rb");
    if (!file) { perror("fixture"); return 2; }
    if (line(file,buffer)!=1 || split(buffer,fields,FIELD_COUNT)!=3 ||
        strcmp(fields[0],"VIGILENS_REPLAY_V1") || strcmp(fields[1],"synthetic") ||
        integer(fields[2],MAX_RECORDS,&count)) {
        fprintf(stderr,"invalid synthetic fixture header\n"); fclose(file); return 2;
    }
    engine=vg_measure_create(&vg_exported_config);
    if (!engine) { fclose(file); fprintf(stderr,"invalid configuration or allocation failure\n"); return 2; }
    for (k=0;k<count;++k) {
        vg_measure_input input={0};
        vg_measure_result result;
        int rc;
        if (line(file,buffer)!=1 || split(buffer,fields,FIELD_COUNT)!=FIELD_COUNT || record(fields,&input)) {
            fprintf(stderr,"invalid fixture record %u (length/fields/nonfinite/number)\n",k);
            status=2; break;
        }
        rc=vg_measure_update(engine,&input,&result);
        output(k,&input,rc,&result);
    }
    if (!status) {
        int ch;
        while ((ch=fgetc(file))!=EOF) {
            if (!isspace((unsigned char)ch)) { fprintf(stderr,"trailing fixture data\n"); status=2; break; }
        }
        if (ferror(file)) status=2;
    }
    if (ferror(stdout)) status=2;
    vg_measure_destroy(engine); fclose(file);
    return status;
}
