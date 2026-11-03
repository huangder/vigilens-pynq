/* Aggregate only observed, valid ROI statistics into a slower physiological
 * time grid. No repeated samples, synthetic timestamps or cross-loss batches. */
#ifndef VG_RGB_SAMPLER_H
#define VG_RGB_SAMPLER_H
#include <math.h>
#include <stdint.h>
#include <string.h>
typedef struct {
    double fps,gap_s,origin,last_ts,time_sum;
    uint64_t bin,last_id,sums[3],pixels,frames;
    int started;
} vg_rgb_sampler;
static inline void vg_rgb_sampler_reset(vg_rgb_sampler *s) {
    double fps=s->fps,gap=s->gap_s;memset(s,0,sizeof(*s));s->fps=fps;s->gap_s=gap;
}
static inline int vg_rgb_sampler_init(vg_rgb_sampler *s,double fps,double gap) {
    if(!s||!isfinite(fps)||fps<=0||!isfinite(gap)||gap<=0)return -1;
    memset(s,0,sizeof(*s));s->fps=fps;s->gap_s=gap;return 0;
}
/* 1 emits real aggregate, 0 buffered, -1 malformed. id is the last contributing
 * source id; ts is the arithmetic mean of its actual contributing timestamps. */
static inline int vg_rgb_sampler_push(vg_rgb_sampler *s,const uint32_t rgb[3],uint32_t count,
    uint64_t id,double ts,uint32_t out[3],uint32_t *out_count,double *out_ts) {
    if(!s||!rgb||!out||!out_count||!out_ts||!count||!isfinite(ts)||ts<0||!(s->fps>0))return -1;
    for(int c=0;c<3;++c)if((uint64_t)rgb[c]>(uint64_t)count*255)return -1;
    if(s->started&&(id<=s->last_id||ts<=s->last_ts))return -1;
    if(s->started&&ts-s->last_ts>s->gap_s)vg_rgb_sampler_reset(s);
    int first=!s->started;
    if(first){s->origin=ts;s->started=1;}
    double position=floor((ts-s->origin)*s->fps+1e-9);
    if(!isfinite(position)||position<0||position>=18446744073709551616.0)return -1;
    uint64_t bin=(uint64_t)position;
    if(s->pixels+count>UINT32_MAX)return -1;
    for(int c=0;c<3;++c)if(s->sums[c]+rgb[c]>UINT32_MAX)return -1;
    for(int c=0;c<3;++c)s->sums[c]+=rgb[c];
    s->pixels+=count;s->time_sum+=ts;++s->frames;s->last_id=id;s->last_ts=ts;
    if(!first&&bin==s->bin)return 0;
    if(s->pixels>UINT32_MAX)return -1;
    for(int c=0;c<3;++c){if(s->sums[c]>UINT32_MAX)return -1;out[c]=(uint32_t)s->sums[c];}
    *out_count=(uint32_t)s->pixels;*out_ts=s->time_sum/(double)s->frames;
    s->bin=bin;s->pixels=s->frames=0;s->time_sum=0;memset(s->sums,0,sizeof(s->sums));return 1;
}
#endif
