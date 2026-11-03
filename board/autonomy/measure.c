#include "measure.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

struct vg_measure {
    vg_measure_config config;
    vg_behavior *behavior;
    vg_decision *decision;
    uint64_t last_id;
    double last_ts;
    int started;
};
double vg_round_decimal(double value, unsigned digits) {
    char buffer[384];
    int length;
    /* printf rounds the exact binary value to decimal; round(x*10000)/10000
     * introduces an extra binary rounding and diverges from Python at ties.
     * The embedding executable must keep LC_NUMERIC=C and FE_TONEAREST. */
    if (!isfinite(value) || digits > 4) return NAN;
    length=snprintf(buffer, sizeof(buffer), "%.*f", (int)digits, value);
    if (length<0 || (size_t)length>=sizeof(buffer)) return NAN;
    return strtod(buffer, NULL);
}
vg_measure *vg_measure_create(const vg_measure_config *config) {
    vg_measure *e;
    vg_quality_result q;
    if (!config || vg_quality_from_stats(&config->quality, 0, 0, 0, &q)) return NULL;
    e=calloc(1, sizeof(*e));
    if (!e) return NULL;
    e->config=*config;
    e->behavior=vg_behavior_create(&config->behavior);
    e->decision=vg_decision_create(&config->decision);
    if (!e->behavior || !e->decision) { vg_measure_destroy(e); return NULL; }
    return e;
}
void vg_measure_destroy(vg_measure *e) {
    if (!e) return;
    vg_behavior_destroy(e->behavior); vg_decision_destroy(e->decision); free(e);
}
static int point_ok(vg_point p) { return isfinite(p.x) && isfinite(p.y); }
int vg_measure_configure_blink(vg_measure *e, const vg_blink_config *c) {
    return e && !e->started ? vg_behavior_configure_blink(e->behavior,c) : -1;
}
static int observation_ok(const vg_behavior_observation *o) {
    unsigned i;
    if (!isfinite(o->visible)) return 0;
    for (i=0; i<6; ++i)
        if ((o->left.present && !point_ok(o->left.p[i])) ||
            (o->right.present && !point_ok(o->right.p[i]))) return 0;
    return !o->mouth.present || (point_ok(o->mouth.left) && point_ok(o->mouth.right) &&
        point_ok(o->mouth.top) && point_ok(o->mouth.bottom));
}
int vg_measure_update(vg_measure *e, const vg_measure_input *i, vg_measure_result *out) {
    vg_measure_result r={0};
    vg_decision_input d={0};
    double ratio;
    int rc;
    if (!e || !i || !out || !isfinite(i->ts) || !isfinite(i->mean_luma) ||
        !observation_ok(&i->observation) || !isfinite(i->face.visible) ||
        !isfinite(i->face.yaw) || !isfinite(i->face.pitch) ||
        i->face.visible != i->observation.visible) return -1;
    if (!isfinite(vg_ear(&i->observation.left)) ||
        !isfinite(vg_ear(&i->observation.right)) ||
        !isfinite(vg_mar(&i->observation.mouth))) return -1;
    if (e->started && (i->ts<=e->last_ts || i->frame_id<=e->last_id)) return -3;
    if (e->started && !isfinite(i->ts-e->last_ts)) return -1;
    rc=vg_quality_motion_q16(i->motion_q16, i->motion_valid, &ratio);
    if (rc) return rc;
    if (vg_quality_from_stats(&e->config.quality, i->mean_luma, ratio, i->face.visible, &r.quality)) return -1;
    r.quality.light_score=vg_round_decimal(r.quality.light_score, 4);
    r.quality.motion_score=vg_round_decimal(r.quality.motion_score, 4);
    r.quality.overall=vg_round_decimal(r.quality.overall, 4);
    r.quality.mean_luma=vg_round_decimal(r.quality.mean_luma, 4);
    r.quality.motion_ratio=vg_round_decimal(r.quality.motion_ratio, 4);
    /* Reject overflow before the state machine is advanced. */
    if (!isfinite(r.quality.overall) || !isfinite(r.quality.light_score) ||
        !isfinite(r.quality.motion_score) || !isfinite(r.quality.mean_luma)) return -1;
    if (vg_behavior_update(e->behavior, i->ts, &i->observation, &r.behavior)) return -1;
    r.behavior.ear_left=vg_round_decimal(r.behavior.ear_left, 4);
    r.behavior.ear_right=vg_round_decimal(r.behavior.ear_right, 4);
    r.behavior.perclos=vg_round_decimal(r.behavior.perclos, 4);
    r.behavior.mar=vg_round_decimal(r.behavior.mar, 4);
    r.behavior.blink_rate_per_min=vg_round_decimal(r.behavior.blink_rate_per_min, 2);
    d.perclos=r.behavior.perclos; d.blink_rate_per_min=r.behavior.blink_rate_per_min;
    d.yawn_count=r.behavior.yawn_count; d.long_close_count=r.behavior.long_close_count;
    d.overall=r.quality.overall; d.light_score=r.quality.light_score;
    d.motion_score=r.quality.motion_score; d.face=i->face;
    /* Every decision input was validated above or computed from valid geometry. */
    if (vg_decision_decide(e->decision, &d, &r.decision)) return -1;
    r.frame_id=i->frame_id; r.ts=i->ts;
    e->last_id=i->frame_id; e->last_ts=i->ts; e->started=1;
    *out=r; return 0;
}
