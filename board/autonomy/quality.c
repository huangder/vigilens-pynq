#include "quality.h"
#include <math.h>
#include <stddef.h>

static double clamp01(double x) { return x<0.0 ? 0.0 : (x>1.0 ? 1.0 : x); }
static int config_valid(const vg_quality_config *c) {
    return c && isfinite(c->light_target) && isfinite(c->motion_score_gain) &&
           isfinite(c->weight_light) && isfinite(c->weight_motion) && isfinite(c->weight_face);
}
static double overall(const vg_quality_config *c, double light, double motion, double face) {
    /* Numerical denominator guard from Python, not a configurable threshold. */
    double sum=fmax(1e-9,c->weight_light+c->weight_motion+c->weight_face);
    return clamp01((c->weight_light*light+c->weight_motion*(1.0-motion)+c->weight_face*face)/sum);
}
int vg_quality_from_stats(const vg_quality_config *c, double luma, double ratio,
                          double face, vg_quality_result *out) {
    vg_quality_result r;
    if (!config_valid(c) || !out || !isfinite(luma) || !isfinite(ratio) || !isfinite(face)) return -1;
    r.light_score=clamp01(1.0-fabs(luma-c->light_target)*2.0);
    r.motion_score=clamp01(ratio*c->motion_score_gain);
    r.overall=overall(c,r.light_score,r.motion_score,face);
    r.mean_luma=luma; r.motion_ratio=ratio;
    *out=r; return 0;
}
int vg_quality_apply_motion_ratio(const vg_quality_config *c, double light,
                                  double ratio, const double *face, vg_quality_result *out) {
    vg_quality_result r={0};
    if (!config_valid(c) || !out || !isfinite(light) || !isfinite(ratio) ||
        (face && !isfinite(*face))) return -1;
    r.light_score=clamp01(light);
    r.motion_ratio=clamp01(ratio);
    r.motion_score=clamp01(r.motion_ratio*c->motion_score_gain);
    r.overall=overall(c,r.light_score,r.motion_score,clamp01(face ? *face : 0.0));
    /* This formula does not consume luminance: zero here is not image evidence.
     * Caller preserves its existing mean_luma metadata, as documented in header. */
    *out=r; return 0;
}
int vg_quality_motion_q16(uint32_t q16, int valid, double *out) {
    if (!out || q16>65536u) return -1;
    if (!valid) return -2;
    *out=(double)q16/65536.0; /* HLS binary point placement, not a tuning value. */
    return 0;
}
