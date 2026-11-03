/* Synthetic four-slot lease/FIFO regression. No camera or vision accuracy claim. */
#define _POSIX_C_SOURCE 200809L
#include "autonomy_camera.h"
#include "vision_runtime.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"FAIL line=%d %s\n",__LINE__,#x);abort(); } } while(0)
struct vg_vision { pthread_t owner; };
static struct vg_vision vision;
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond=PTHREAD_COND_INITIALIZER;
static int hold,entered,calls,released[5];
static unsigned char pixels[5][12];
static void release(void *context) { ++released[(size_t)context]; }
vg_vision *vg_vision_create(const char *directory,const char *model) {
    CHECK(!strcmp(directory,"det")&&!strcmp(model,"model"));
    vision.owner=pthread_self();return &vision;
}
void vg_vision_destroy(vg_vision *v) { CHECK(v==&vision&&pthread_equal(v->owner,pthread_self())); }
int vg_vision_update(vg_vision *v,const vg_vision_frame *f,char *json,size_t n,vg_vision_info *info) {
    CHECK(pthread_equal(v->owner,pthread_self()));
    pthread_mutex_lock(&mutex);
    entered=1;pthread_cond_broadcast(&cond);
    while(hold)pthread_cond_wait(&cond,&mutex);
    pthread_mutex_unlock(&mutex);
    CHECK(f->frame_id==(unsigned)++calls&&f->frame_id<=4);
    CHECK(f->ts==f->frame_id/10.&&f->width==2&&f->height==2);
    CHECK(f->rgb==pixels[f->frame_id-1]);
    for(int i=0;i<12;++i)CHECK(f->rgb[i]==f->frame_id);
    memset(info,0,sizeof(*info));snprintf(json,n,"{\"frame_id\":%u}",(unsigned)f->frame_id);return 0;
}
int main(void) {
    char dir[]="/tmp/vg_buffered_XXXXXX",log[256],latest[256],line[256];
    CHECK(mkdtemp(dir));snprintf(log,sizeof(log),"%s/log",dir);snprintf(latest,sizeof(latest),"%s/latest",dir);
    for(int round=0;round<20;++round) {
        hold=1;entered=calls=0;memset(released,0,sizeof(released));
        for(int i=0;i<5;++i)memset(pixels[i],i+1,sizeof(pixels[i]));
        vg_camera *c=vg_camera_open("det","model",log,latest);CHECK(c);
        for(int i=0;i<5;++i) {
            int accepted=vg_camera_submit_leased(c,pixels[i],2,2,NULL,0,0,.5,0,1,i+1,(i+1)/10.,release,(void *)(size_t)i);
            CHECK(accepted==(i<4));
            if(!i) { pthread_mutex_lock(&mutex);while(!entered)pthread_cond_wait(&cond,&mutex);pthread_mutex_unlock(&mutex); }
        }
        for(int i=0;i<5;++i)CHECK(!released[i]);
        pthread_mutex_lock(&mutex);hold=0;pthread_cond_broadcast(&cond);pthread_mutex_unlock(&mutex);
        vg_camera_stats stats;CHECK(!vg_camera_finish(c,&stats));
        CHECK(stats.submitted==5&&stats.accepted==4&&stats.skipped==1&&stats.processed==4&&stats.succeeded==4);
        CHECK(!stats.failed&&!stats.rejected&&calls==4);
        for(int i=0;i<5;++i)CHECK(released[i]==(i<4));
        FILE *f=fopen(log,"r");CHECK(f);
        for(int i=1;i<=4;++i) { char expected[64];snprintf(expected,sizeof(expected),"{\"frame_id\":%d}\n",i);CHECK(fgets(line,sizeof(line),f)&&!strcmp(line,expected)); }
        CHECK(!fgets(line,sizeof(line),f));CHECK(!fclose(f));vg_camera_destroy(c);
    }
    unlink(log);unlink(latest);CHECK(!rmdir(dir));
    puts("BUFFERED_CAMERA_TEST_PASS rounds=20 slots=4 FIFO=80 exactly_once_release=80 full_skips=20");return 0;
}
