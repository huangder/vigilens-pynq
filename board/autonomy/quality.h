#ifndef VG_QUALITY_H
#define VG_QUALITY_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Formula-only PS port of backend/quality.py. No image decoder, placeholder
 * generator, pixel grayscale conversion or frame-difference implementation.
 * All tuning parameters are loaded by the caller from config.yaml. */
typedef struct {
    double light_target, motion_score_gain;
    double weight_light, weight_motion, weight_face;
} vg_quality_config;
typedef struct {
    double light_score, motion_score, overall, mean_luma, motion_ratio;
} vg_quality_result;

/* mean_luma = mean(gray)/255, using the SAME full decimated grayscale frame
 * as quality.py::to_gray, not only the forehead ROI. ROI RGB sums from PL
 * roi_statistic are not interchangeable with this whole-frame statistic.
 * motion_ratio comes from same-frame full-gray motion_ratio_q16 / 65536.
 * face_visible is the current landmark observation's visible value.
 * Matches QualityScorer.update's pixels-mode formula; face is not clamped
 * before weighting, just as in Python. Caller supplies motion validity and
 * temporal/frame binding upstream; no stale cached statistics are inferred.
 * Output doubles are unrounded; serialization rounds to 4 decimal places
 * using Python round semantics before decision/PL override consumes them.
 * Returns 0, or -1 on invalid pointer/nonfinite inputs, leaving out unchanged. */
int vg_quality_from_stats(const vg_quality_config *config, double mean_luma,
                          double motion_ratio, double face_visible,
                          vg_quality_result *out);

/* Matches QualityScorer.apply_motion_ratio's numeric fields. light_score
 * must be the already rounded output of the initial pixel quality update.
 * Ratio, light and face are clamped here, unlike update's raw face input.
 * mean_luma and other metadata in the caller's quality record stay unchanged.
 * face_score=NULL implements Python's omitted/None face_score -> 0.
 * For the legacy simulated path the caller preserves _motion_source=pl_sim;
 * real PL provenance belongs in its actual acquisition record. */
int vg_quality_apply_motion_ratio(const vg_quality_config *config,
                                  double light_score, double motion_ratio,
                                  const double *face_score, vg_quality_result *out);

/* Decode valid HLS motion_quality Q16 only; 65536 represents exactly 1.
 * valid=0 (including hardware's first frame) returns -2 and leaves out alone;
 * q16>65536 or null output returns -1. The caller must suppress invalid
 * measurement output or explicitly apply its documented first-frame policy.
 * This helper intentionally does not invent a ratio from an invalid PL result. */
int vg_quality_motion_q16(uint32_t q16, int valid, double *out);
#ifdef __cplusplus
}
#endif
#endif
