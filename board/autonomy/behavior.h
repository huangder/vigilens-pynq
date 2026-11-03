#ifndef VG_BEHAVIOR_H
#define VG_BEHAVIOR_H

#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* PS-side port of backend/behavior_metrics.py; no model or configuration defaults. */
typedef struct { double x, y; } vg_point;
typedef struct { int present; vg_point p[6]; } vg_eye; /* p1 through p6 */
typedef struct {
    int present;
    vg_point left, right, top, bottom;
} vg_mouth;
typedef struct {
    int eyes_present; /* bool(obs.eyes), independent of individual eye presence */
    double visible;
    vg_eye left, right;
    vg_mouth mouth;
} vg_behavior_observation;
typedef struct {
    double ear_close_threshold, min_close_ms, window_seconds;
    double long_close_ms, mar_threshold, yawn_min_duration_ms;
} vg_behavior_config;
/* Opt-in board ERT candidate; legacy PC-parity behavior remains the default. */
typedef struct {
    double close_ratio, open_ratio, baseline_tau_s;
    double min_close_ms, min_open_ms, max_gap_ms, baseline_reset_ms;
    double close_threshold, open_threshold; /* both zero selects adaptive mode */
    double long_close_ms; /* zero preserves the legacy behavior config limit */
} vg_blink_config;
typedef enum {
    VG_EYE_OPEN, VG_EYE_CLOSING, VG_EYE_CLOSED, VG_EYE_OPENING
} vg_eye_state;
typedef struct {
    double ear_left, ear_right, blink_rate_per_min, perclos, mar, dt;
    vg_eye_state blink_state;
    uint64_t blink_count, long_close_count, yawn_count;
} vg_behavior_result;
typedef struct vg_behavior vg_behavior;

double vg_ear(const vg_eye *eye);
double vg_mar(const vg_mouth *mouth);
const char *vg_eye_state_name(vg_eye_state state);
/* Caller loads config.yaml and resolves the legacy frames/fps alias before create.
 * NULL means invalid config/allocation failure. Tracker owns its window storage. */
vg_behavior *vg_behavior_create(const vg_behavior_config *config);
int vg_behavior_configure_blink(vg_behavior *tracker, const vg_blink_config *config);
void vg_behavior_destroy(vg_behavior *tracker);
/* Returns 0 on success, -1 for invalid arguments/backward/nonfinite time or OOM.
 * Failure leaves logical tracker state and output unchanged. Repeated ts is allowed.
 * Result doubles are UNROUNDED: contract serialization rounds EAR/MAR/PERCLOS
 * to 4 decimal places and blink rate to 2, using Python round semantics.
 * PERCLOS uses a FRAME proportion, including missing-face frames in its
 * denominator. The default matches Python; opt-in blink policy uses hysteresis.
 * Long closures and yawns are cumulative, not window counts.
 * Caller must serialize access; one tracker represents one measurement session. */
int vg_behavior_update(vg_behavior *tracker, double ts,
                       const vg_behavior_observation *observation,
                       vg_behavior_result *result);
#ifdef __cplusplus
}
#endif
#endif
