/* Board-side measurement service: line protocol in, contract JSON out.
 *
 * Compatibility adapter over autonomy_engine: a producer sends same-frame
 * 68-point geometry, pose, quality statistics and forehead RGB sums. Camera
 * programs can call the engine directly, with no TCP or text round trip.
 *
 * Line format (17 + 136 tokens):
 *   frame_id ts visible bx by bw bh yaw pitch roll mean_luma motion_q16 motion_valid
 *   count sum_r sum_g sum_b x0 y0 ... x67 y67
 * Response: one contract JSON object, or "ERR <rc>".
 *
 * Usage: vg_autonomy_service [--tcp PORT] [--vital-every N]
 *        [--pose producer|pnp] [--width W] [--height H]
 */
#define _POSIX_C_SOURCE 200809L
#include "autonomy_engine.h"
#include <limits.h>
#include "vg_config.h"
#include "vg_vital_config.h"

#ifdef VG_SERVICE_TCP
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#endif
#include <errno.h>
#include <fenv.h>
#include <locale.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define LINE_SIZE 8192
#define POINTS 68

static double now_ms(void)
{
    struct timespec ts;
    timespec_get(&ts, TIME_UTC);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

typedef struct {
    uint64_t frame_id;
    double ts, visible, bbox[4], pose[3], mean_luma, sums[3];
    uint32_t count, motion_q16;
    int motion_valid;
    double points[POINTS][2];
} frame_line;

static int parse_line(char *text, frame_line *out)
{
    char *tokens[17 + POINTS * 2];
    char *token;
    unsigned count = 0;
    int i;
    token = strtok(text, " \t\r\n");
    while (token) {
        if (count >= sizeof(tokens) / sizeof(tokens[0])) return -1;
        tokens[count++] = token;
        token = strtok(NULL, " \t\r\n");
    }
    if (count != sizeof(tokens) / sizeof(tokens[0])) return -1;
    {
        char *end;
        errno=0;
        if (tokens[0][0] == '-') return -1;
        out->frame_id = strtoull(tokens[0], &end, 10);
        if (errno || end==tokens[0] || *end) return -1;
    }
    for (i = 1; i < 17; ++i) {
        char *end;
        double value = strtod(tokens[i], &end);
        if (end==tokens[i] || *end || !isfinite(value)) return -1;
        if ((i==11 || i==13 || i>=14) && (value<0 || value>UINT32_MAX || value!=floor(value))) return -1;
        if (i==12 && value!=0 && value!=1) return -1;
        if (i>=3 && i<=6 && (value<0 || value>INT_MAX)) return -1;
        switch (i) {
        case 1: out->ts = value; break;
        case 2: out->visible = value; break;
        case 3: out->bbox[0] = value; break;
        case 4: out->bbox[1] = value; break;
        case 5: out->bbox[2] = value; break;
        case 6: out->bbox[3] = value; break;
        case 7: out->pose[0] = value; break;
        case 8: out->pose[1] = value; break;
        case 9: out->pose[2] = value; break;
        case 10: out->mean_luma = value; break;
        case 11: out->motion_q16 = (uint32_t)value; break;
        case 12: out->motion_valid = (int)value; break;
        case 13: out->count = (uint32_t)value; break;
        case 14: out->sums[0] = value; break;
        case 15: out->sums[1] = value; break;
        case 16: out->sums[2] = value; break;
        default: break;
        }
    }
    for (i = 0; i < POINTS; ++i) {
        char *end;
        out->points[i][0] = strtod(tokens[17 + 2 * i], &end);
        if (*end || !isfinite(out->points[i][0])) return -1;
        out->points[i][1] = strtod(tokens[18 + 2 * i], &end);
        if (*end || !isfinite(out->points[i][1])) return -1;
    }
    return 0;
}

static int serve(FILE *in, FILE *out, int vital_every, int pose_pnp, int width, int height)
{
    vg_autonomy *engine = vg_autonomy_create(&vg_exported_config,
        &vg_exported_vital_config, VG_POSE_REPROJ_MAX_FRAC, vital_every, pose_pnp);
    char line[LINE_SIZE];
    unsigned frames = 0;
    double total_ms = 0.0;
    if (!engine) return 1;
    if(vg_autonomy_configure_hr_filter(engine,&vg_exported_hr_filter_config)){vg_autonomy_destroy(engine);return 1;}
    while (fgets(line, sizeof(line), in)) {
        frame_line parsed = {0};
        vg_autonomy_frame frame = {0};
        vg_autonomy_result result;
        double started = now_ms();
        int rc, k;
        if (parse_line(line, &parsed)) {
            vg_autonomy_invalidate(engine);
            fprintf(stderr, "SERVICE_PARSE_FAIL frame=%u\n", frames);
            fprintf(out, "ERR -1\n"); fflush(out); continue;
        }
        frame.frame_id=parsed.frame_id; frame.ts=parsed.ts;
        frame.width=width; frame.height=height; frame.visible=parsed.visible;
        for (k=0; k<4; ++k) frame.bbox[k]=(int)lround(parsed.bbox[k]);
        memcpy(frame.pose,parsed.pose,sizeof(frame.pose));
        frame.mean_luma=parsed.mean_luma; frame.motion_q16=parsed.motion_q16;
        frame.motion_valid=parsed.motion_valid; frame.count=parsed.count;
        for (k=0; k<3; ++k) frame.sums[k]=(uint32_t)parsed.sums[k];
        memcpy(frame.points,parsed.points,sizeof(frame.points));
        rc=vg_autonomy_update(engine,&frame,&result);
        if (rc) { fprintf(out,"ERR %d\n",rc); fflush(out); continue; }
        if (vg_autonomy_write(out,&result)) {
            fprintf(stderr,"SERVICE_CONTRACT_FAIL frame=%u\n",frames);
            fprintf(out,"ERR -3\n"); fflush(out); continue;
        }
        fflush(out);
        total_ms += now_ms()-started;
        if (frames%30==0) fprintf(stderr,"SERVICE frame=%u ms=%.3f\n",frames,now_ms()-started);
        ++frames;
    }
    fprintf(stderr,"SERVICE_SUMMARY frames=%u mean_ms=%.3f\n",frames,frames ? total_ms/frames : 0.0);
    vg_autonomy_destroy(engine);
    return 0;
}

int main(int argc, char **argv)
{
    int port = 0, vital_every = 30, pose_pnp = 0, width = 1280, height = 720, i;
    if (!setlocale(LC_NUMERIC, "C") || fesetround(FE_TONEAREST)) return 2;
#ifndef _WIN32
    signal(SIGPIPE, SIG_IGN);
#endif
    for (i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--tcp") && i + 1 < argc) port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--width") && i + 1 < argc) width = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--height") && i + 1 < argc) height = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--vital-every") && i + 1 < argc) vital_every = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--pose") && i + 1 < argc) {
            if (!strcmp(argv[++i], "pnp")) pose_pnp = 1;
            else if (strcmp(argv[i], "producer")) { fprintf(stderr, "pose must be pnp|producer\n"); return 2; }
        }
        else { fprintf(stderr, "usage: %s [--tcp PORT] [--vital-every N] [--pose producer|pnp] [--width W] [--height H]\n", argv[0]); return 2; }
    }
    if (width <= 0 || height <= 0 || vital_every < 0) return 2;
    if (port <= 0) return serve(stdin, stdout, vital_every, pose_pnp, width, height);
#ifndef VG_SERVICE_TCP
    fprintf(stderr, "this build has no TCP support\n");
    return 2;
#else
    {
        int listener = socket(AF_INET, SOCK_STREAM, 0), one = 1, client;
        struct sockaddr_in addr;
        FILE *in, *out;
        if (listener < 0) return 2;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port = htons((unsigned short)port);
        if (bind(listener, (struct sockaddr *)&addr, sizeof(addr)) || listen(listener, 1)) {
            perror("bind/listen");
            return 2;
        }
        printf("SERVICE_LISTEN port=%d vital_every=%d pose=%s\n", port, vital_every, pose_pnp ? "pnp" : "producer");
        fflush(stdout);
        client = accept(listener, NULL, NULL);
        if (client < 0) return 2;
        close(listener);
        in = fdopen(dup(client), "r");
        out = fdopen(client, "w");
        if (!in || !out) return 2;
        serve(in, out, vital_every, pose_pnp, width, height);
        fclose(in);
        fclose(out);
    }
    return 0;
#endif
}
