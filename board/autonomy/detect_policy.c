#include "detect_policy.h"
#include <math.h>

static int valid(const vg_detect_state *state, const vg_detect_policy *policy, double ts) {
    if (!state || !policy) return 0;
    if (!isfinite(ts) || !isfinite(state->last_deep_ts) || !isfinite(state->last_face_ts))
        return 0;
    if (!isfinite(policy->deep_seconds) || policy->deep_seconds <= 0) return 0;
    return isfinite(policy->refresh_seconds) && policy->refresh_seconds > 0;
}

int vg_detect_tier(const vg_detect_state *state, const vg_detect_policy *policy, double ts) {
    if (!valid(state, policy, ts)) return VG_DETECT_FAST;
    if (ts - state->last_face_ts >= policy->deep_seconds &&
        ts - state->last_deep_ts >= policy->deep_seconds) return VG_DETECT_DEEP;
    return VG_DETECT_FAST;
}

int vg_detect_deep_allowed(const vg_detect_state *state, const vg_detect_policy *policy, double ts) {
    if (!valid(state, policy, ts)) return 0;
    return ts - state->last_deep_ts >= policy->deep_seconds;
}

int vg_detect_loss_allowed(const vg_detect_state *state, const vg_detect_policy *policy, double ts) {
    if (!valid(state, policy, ts)) return 0;
    return ts - state->last_deep_ts >= policy->refresh_seconds;
}

double vg_detect_fast_min_face(const vg_detect_state *state, double configured_fast,
                               double configured_deep, double side_margin) {
    double side;
    if (!state) return configured_fast;
    if (!isfinite(configured_fast) || !isfinite(configured_deep) ||
        configured_fast < configured_deep || configured_deep <= 0) return configured_fast;
    if (!isfinite(side_margin) || side_margin <= 0 || side_margin > 1) return configured_fast;
    side = state->last_face_side;
    if (!isfinite(side) || side <= 0) return configured_fast;
    side *= side_margin;
    if (!isfinite(side) || side <= configured_deep) return configured_deep;
    return side < configured_fast ? side : configured_fast;
}
