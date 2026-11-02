#include "autonomy_engine.h"
#include "face68_3d.h"
#include "pose_pnp.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

struct vg_autonomy {
    vg_measure *measure;
    vg_vital *vital;
    vg_decision *gate;
    vg_vitals held;
    vg_hr_filter hr_filter;
    double raw_hr,raw_hr_conf;
    double pose_model[68][3];
    vg_pose_seed pose_seed;
    int pose_tracking,quality_recovery;
    double vital_max_gap_s;
    double reproj_max_frac;
    double vital_min_rate_hz;
    double vital_rate_hz;
    const char *vital_reject;
    const char *vital_gate;
    int vital_every, pose_pnp;
    unsigned long frames;
    uint64_t last_id;
    double last_ts;
    int started;
};
void vg_autonomy_invalidate(vg_autonomy *e) {
    if (!e) return;
    e->pose_seed.valid=0;
    vg_vital_reset(e->vital);
    vg_hr_filter_reset(&e->hr_filter);e->raw_hr=e->raw_hr_conf=NAN;
    e->vital_gate="invalid_frame";
    memset(&e->held, 0, sizeof(e->held));
}
void vg_autonomy_destroy(vg_autonomy *e) {
    if (!e) return;
    vg_measure_destroy(e->measure);
    vg_vital_destroy(e->vital);
    vg_decision_destroy(e->gate);
    free(e);
}
vg_autonomy *vg_autonomy_create(const vg_measure_config *m,
    const vg_vital_config *v, double reproj, int every, int pnp) {
    vg_autonomy *e;
    if (!m || !v || !isfinite(reproj) || reproj <= 0 || every < 0) return NULL;
    e = calloc(1, sizeof(*e));
    if (!e) return NULL;
    e->measure = vg_measure_create(m);
    e->vital = vg_vital_create(v);
    e->gate = vg_decision_create(&m->decision);
    if (!e->measure || !e->vital || !e->gate) { vg_autonomy_destroy(e); return NULL; }
    e->reproj_max_frac = reproj; e->vital_every = every; e->pose_pnp = pnp;
    e->vital_min_rate_hz = v->fps * (1.0 - v->fs_tol);
    e->vital_max_gap_s=v->max_gap_s;
    memcpy(e->pose_model,VG_FACE68_MODEL,sizeof(e->pose_model));
    return e;
}
int vg_autonomy_configure_temporal(vg_autonomy *e,const double model[68][3],int tracking,int recovery) {
    int i,k;
    if(!e||e->started||(tracking!=0&&tracking!=1)||(recovery!=0&&recovery!=1))return -1;
    if(model) {
        for(i=0;i<68;++i)for(k=0;k<3;++k)if(!isfinite(model[i][k]))return -1;
        memcpy(e->pose_model,model,sizeof(e->pose_model));
    }
    e->pose_tracking=tracking;e->quality_recovery=recovery;e->pose_seed.valid=0;return 0;
}
int vg_autonomy_configure_hr_filter(vg_autonomy *e,const vg_hr_filter_config *cfg) {
    if(!e||e->started)return -1;
    return vg_hr_filter_init(&e->hr_filter,cfg);
}
static void build_input(const vg_autonomy_frame *f, vg_measure_input *i) {
    int k;
    memset(i, 0, sizeof(*i));
    i->frame_id=f->frame_id; i->ts=f->ts; i->mean_luma=f->mean_luma;
    i->motion_q16=f->motion_q16; i->motion_valid=f->motion_valid;
    i->face.visible=f->visible; i->face.yaw=f->pose[0]; i->face.pitch=f->pose[1];
    i->observation.visible=f->visible;
    i->observation.eyes_present=f->visible>0;
    i->observation.left.present=i->observation.right.present=i->observation.mouth.present=f->visible>0;
    for (k=0; k<6; ++k) {
        i->observation.left.p[k].x=f->points[36+k][0];
        i->observation.left.p[k].y=f->points[36+k][1];
        i->observation.right.p[k].x=f->points[42+k][0];
        i->observation.right.p[k].y=f->points[42+k][1];
    }
    i->observation.mouth.left=(vg_point){f->points[48][0],f->points[48][1]};
    i->observation.mouth.right=(vg_point){f->points[54][0],f->points[54][1]};
    i->observation.mouth.top=(vg_point){f->points[51][0],f->points[51][1]};
    i->observation.mouth.bottom=(vg_point){f->points[57][0],f->points[57][1]};
}
int vg_autonomy_update(vg_autonomy *e, const vg_autonomy_frame *f, vg_autonomy_result *out) {
    vg_autonomy_result r={0};
    vg_measure_input input;
    double cadence;
    int k, rc=-1;
    if (!e) return -1;
    if (!f || !out || !isfinite(f->ts) || !isfinite(f->visible) ||
        f->visible<0 || f->visible>1 || f->width<=0 || f->height<=0 ||
        !isfinite(f->mean_luma) || f->mean_luma<0 || f->mean_luma>1) goto reject;
    if (e->started && (f->frame_id<=e->last_id || f->ts<=e->last_ts)) { rc=-3; goto reject; }
    if (!f->motion_valid || f->motion_q16>65536u) { rc=-2; goto reject; }
    if (f->visible>0) {
        if (f->bbox[0]<0 || f->bbox[1]<0 || f->bbox[2]<=0 || f->bbox[3]<=0 ||
            f->bbox[2]>f->width || f->bbox[3]>f->height ||
            f->bbox[0]>f->width-f->bbox[2] || f->bbox[1]>f->height-f->bbox[3]) goto reject;
        for (k=0; k<68; ++k)
            if (!isfinite(f->points[k][0]) || !isfinite(f->points[k][1])) goto reject;
    }
    for (k=0; k<3; ++k)
        if ((double)f->sums[k]>(double)f->count*255.0) goto reject;
    build_input(f, &input);
    memcpy(r.face.bbox,f->bbox,sizeof(r.face.bbox));
    r.face.visible=f->visible;
    if (f->visible>0 && e->pose_pnp) {
        vg_pose pose;
        int pose_rc=e->pose_tracking ? vg_pose_track(f->points,f->width,f->height,e->pose_model,e->reproj_max_frac,
            f->pose_force_cold?NULL:&e->pose_seed,&e->pose_seed,&pose) :
            vg_pose_solve(f->points,f->width,f->height,e->pose_model,e->reproj_max_frac,&pose);
        if (pose_rc) {
            rc=-4; goto reject;
        }
        r.face.yaw=pose.yaw; r.face.pitch=pose.pitch; r.face.roll=pose.roll;
    } else if (f->visible>0) {
        for (k=0; k<3; ++k) if (!isfinite(f->pose[k])) goto reject;
        r.face.yaw=f->pose[0]; r.face.pitch=f->pose[1]; r.face.roll=f->pose[2];
    }
    if(!(f->visible>0))e->pose_seed.valid=0;
    input.face.yaw=r.face.yaw; input.face.pitch=r.face.pitch;
    rc=vg_measure_update(e->measure,&input,&r.measure);
    if (rc) goto reject;
    /* No mixed-quality or mixed-posture windows and no stale held estimate. */
    cadence = (e->started && f->ts > e->last_ts) ? f->ts - e->last_ts : 0.0;
    if(e->hr_filter.cfg.enabled&&e->hr_filter.have_ts&&f->ts-e->hr_filter.last_ts>e->hr_filter.cfg.max_gap_s) {
        vg_hr_filter_reset(&e->hr_filter);memset(&e->held,0,sizeof(e->held));
    }
    if (!f->count || !vg_decision_vitals_allowed(e->gate,r.measure.quality.overall,
            &input.face,r.measure.decision.status)) {
        e->vital_gate=f->visible<=0?"face_missing":(!f->count?"roi_empty":"quality_or_pose");
        vg_hr_filter_reset(&e->hr_filter);e->raw_hr=e->raw_hr_conf=NAN;
        if(e->quality_recovery&&f->visible>0&&f->count) {
            vg_vital_invalidate(e->vital,"vitals_blocked",f->ts,1);
            memset(&e->held,0,sizeof(e->held));
        } else {
            vg_vital_reset(e->vital);memset(&e->held,0,sizeof(e->held));
        }
        e->vital_reject="vitals_blocked";
        e->vital_rate_hz=cadence>0 ? 1.0/cadence : 0.0;
    } else if (e->vital_min_rate_hz>0 && cadence>1.0/e->vital_min_rate_hz &&
               (!e->quality_recovery||cadence>e->vital_max_gap_s)) {
        /* The frozen FIR is designed for fps_nominal; an analysis loop slower
           than fps*(1-fs_tol) can never fill its window. Report that cause and
           skip the push/reset work instead of waiting for a window that cannot
           fill (the board runs this path on every frame). */
        vg_vital_invalidate(e->vital,"sampling_too_slow",f->ts,0);
        memset(&e->held,0,sizeof(e->held));
        e->vital_reject="sampling_too_slow";
        vg_hr_filter_reset(&e->hr_filter);e->raw_hr=e->raw_hr_conf=NAN;
        e->vital_gate="sampling_gap";
        e->vital_rate_hz=1.0/cadence;
    } else {
        e->vital_gate="accepted";
        if (vg_vital_push_rgb(e->vital,f->sums,f->count,f->frame_id,f->ts)) {
            rc=-2; goto reject;
        }
        e->vital_rate_hz=cadence>0 ? 1.0/cadence : 0.0;
        if (e->vital_every>0 && e->frames%(unsigned)e->vital_every==0) {
            vg_vital_result estimate={0};
            e->raw_hr=e->raw_hr_conf=NAN;
            memset(&e->held,0,sizeof(e->held));
            e->vital_reject=NULL;
            if (!vg_vital_estimate(e->vital,&estimate)) {
                if (estimate.present) {
                    e->raw_hr=estimate.hr_bpm;e->raw_hr_conf=estimate.hr_conf;
                    if(vg_hr_filter_update(&e->hr_filter,estimate.hr_bpm,estimate.hr_conf,f->ts)) {
                        e->held.value[0]=e->hr_filter.value;e->held.value[1]=e->hr_filter.conf;
                        e->held.present[0]=e->held.present[1]=1;
                    } else e->vital_reject=e->hr_filter.reason;
                } else {
                    vg_hr_filter_reset(&e->hr_filter);
                    e->vital_reject=estimate.reject;
                }
            }
        }
        vg_decision_gate_vitals(e->gate,r.measure.quality.overall,&e->held,
            &input.face,r.measure.decision.status,&r.vital);
    }
    e->started=1; e->last_id=f->frame_id; e->last_ts=f->ts; ++e->frames;
    *out=r;
    return 0;
reject:
    vg_autonomy_invalidate(e);
    return rc;
}
int vg_autonomy_line(char *b, size_t n, const vg_autonomy_result *r) {
    return r ? vg_contract_line(b,n,&r->measure,&r->face,&r->vital) : -1;
}
int vg_autonomy_write(FILE *s, const vg_autonomy_result *r) {
    return r ? vg_contract_write(s,&r->measure,&r->face,&r->vital) : -1;
}
const char *vg_autonomy_vital_reject(const vg_autonomy *e) {
    return e ? e->vital_reject : NULL;
}
double vg_autonomy_vital_rate_hz(const vg_autonomy *e) {
    return e ? e->vital_rate_hz : 0.0;
}
int vg_autonomy_vital_samples(const vg_autonomy *e) {
    return e ? vg_vital_samples(e->vital) : 0;
}
const char *vg_autonomy_vital_gate(const vg_autonomy *e) {
    return e ? e->vital_gate : NULL;
}
double vg_autonomy_raw_hr(const vg_autonomy *e){return e?e->raw_hr:NAN;}
double vg_autonomy_raw_hr_conf(const vg_autonomy *e){return e?e->raw_hr_conf:NAN;}
const char *vg_autonomy_hr_filter_reason(const vg_autonomy *e){return e?e->hr_filter.reason:NULL;}
