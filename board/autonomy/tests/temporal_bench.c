/* Synthetic CPU benchmark. No claim about a person's pulse or end-to-end FPS. */
#define _POSIX_C_SOURCE 200809L
#include "pose_pnp.h"
#include "face68_3d.h"
#include "vital.h"
#include "vg_vital_config.h"
#include <math.h>
#include <stdio.h>
#include <time.h>

static double cpu_ms(void) {
    struct timespec t;
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))return -1;
    return t.tv_sec*1000.+t.tv_nsec/1e6;
}
int main(void) {
    double cold_ms=0,warm_ms=0,max_difference=0;
    vg_pose_seed seed={0};
    for(int frame=0;frame<120;++frame) {
        double p[68][2],yaw=.1*sin(frame*.02),c=cos(yaw),s=sin(yaw);
        for(int i=0;i<68;++i) {
            const double *m=VG_FACE68_MODEL[i];
            double x=c*m[0]+s*m[2],z=-s*m[0]+c*m[2]+10.;
            p[i][0]=320.+640*x/z;p[i][1]=240.+640*m[1]/z;
        }
        vg_pose cold,warm;
        double t=cpu_ms();int rc=vg_pose_solve(p,640,480,VG_FACE68_MODEL,.25,&cold);cold_ms+=cpu_ms()-t;
        t=cpu_ms();int rw=vg_pose_track(p,640,480,VG_FACE68_MODEL,.25,&seed,&seed,&warm);warm_ms+=cpu_ms()-t;
        if(rc||rw)return 1;
        double difference=fmax(fabs(cold.yaw-warm.yaw),fmax(fabs(cold.pitch-warm.pitch),fabs(cold.roll-warm.roll)));
        if(difference>max_difference)max_difference=difference;
        if(difference>.05)return 1; /* assertion for this noise-free fixture */
    }
    printf("POSE_TEMPORAL_BENCH frames=120 cold_cpu_ms=%.6f warm_cpu_ms=%.6f max_angle_difference_deg=%.9f synthetic_only=1\n",cold_ms/120,warm_ms/120,max_difference);
    const double rates[]={27.5,28.5,29.95,30.0};
    for(int method=0;method<=1;++method) for(unsigned rate_index=0;rate_index<sizeof(rates)/sizeof(rates[0]);++rate_index) {
        const double rate=rates[rate_index];
        vg_vital_config cfg=vg_exported_vital_config;cfg.method=method;
        vg_vital *v=vg_vital_create(&cfg);if(!v)return 1;
        for(int i=0;i<1200;++i) {
            double pulse=.012*sin(2*3.14159265358979323846*1.15*i/rate);
            uint32_t sums[3]={(uint32_t)lround(122000*(1+pulse)),(uint32_t)lround(118000*(1-pulse)),(uint32_t)lround(112000*(1+.5*pulse))};
            if(vg_vital_push_rgb(v,sums,1000,(uint64_t)i,1+i/rate)){vg_vital_destroy(v);return 1;}
        }
        double ms=0;vg_vital_result result={0};
        for(int n=0;n<20;++n) {
            double t=cpu_ms();int rc=vg_vital_estimate(v,&result);ms+=cpu_ms()-t;
            if(rc||!result.present){vg_vital_destroy(v);return 1;}
        }
        printf("VITAL_CPU_BENCH method=%s source_hz=%.2f iterations=20 estimate_cpu_ms=%.6f synthetic_input_bpm=69 estimated_bpm=%.9f conf=%.9f synthetic_only=1\n",method?"green":"pos",rate,ms/20,result.hr_bpm,result.hr_conf);
        vg_vital_destroy(v);
    }
    return 0;
}
