#ifndef VG_DECISION_H
#define VG_DECISION_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double quality_min_score, face_visible_min, pose_yaw_max_deg, pose_pitch_max_deg;
    double perclos_warning, fatigue_blink_rate_low, vital_require_quality;
    uint64_t fatigue_yawn_count, fatigue_long_close_count;
    uint32_t pose_hold_frames;
} vg_decision_config;
typedef struct { double visible, yaw, pitch; } vg_decision_face;
typedef struct {
    double perclos, blink_rate_per_min;
    uint64_t long_close_count, yawn_count;
    double overall, light_score, motion_score;
    vg_decision_face face;
} vg_decision_input;
typedef struct {
    const char *rule, *metric, *verdict;
    double value, threshold;
} vg_decision_trigger;
typedef struct {
    char status[24], advice[256], reason[2048]; /* UTF-8; caller serializes JSON */
    unsigned trigger_count;
    vg_decision_trigger triggers[5]; /* quality gate plus four fatigue rules */
} vg_decision_result;
typedef struct vg_decision vg_decision;
/* Parameters supplied from config.yaml; pose_hold_frames=0 is clamped to 1. */
vg_decision *vg_decision_create(const vg_decision_config *config);
void vg_decision_destroy(vg_decision *engine);
/* Input behavior numbers must already have Python BehaviorTracker's rounding.
 * Output explanation formatting requires the C numeric locale (decimal point).
 * 0 success, -1 invalid pointers or nonfinite inputs (state/output unchanged).
 * Quality and visibility early returns deliberately freeze pose streaks. */
int vg_decision_decide(vg_decision *engine, const vg_decision_input *input,
                       vg_decision_result *result);
int vg_decision_vitals_allowed(const vg_decision *engine, double quality,
                               const vg_decision_face *face, const char *status);
/* Four slots: hr_bpm, hr_conf, rr_per_min, rr_conf; present=0 represents null.
 * NULL input represents Python's absent vital dictionary. NULL face retains
 * Python gate_vitals' quality-only legacy call semantics, including status. */
typedef struct { double value[4]; int present[4]; } vg_vitals;
void vg_decision_gate_vitals(const vg_decision *engine, double quality,
                            const vg_vitals *input, const vg_decision_face *face,
                            const char *status, vg_vitals *output);
#ifdef __cplusplus
}
#endif
#endif
