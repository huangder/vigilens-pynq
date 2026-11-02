#ifndef VG_HR_FILTER_H
#define VG_HR_FILTER_H
#include <math.h>
#include <string.h>
/* Output filter only. A rejected raw estimate MUST call reset immediately.
 * Nine is a storage bound, not a measurement threshold; window is configured. */
typedef struct { int enabled,window; double alpha,interval_s,max_gap_s,max_spread_bpm; int startup_samples; } vg_hr_filter_config;
typedef struct {
    vg_hr_filter_config cfg;
    double values[9],confs[9],last_ts,last_seen,ema,value,conf;
    int count,have_ts,have_seen,have_ema,present;
    const char *reason;
} vg_hr_filter;
static inline void vg_hr_filter_reset(vg_hr_filter *f) {
    vg_hr_filter_config cfg=f->cfg;memset(f,0,sizeof(*f));f->cfg=cfg;f->reason="warming";
}
static inline int vg_hr_filter_init(vg_hr_filter *f,const vg_hr_filter_config *c) {
    if(!f||!c||(c->enabled!=0&&c->enabled!=1))return -1;
    if(c->enabled&&(c->window<3||c->window>9||c->window%2!=1||
        !isfinite(c->alpha)||!(c->alpha>0&&c->alpha<=1)||!isfinite(c->interval_s)||c->interval_s<=0||
        !isfinite(c->max_gap_s)||c->max_gap_s<c->interval_s||!isfinite(c->max_spread_bpm)||c->max_spread_bpm<=0||
        c->startup_samples<0||c->startup_samples>c->window))return -1;
    memset(f,0,sizeof(*f));f->cfg=*c;f->reason="warming";return 0;
}
static inline int vg_hr_filter_update(vg_hr_filter *f,double bpm,double conf,double ts) {
    double sorted[9],minimum=conf;int i,j;
    if(!f->cfg.enabled){f->value=bpm;f->conf=conf;f->present=1;f->reason="disabled";return 1;}
    if(!isfinite(bpm)||!isfinite(conf)||!isfinite(ts)||bpm<=0||conf<0||conf>1) {
        vg_hr_filter_reset(f);f->reason="invalid_input";return 0;
    }
    if(f->have_seen&&ts<f->last_seen){vg_hr_filter_reset(f);f->reason="invalid_time";return 0;}
    f->last_seen=ts;f->have_seen=1;
    if(f->have_ts&&ts-f->last_ts>f->cfg.max_gap_s){vg_hr_filter_reset(f);f->last_seen=ts;f->have_seen=1;}
    if(f->have_ts&&ts-f->last_ts<f->cfg.interval_s){if(f->present&&conf<f->conf)f->conf=conf;return f->present;}
    f->last_ts=ts;f->have_ts=1;
    if(f->count==f->cfg.window) {
        memmove(f->values,f->values+1,(size_t)(f->count-1)*sizeof(double));
        memmove(f->confs,f->confs+1,(size_t)(f->count-1)*sizeof(double));--f->count;
    }
    f->values[f->count]=bpm;f->confs[f->count++]=conf;
    if(f->count<(f->cfg.startup_samples?f->cfg.startup_samples:f->cfg.window)){f->reason="warming";f->present=0;return 0;}
    for(i=0;i<f->count;++i){sorted[i]=f->values[i];if(f->confs[i]<minimum)minimum=f->confs[i];}
    for(i=1;i<f->count;++i){double v=sorted[i];j=i;while(j>0&&sorted[j-1]>v){sorted[j]=sorted[j-1];--j;}sorted[j]=v;}
    if(sorted[f->count-1]-sorted[0]>f->cfg.max_spread_bpm){f->have_ema=f->present=0;f->reason="unstable";return 0;}
    {double median=f->count%2?sorted[f->count/2]:(sorted[f->count/2-1]+sorted[f->count/2])/2;
     if(!f->have_ema){f->ema=median;f->have_ema=1;}
     else f->ema+=f->cfg.alpha*conf*(median-f->ema);}
    f->value=floor(f->ema*10+.5)/10;f->conf=minimum;f->present=1;f->reason="accepted";return 1;
}
#endif
