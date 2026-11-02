#ifndef VG_MEASURE_H
#define VG_MEASURE_H
#include "behavior.h"
#include "quality.h"
#include "decision.h"
#ifdef __cplusplus
extern "C" {
#endif

/* Native PS composition boundary. Caller owns capture/model/PL frame binding.
 * This module contains no detector, rPPG estimator or networking implementation. */
typedef struct {
    vg_behavior_config behavior;
    vg_quality_config quality;
    vg_decision_config decision;
} vg_measure_config;
typedef struct {
    uint64_t frame_id;
    double ts, mean_luma;
    uint32_t motion_q16;
    int motion_valid;
    vg_behavior_observation observation;
    vg_decision_face face;
} vg_measure_input;
typedef struct {
    uint64_t frame_id;
    double ts;
    vg_behavior_result behavior;
    vg_quality_result quality;
    vg_decision_result decision;
    vg_vitals vital; /* all null: no estimator is wired in this migration stage */
} vg_measure_result;
typedef struct vg_measure vg_measure;
vg_measure *vg_measure_create(const vg_measure_config *config);
void vg_measure_destroy(vg_measure *engine);
/* 0=success, -1=invalid/OOM, -2=motion invalid, -3=nonmonotonic frame/time.
 * Rejected frames leave logical state/output unchanged. No backfill or reuse
 * of a previous frame's PL ratio. Successful timestamp/frame IDs strictly rise. */
int vg_measure_update(vg_measure *engine, const vg_measure_input *input, vg_measure_result *out);
/* Match Python's decimal rounding before quality/behavior enter decision. */
double vg_round_decimal(double value, unsigned digits);
#ifdef __cplusplus
}
#endif
#endif
