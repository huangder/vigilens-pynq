#ifndef VG_VISION_RUNTIME_H
#define VG_VISION_RUNTIME_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct vg_vision vg_vision;
typedef struct {
    uint64_t frame_id;
    double ts;
    int width, height;
    const unsigned char *rgb; /* tightly packed RGB888; same frame as PL stats */
    double mean_luma;        /* mean of actual PL gray output divided by 255 */
    uint32_t motion_q16;
    int motion_valid;
    const unsigned char *pl_gray; /* optional owned/leased completed PL 3of8 gray */
    int gray_width,gray_height;
} vg_vision_frame;
typedef struct {
    int detections, visible;
    double detector_ms, landmark_ms, total_ms;
    int detector_path; /* 0=none, 1=full fast, 2=refine, 3=refine then full,
                          4=full deep, 5=fast then deep after a lost box,
                          6=current-image LK tracking, 7=rebased model correction,
                          8=current-image ERT, 9=ERT with a new detector anchor,
                          10=current-image LK in the ERT recovery experiment */
    double landmark_model_ms;
    /* CPU time of the calling analysis thread ONLY, excluding runtime workers. */
    double detector_caller_cpu_ms, landmark_caller_cpu_ms;
    /* Vital feed diagnostics: measured analysis cadence and, when no heart rate
     * is held, why. Reporting only; the contract JSON does not change. */
    double vital_rate_hz;
    const char *vital_reject;
    uint32_t roi_pixels;
    int vital_samples;
    const char *vital_gate;
    double raw_hr,raw_hr_conf;
    const char *hr_filter_reason;
} vg_vision_info;
/* One externally thread-confined instance per process. Implementations may own
 * bounded workers; update/destroy must be called serially by their owner.
 * Models are local files; all thresholds come from exported config.yaml. */
vg_vision *vg_vision_create(const char *detector_directory, const char *landmark_model);
void vg_vision_destroy(vg_vision *vision);
/* Produces a nine-field contract JSON line without newline. No previous frame's
 * box/points/motion are reused as measurements. The tracked implementation
 * derives current points from current pixels and validates late model anchors
 * in that image; inference cadence is independent. 0=JSON ready, negative=reject. */
int vg_vision_update(vg_vision *vision, const vg_vision_frame *frame,
                     char *json, size_t capacity, vg_vision_info *info);
#ifdef __cplusplus
}
#endif
#endif
