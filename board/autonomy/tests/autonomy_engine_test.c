/* Native integration regression. Generated geometry/RGB is a test stimulus,
 * never evidence of physiological accuracy or performance on the board. */
#include "autonomy_engine.h"
#include "vg_config.h"
#include "vg_vital_config.h"
#include "face68_3d.h"
#include <fenv.h>
#include <locale.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line=%d: %s\n",__LINE__,#x); exit(1); } } while (0)

static vg_autonomy_frame frame(uint64_t id) {
    vg_autonomy_frame f={0};
    int k;
    double t=(double)id/vg_exported_vital_config.fps;
    double pulse=0.012*sin(2*acos(-1.0)*1.15*t), drift=0.004*sin(2*acos(-1.0)*0.05*t);
    f.frame_id=id; f.ts=t; f.width=640; f.height=480;
    f.visible=1; f.bbox[0]=100; f.bbox[1]=80; f.bbox[2]=300; f.bbox[3]=320;
    f.mean_luma=vg_exported_config.quality.light_target; f.motion_valid=1;
    f.count=1000;
    f.sums[0]=(uint32_t)lround(122*(1+pulse+0.3*drift)*f.count);
    f.sums[1]=(uint32_t)lround(118*(1-pulse-0.2*drift)*f.count);
    f.sums[2]=(uint32_t)lround(112*(1+0.5*pulse)*f.count);
    for (k=0;k<68;++k) {
        f.points[k][0]=320+640*VG_FACE68_MODEL[k][0]/(10+VG_FACE68_MODEL[k][2]);
        f.points[k][1]=240+640*VG_FACE68_MODEL[k][1]/(10+VG_FACE68_MODEL[k][2]);
    }
    return f;
}
static vg_autonomy *create(int pnp) {
    vg_autonomy *e=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,
        VG_POSE_REPROJ_MAX_FRAC,30,pnp);
    CHECK(e); return e;
}
static void pose_and_dimensions(void) {
    vg_autonomy *e=create(1);
    vg_autonomy_frame f=frame(1);
    vg_autonomy_result r, sentinel;
    int k;
    CHECK(!vg_autonomy_update(e,&f,&r));
    CHECK(fabs(r.face.yaw)<0.05 && fabs(r.face.pitch)<0.05);
    CHECK(vg_autonomy_vital_samples(e)==1);
    CHECK(!strcmp(vg_autonomy_vital_gate(e),"accepted"));
    f=frame(2); f.width*=2; f.height*=2;
    for (k=0;k<68;++k) { f.points[k][0]*=2; f.points[k][1]*=2; }
    CHECK(!vg_autonomy_update(e,&f,&r));
    CHECK(fabs(r.face.yaw)<0.05 && fabs(r.face.pitch)<0.05);
    f=frame(3); memset(f.points,0,sizeof(f.points));
    memset(&r,0x5a,sizeof(r)); sentinel=r;
    CHECK(vg_autonomy_update(e,&f,&r)==-4);
    CHECK(vg_autonomy_vital_samples(e)==0);
    CHECK(!strcmp(vg_autonomy_vital_gate(e),"invalid_frame"));
    CHECK(!memcmp(&r,&sentinel,sizeof(r)));
    vg_autonomy_destroy(e);
    puts("PASS pose failure rejected; image dimensions consumed");
}
static void loss_and_rejection(void) {
    int scenario, warm=(int)lround(vg_exported_vital_config.window_seconds*vg_exported_vital_config.fps)+180;
    for (scenario=0;scenario<11;++scenario) {
        vg_autonomy *e=create(scenario==8);
        vg_autonomy_result r, sentinel;
        vg_autonomy_frame f;
        int i, had=0, rc;
        for (i=1;i<=warm;++i) { f=frame((uint64_t)i); CHECK(!vg_autonomy_update(e,&f,&r)); had|=r.vital.present[0]; }
        CHECK(had && r.vital.present[0]); /* prove stale-output checks start with real held estimate */
        f=frame((uint64_t)i);
        switch(scenario) {
        case 0: f.visible=0; break;
        case 1: f.count=0; memset(f.sums,0,sizeof(f.sums)); break;
        case 2: f.motion_valid=0; break;
        case 3: --f.frame_id; break;
        case 4: f.ts=r.measure.ts; break;
        case 5: f.mean_luma=NAN; break;
        case 6: f.ts+=vg_exported_vital_config.max_gap_s*2; break;
        case 7: f.pose[0]=vg_exported_config.decision.pose_yaw_max_deg+1; break;
        case 8: memset(f.points,0,sizeof(f.points)); break;
        case 9: vg_autonomy_invalidate(e); break;
        case 10: f.motion_q16=65536u; break;
        }
        memset(&r,0x5a,sizeof(r)); sentinel=r;
        rc=vg_autonomy_update(e,&f,&r);
        if ((scenario>=2 && scenario<=5) || scenario==8) {
            CHECK(rc==(scenario==2 ? -2 : scenario==5 ? -1 : scenario==8 ? -4 : -3));
            CHECK(!memcmp(&r,&sentinel,sizeof(r)));
        } else { CHECK(!rc); CHECK(!r.vital.present[0]); }
        /* Reacquisition between cadence ticks cannot resurrect the old estimate. */
        for (i=warm+2;i<warm+20;++i) {
            vg_autonomy_frame next=frame((uint64_t)i);
            if (scenario==6) next.ts+=vg_exported_vital_config.max_gap_s*2;
            CHECK(!vg_autonomy_update(e,&next,&r)); CHECK(!r.vital.present[0]);
        }
        vg_autonomy_destroy(e);
    }
    puts("PASS held HR cleared on face/ROI/motion/id/time/malformed/gap/posture/PnP/loss/quality refusal");
}
static void vital_cadence(void) {
    vg_autonomy *e=create(0);
    vg_autonomy_result r;
    int i;
    const char *reject;
    /* 600 ms between analysed frames: the FIR design rate cannot be fed. */
    for (i=1;i<=8;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        f.ts=0.6*(double)i;
        CHECK(!vg_autonomy_update(e,&f,&r));
        CHECK(!r.vital.present[0]);
        if (i==1) continue; /* the first frame has no cadence to measure yet */
        reject=vg_autonomy_vital_reject(e);
        CHECK(reject && !strcmp(reject,"sampling_too_slow"));
        CHECK(fabs(vg_autonomy_vital_rate_hz(e)-1.0/0.6)<1e-3);
    }
    /* A 30 Hz feed resumes normal accumulation and stops claiming slow rate. */
    for (i=1;i<=40;++i) {
        vg_autonomy_frame f=frame((uint64_t)(8+i));
        f.ts=4.8+(double)i/30.0;
        CHECK(!vg_autonomy_update(e,&f,&r));
    }
    CHECK(fabs(vg_autonomy_vital_rate_hz(e)-30.0)<0.5);
    reject=vg_autonomy_vital_reject(e);
    CHECK(reject && !strcmp(reject,"window_not_full"));
    vg_autonomy_destroy(e);
    puts("PASS slow analysis cadence names sampling_too_slow and recovers on a fast feed");
}
static void source_jitter_and_short_recovery(void) {
    vg_autonomy *e=create(0);vg_autonomy_result r;int i,had=0,resumed=0;
    CHECK(!vg_autonomy_configure_temporal(e,NULL,0,1));
    /* Actual monotonic source times occasionally exceed 1/27 s, but remain
     * inside the configured interpolation gap/jitter and window-rate guards. */
    for(i=1;i<=1200;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);f.ts=i/30.0+.005*sin(i*1.5);
        CHECK(!vg_autonomy_update(e,&f,&r));had|=r.vital.present[0];
    }
    CHECK(had);CHECK(r.vital.present[0]);
    {vg_autonomy_frame f=frame(1201);f.ts=1201/30.0+.005*sin(1201*1.5);f.motion_q16=65536;
     CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);}
    for(i=1202;i<=1260;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);f.ts=i/30.0+.005*sin(i*1.5);
        CHECK(!vg_autonomy_update(e,&f,&r));
        if(i<1204)CHECK(!r.vital.present[0]);
        resumed|=r.vital.present[0];
    }
    CHECK(resumed);
    {vg_autonomy_frame f=frame(1261);f.visible=0;f.count=0;memset(f.sums,0,sizeof(f.sums));
     CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);}
    for(i=1262;i<1320;++i){vg_autonomy_frame f=frame((uint64_t)i);CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);}
    vg_autonomy_destroy(e);
    puts("PASS source jitter uses window-rate gate; short quality pause recomputes HR; face loss clears history");
}
static void observable_metrics(void) {
    vg_autonomy *e=create(0);vg_autonomy_result r;int blink=0,long_close=0,yawn=0,hr=0,perclos=0;
    CHECK(!vg_autonomy_configure_temporal(e,NULL,0,1));
    for(int i=1;i<=1200;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        if((i>=10&&i<=18)||(i>=40&&i<=70)) {
            for(int eye=36;eye<=42;eye+=6) {
                double left=f.points[eye][0],right=f.points[eye+3][0],middle=(f.points[eye][1]+f.points[eye+3][1])/2;
                double half_height=(right-left)*vg_exported_config.behavior.ear_close_threshold/4;
                f.points[eye+1][1]=f.points[eye+2][1]=middle-half_height;
                f.points[eye+4][1]=f.points[eye+5][1]=middle+half_height;
            }
        }
        if(i>=100&&i<=150) {
            double middle=(f.points[48][1]+f.points[54][1])/2;
            double height=(f.points[54][0]-f.points[48][0])*(vg_exported_config.behavior.mar_threshold+.2);
            f.points[51][1]=middle-height/2;f.points[57][1]=middle+height/2;
        }
        CHECK(!vg_autonomy_update(e,&f,&r));
        blink|=r.measure.behavior.blink_count>0;long_close|=r.measure.behavior.long_close_count>0;
        yawn|=r.measure.behavior.yawn_count>0;hr|=r.vital.present[0];perclos|=r.measure.behavior.perclos>0;
        CHECK(isfinite(r.measure.behavior.ear_left)&&isfinite(r.measure.behavior.mar));
        CHECK(r.measure.quality.overall>0);
    }
    CHECK(blink&&long_close&&yawn&&hr&&perclos);
    vg_autonomy_destroy(e);
    puts("PASS synthetic metric stimuli produce blink/long-close/PERCLOS/yawn/HR and quality; physiological_accuracy_verified=0");
}
static void hr_filter_integration(void) {
    vg_autonomy *e=create(0);vg_autonomy_result r;int had=0,raw_first=0,filtered_first=0;
    vg_hr_filter_config strict=vg_exported_hr_filter_config;strict.startup_samples=strict.window;
    CHECK(!vg_autonomy_configure_hr_filter(e,&strict));
    for(int i=1;i<=900;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        CHECK(!vg_autonomy_update(e,&f,&r));
        if(!raw_first&&isfinite(vg_autonomy_raw_hr(e)))raw_first=i;
        if(r.vital.present[0]){had=1;if(!filtered_first)filtered_first=i;}
    }
    CHECK(had);CHECK(filtered_first>raw_first);
    {vg_autonomy_frame f=frame(901);f.visible=0;f.count=0;memset(f.sums,0,sizeof(f.sums));
     CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);CHECK(!isfinite(vg_autonomy_raw_hr(e)));}
    for(int i=902;i<1000;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);
    }
    vg_autonomy_destroy(e);puts("PASS filtered HR confirms new estimates and face loss clears raw/filtered output");
    e=create(0);CHECK(!vg_autonomy_configure_hr_filter(e,&vg_exported_hr_filter_config));had=0;
    for(int i=1;i<400;++i){vg_autonomy_frame f=frame((uint64_t)i);CHECK(!vg_autonomy_update(e,&f,&r));
        if(isfinite(vg_autonomy_raw_hr(e))){CHECK(r.vital.present[0]);had=1;break;}}
    CHECK(had);vg_autonomy_destroy(e);puts("PASS progressive filter starts on the first valid estimate");
}
static void capture60_vital30(void) {
    vg_autonomy *e=create(0);vg_autonomy_result r;int had=0;
    CHECK(!vg_autonomy_configure_capture_rate(e,60));
    for(int i=1;i<=1800;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);f.ts=i/60.;
        CHECK(!vg_autonomy_update(e,&f,&r));had|=r.vital.present[0];
        CHECK(vg_autonomy_vital_samples(e)<=i/2+1);
    }
    CHECK(had);CHECK(vg_autonomy_configure_capture_rate(e,60)==-1);
    vg_autonomy_frame f=frame(1801);f.ts=1801/60.;f.visible=0;f.count=0;memset(f.sums,0,sizeof(f.sums));
    CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);CHECK(!vg_autonomy_vital_samples(e));
    f=frame(1802);f.ts=1802/60.;CHECK(!vg_autonomy_update(e,&f,&r));CHECK(vg_autonomy_vital_samples(e)==1);
    vg_autonomy_destroy(e);puts("PASS 60Hz observations aggregate real RGB into 30Hz FIR; HR produced; face loss clears aggregates/history");
}
static void rolling_filter_recovery(void) {
    vg_autonomy *e=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,
        VG_POSE_REPROJ_MAX_FRAC,1,0);
    vg_hr_filter_config strict=vg_exported_hr_filter_config;
    vg_autonomy_result r;
    int resumed=0,seen_fresh_raw=0;
    CHECK(e);strict.startup_samples=strict.window;
    CHECK(!vg_autonomy_configure_temporal(e,NULL,0,1));
    CHECK(!vg_autonomy_configure_hr_filter(e,&strict));
    for(int i=1;i<=900;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        CHECK(!vg_autonomy_update(e,&f,&r));
    }
    CHECK(r.vital.present[0]);
    CHECK(vg_autonomy_vital_history_samples(e)>vg_autonomy_vital_samples(e));
    double before=r.vital.value[0];
    {vg_autonomy_frame f=frame(901);f.motion_q16=65536;
     CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);
     CHECK(!strcmp(vg_autonomy_hr_filter_reason(e),"paused"));
     CHECK(vg_autonomy_vital_samples(e)>0);}
    int end=902+(int)ceil(vg_exported_vital_config.fps);
    for(int i=902;i<end;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        CHECK(!vg_autonomy_update(e,&f,&r));
        if(i<901+vg_exported_vital_config.recovery_samples)CHECK(!r.vital.present[0]);
        seen_fresh_raw|=isfinite(vg_autonomy_raw_hr(e));
        if(r.vital.present[0]) {
            CHECK(isfinite(vg_autonomy_raw_hr(e)));
            CHECK(fabs(r.vital.value[0]-before)<=strict.max_spread_bpm);
            resumed=1;break;
        }
    }
    /* Strict startup would need three new spaced estimates if pause reset it.
     * Recovery within one second proves prior valid votes were retained. */
    CHECK(seen_fresh_raw&&resumed);
    {vg_autonomy_frame f=frame((uint64_t)(end+1));f.visible=0;f.count=0;memset(f.sums,0,sizeof(f.sums));
     CHECK(!vg_autonomy_update(e,&f,&r));CHECK(!r.vital.present[0]);
     CHECK(!vg_autonomy_vital_samples(e));
     CHECK(!strcmp(vg_autonomy_hr_filter_reason(e),"warming"));}
    vg_autonomy_destroy(e);
    puts("ROLLING_RECOVERY_PASS fresh_reestimate=1 bounded_filter_resume=1 hard_loss_clears=1 synthetic=1");
}
static void report_floor_integration(void) {
    vg_autonomy *e=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,
        VG_POSE_REPROJ_MAX_FRAC,1,0);
    vg_autonomy_result r;
    CHECK(e);CHECK(!vg_autonomy_configure_temporal(e,NULL,0,1));
    CHECK(!vg_autonomy_configure_hr_filter(e,&vg_exported_hr_filter_config));
    for(int i=1;i<=900;++i) {
        vg_autonomy_frame f=frame((uint64_t)i);
        /* Deliberate 0.8Hz/48BPM stimulus below the configured report floor. */
        double pulse=.012*sin(2*acos(-1.0)*.8*f.ts);
        f.sums[0]=(uint32_t)lround(122000*(1+pulse));
        f.sums[1]=(uint32_t)lround(118000*(1-pulse));
        f.sums[2]=(uint32_t)lround(112000*(1+.5*pulse));
        CHECK(!vg_autonomy_update(e,&f,&r));
    }
    CHECK(isfinite(vg_autonomy_raw_hr(e)));
    CHECK(vg_autonomy_raw_hr(e)<vg_exported_hr_filter_config.min_bpm);
    CHECK(!r.vital.present[0]&&!r.vital.present[1]);
    CHECK(!strcmp(vg_autonomy_vital_reject(e),"below_report_min"));
    CHECK(!strcmp(vg_autonomy_hr_filter_reason(e),"below_report_min"));
    vg_autonomy_destroy(e);
    puts("REPORT_FLOOR_PASS low_raw_retained=1 current_output_absent=1 alternate_peak_selected=0 synthetic=1");
}
int main(int argc, char **argv) {
    CHECK(setlocale(LC_NUMERIC,"C")); CHECK(!fesetround(FE_TONEAREST));
    if(argc==2&&!strcmp(argv[1],"--rolling-recovery")) {rolling_filter_recovery();return 0;}
    if(argc==2&&!strcmp(argv[1],"--report-floor")) {report_floor_integration();return 0;}
    if(argc==2&&!strcmp(argv[1],"--full60")) {
        CHECK(vg_exported_vital_config.fps==60);
        CHECK(lround(vg_exported_vital_config.fps*vg_exported_vital_config.window_seconds)==75);
        vg_autonomy *e=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,VG_POSE_REPROJ_MAX_FRAC,1,0);
        CHECK(e);vg_autonomy_result r;int had=0;
        CHECK(!vg_autonomy_configure_capture_rate(e,60));
        for(int i=1;i<=2400;++i) {vg_autonomy_frame f=frame((uint64_t)i);CHECK(!vg_autonomy_update(e,&f,&r));had|=r.vital.present[0];}
        CHECK(had);CHECK(vg_autonomy_vital_samples(e)==75);CHECK(vg_autonomy_vital_history_samples(e)>75);
        vg_autonomy_destroy(e);puts("FULL60_ENGINE_PASS capture=60 vital=60 real_minimum=75 overlapping_history_grows=1 synthetic_hr_present=1");return 0;
    }
    if (argc==2 && !strcmp(argv[1],"--fixture")) {
        int i,k;
        for (i=1;i<=1200;++i) {
            vg_autonomy_frame f=frame((uint64_t)i);
            printf("%llu %.17g %.17g",(unsigned long long)f.frame_id,f.ts,f.visible);
            for (k=0;k<4;++k) printf(" %d",f.bbox[k]);
            printf(" 0 0 0 %.17g %u %d %u %u %u %u",f.mean_luma,
                f.motion_q16,f.motion_valid,f.count,f.sums[0],f.sums[1],f.sums[2]);
            for (k=0;k<68;++k) printf(" %.17g %.17g",f.points[k][0],f.points[k][1]);
            putchar('\n');
        }
        return 0;
    }
    pose_and_dimensions(); loss_and_rejection(); vital_cadence();source_jitter_and_short_recovery();observable_metrics();hr_filter_integration();capture60_vital30();
    puts("PASS autonomy native integration"); return 0;
}
