/* Fake runtime tests queue ownership and file lifecycle, NOT vision accuracy.
 * gcc -std=c11 -Wall -Wextra -Werror -pthread -I board/autonomy \
 *   board/autonomy/autonomy_camera.c board/autonomy/tests/autonomy_camera_test.c \
 *   -o /tmp/vg_camera_test && /tmp/vg_camera_test
 */
#define _POSIX_C_SOURCE 200809L
#include "autonomy_camera.h"
#include "vision_runtime.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line=%d %s\n",__LINE__,#x); abort(); } } while(0)
struct vg_vision { pthread_t owner; };
static struct vg_vision fake;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond = PTHREAD_COND_INITIALIZER;
static int hold, entered, fail_create, calls;
static int released[3];
static const unsigned char *leased_rgb[2];
static void release_test(void *context) { ++released[(size_t)context]; }
vg_vision *vg_vision_create(const char *directory, const char *model) {
    CHECK(!strcmp(directory,"det") && !strcmp(model,"model"));
    fake.owner = pthread_self();
    return fail_create ? NULL : &fake;
}
void vg_vision_destroy(vg_vision *v) { if(v) CHECK(pthread_equal(v->owner,pthread_self())); }
int vg_vision_update(vg_vision *v, const vg_vision_frame *f,
                     char *json, size_t n, vg_vision_info *info) {
    int i;
    CHECK(pthread_equal(v->owner,pthread_self()));
    pthread_mutex_lock(&mutex);
    ++calls; entered=1; pthread_cond_broadcast(&cond);
    while(hold) pthread_cond_wait(&cond,&mutex);
    pthread_mutex_unlock(&mutex);
    /* Read after the caller has reused its own buffer and filled the other slot. */
    CHECK(f->width==2 && f->height==2 && f->ts==f->frame_id/10.0);
    CHECK(f->mean_luma==f->frame_id/100.0 && f->motion_q16==f->frame_id*2);
    for(i=0;i<12;++i) CHECK(f->rgb[i]==f->frame_id);
    if(f->frame_id==9||f->frame_id==10)CHECK(f->rgb==leased_rgb[f->frame_id-9]);
    memset(info,0,sizeof(*info));
    if(f->frame_id==5) return -10;
    if(f->frame_id==6) { CHECK(!f->motion_valid); return -2; }
    if(f->frame_id==7) return -4;
    snprintf(json,n,"{\"frame_id\":%lu}",(unsigned long)f->frame_id);
    return 0;
}
static int submit(vg_camera *c,unsigned id) {
    unsigned char rgb[12]; int rc;
    memset(rgb,(int)id,sizeof(rgb));
    rc=vg_camera_submit(c,rgb,2,2,id/100.0,id*2,id!=6,id,id/10.0);
    memset(rgb,255,sizeof(rgb)); /* no borrowed RGB can survive this point */
    return rc;
}
static void seed(const char *path) {
    FILE *f=fopen(path,"w"); CHECK(f); CHECK(fputs("OLD_STATE",f)>=0); CHECK(!fclose(f));
}
int main(void) {
    char dir[]="/tmp/vg_camera_XXXXXX",log[256],latest[256],bad[256],line[256];
    vg_camera *c; vg_camera_stats s; FILE *f;
    CHECK(mkdtemp(dir));
    snprintf(log,sizeof(log),"%s/frames.jsonl",dir);
    snprintf(latest,sizeof(latest),"%s/latest.json",dir);
    snprintf(bad,sizeof(bad),"%s/missing/latest.json",dir);
    seed(latest); hold=1;
    c=vg_camera_open("det","model",log,latest); CHECK(c); CHECK(access(latest,F_OK));
    CHECK(submit(c,1)==1);
    pthread_mutex_lock(&mutex);
    while(!entered) pthread_cond_wait(&cond,&mutex);
    pthread_mutex_unlock(&mutex);
    CHECK(submit(c,2)==1); CHECK(submit(c,3)==0);
    pthread_mutex_lock(&mutex); hold=0; pthread_cond_broadcast(&cond); pthread_mutex_unlock(&mutex);
    CHECK(!vg_camera_finish(c,&s));
    CHECK(s.submitted==3 && s.accepted==2 && s.skipped==1 && s.processed==2 && s.succeeded==2);
    CHECK(s.failed==0 && s.rejected==0 && calls==2);
    CHECK(submit(c,4)<0); CHECK(!vg_camera_finish(c,&s));
    f=fopen(log,"r"); CHECK(f);
    CHECK(fgets(line,sizeof(line),f) && strstr(line,":1}"));
    CHECK(fgets(line,sizeof(line),f) && strstr(line,":2}"));
    CHECK(!fgets(line,sizeof(line),f)); fclose(f);
    f=fopen(latest,"r"); CHECK(f); CHECK(fgets(line,sizeof(line),f) && strstr(line,":2}")); fclose(f);
    vg_camera_destroy(c);
    puts("PASS owned slots / same-frame metadata / full skip / FIFO drain / repeated finish");

    c=vg_camera_open("det","model",log,latest); CHECK(c);
    CHECK(submit(c,4)==1); CHECK(submit(c,5)==1);
    CHECK(vg_camera_finish(c,&s)==-10);
    CHECK(s.succeeded==1 && s.failed==1 && s.rejected==0 && access(latest,F_OK));
    vg_camera_destroy(c);
    puts("PASS runtime failure removes prior successful latest and returns error");

    seed(latest); c=vg_camera_open("det","model",log,latest); CHECK(c);
    CHECK(submit(c,6)==1); CHECK(submit(c,7)==1);
    CHECK(!vg_camera_finish(c,&s));
    CHECK(s.processed==2 && s.rejected==2 && s.failed==0 && access(latest,F_OK));
    vg_camera_destroy(c);
    puts("PASS invalid motion / PnP rejection remove latest without hard fault");

    c=vg_camera_open("det","model",log,bad); CHECK(c); CHECK(submit(c,8)==1);
    CHECK(vg_camera_finish(c,&s)<0 && s.failed==1 && s.succeeded==0);
    vg_camera_destroy(c);
    puts("PASS publish IO failure surfaces and drains cleanly");

    {
        unsigned char rgb[3][12];
        entered=0;hold=1;
        for(int i=0;i<3;++i)memset(rgb[i],9+i,sizeof(rgb[i]));
        leased_rgb[0]=rgb[0];leased_rgb[1]=rgb[1];
        c=vg_camera_open("det","model",log,latest);CHECK(c);
        CHECK(vg_camera_submit_leased(c,rgb[0],2,2,NULL,0,0,.09,18,1,9,.9,release_test,(void *)0)==1);
        pthread_mutex_lock(&mutex);while(!entered)pthread_cond_wait(&cond,&mutex);pthread_mutex_unlock(&mutex);
        CHECK(vg_camera_submit_leased(c,rgb[1],2,2,NULL,0,0,.10,20,1,10,1.,release_test,(void *)1)==1);
        CHECK(vg_camera_submit_leased(c,rgb[2],2,2,NULL,0,0,.11,22,1,11,1.1,release_test,(void *)2)==0);
        CHECK(!released[0]&&!released[1]&&!released[2]);
        pthread_mutex_lock(&mutex);hold=0;pthread_cond_broadcast(&cond);pthread_mutex_unlock(&mutex);
        CHECK(!vg_camera_finish(c,&s));
        CHECK(released[0]==1&&released[1]==1&&released[2]==0);
        vg_camera_destroy(c);
        puts("PASS zero-copy leases preserve pointer identity, release once after use, no release on skip");
    }
    seed(latest); fail_create=1;
    CHECK(!vg_camera_open("det","model",log,latest)); CHECK(access(latest,F_OK));
    puts("PASS model initialization failure clears old latest");
    unlink(log); unlink(latest); CHECK(!rmdir(dir));
    puts("AUTONOMY_CAMERA_TEST_PASS (fake vision; no accuracy/board FPS claim)");
    return 0;
}
