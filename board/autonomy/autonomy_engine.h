#ifndef VG_AUTONOMY_ENGINE_H
#define VG_AUTONOMY_ENGINE_H
#include "contract_json.h"
#include "vital.h"
#include "hr_filter.h"
#ifdef __cplusplus
extern "C" {
#endif
/* All statistics and points MUST belong to this frame, in original-image pixels.
 * mean_luma is normalized 0..1, bbox is x,y,w,h, sums are RGB (not BGR).
 * The embedding program keeps LC_NUMERIC=C and FE_TONEAREST. */
typedef struct {
    uint64_t frame_id;
    double ts;
    int width, height;
    double visible;
    int bbox[4];
    double pose[3]; /* yaw,pitch,roll; only consumed in producer mode */
    double mean_luma;
    uint32_t motion_q16;
    int motion_valid;
    uint32_t count, sums[3];
    double points[68][2];
    int pose_force_cold; /* current detector correction; no reuse of an old pose */
} vg_autonomy_frame;
typedef struct {
    vg_measure_result measure;
    vg_contract_face face;
    vg_vitals vital;
} vg_autonomy_result;
typedef struct vg_autonomy vg_autonomy;
/* Configs are copied (vital coefficient storage must outlive the engine).
 * reproj_max_frac comes from config.yaml pose_reproj_max_frac. vital_every=0
 * disables estimation; positive values set compute cadence, not capture rate. */
vg_autonomy *vg_autonomy_create(const vg_measure_config *measure,
    const vg_vital_config *vital, double reproj_max_frac, int vital_every, int pose_pnp);
void vg_autonomy_destroy(vg_autonomy *engine);
/* Configure before the first frame. Copies the model; default create() retains
 * the frozen Peppa/cold-solve policy. Recovery uses existing vital gap settings. */
int vg_autonomy_configure_temporal(vg_autonomy *engine,const double model[68][3],
                                   int pose_tracking,int quality_recovery);
int vg_autonomy_configure_hr_filter(vg_autonomy *engine,const vg_hr_filter_config *config);
double vg_autonomy_raw_hr(const vg_autonomy *engine);
double vg_autonomy_raw_hr_conf(const vg_autonomy *engine);
const char *vg_autonomy_hr_filter_reason(const vg_autonomy *engine);
/* 0 success, -1 malformed/OOM, -2 motion/vital input refused, -3 nonmonotonic,
 * -4 pose solve failed. All rejections clear retained vital samples/estimate;
 * output is only written on success. Pose failure NEVER becomes a zero pose.
 * Missing face/ROI clears history immediately. Optional temporal recovery keeps
 * only a configured short quality/posture pause and always clears held values. */
int vg_autonomy_update(vg_autonomy *engine, const vg_autonomy_frame *frame,
    vg_autonomy_result *out);
/* Explicit loss notification for a capture/detector failure with no frame. */
void vg_autonomy_invalidate(vg_autonomy *engine);
int vg_autonomy_line(char *buffer, size_t capacity, const vg_autonomy_result *result);
int vg_autonomy_write(FILE *stream, const vg_autonomy_result *result);
/* Why the last update produced no heart rate (NULL when an estimate is held or
 * before the first gated update), and the feed rate it measured in Hz. A feed
 * slower than vg_vital_required_hz reports "sampling_too_slow" instead of
 * quietly waiting for a window that cannot fill. Both are read-only reports;
 * they never change the contract JSON. */
const char *vg_autonomy_vital_reject(const vg_autonomy *engine);
double vg_autonomy_vital_rate_hz(const vg_autonomy *engine);
int vg_autonomy_vital_samples(const vg_autonomy *engine);
/* Current-frame gate; distinct from the last spectral-estimation rejection. */
const char *vg_autonomy_vital_gate(const vg_autonomy *engine);
#ifdef __cplusplus
}
#endif
#endif
