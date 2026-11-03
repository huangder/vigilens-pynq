#include "rgb_sampler.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
    vg_rgb_sampler s;uint32_t rgb[]={100,120,80},out[3],count;double ts;int emitted=0;
    assert(!vg_rgb_sampler_init(&s,30,.2));
    for(int i=0;i<=720;++i) {
        int ready=vg_rgb_sampler_push(&s,rgb,1,i,i/60.,out,&count,&ts);assert(ready>=0);
        if(ready){assert(count==(i?2u:1u));assert(out[0]==100*count&&out[1]==120*count&&out[2]==80*count);
            assert(fabs(ts-(i? (i-.5)/60.:0))<1e-12);++emitted;}
    }
    assert(emitted==361);
    assert(vg_rgb_sampler_push(&s,rgb,1,721,12.001,out,&count,&ts)==0);
    vg_rgb_sampler_reset(&s);
    uint32_t changed[]={50,40,30};assert(vg_rgb_sampler_push(&s,changed,1,722,12.02,out,&count,&ts)==1);
    assert(count==1&&out[0]==50&&ts==12.02);
    assert(vg_rgb_sampler_push(&s,rgb,1,723,12.03,out,&count,&ts)==0);
    assert(vg_rgb_sampler_push(&s,changed,1,724,13,out,&count,&ts)==1);
    assert(count==1&&out[0]==50&&ts==13);
    assert(vg_rgb_sampler_push(&s,rgb,1,724,13.1,out,&count,&ts)==-1);
    assert(vg_rgb_sampler_push(&s,rgb,0,725,13.1,out,&count,&ts)==-1);
    assert(vg_rgb_sampler_push(&s,rgb,1,725,NAN,out,&count,&ts)==-1);
    assert(vg_rgb_sampler_init(&s,0,.2)==-1);
    puts("RGB_SAMPLER_PASS input=721 real_aggregates=361 exact_RGB=1 mean_actual_ts=1 loss_resets=1 gap_resets=1 malformed_refused=1");
}
