#ifndef VG_AUTONOMY_CAMERA_H
#define VG_AUTONOMY_CAMERA_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct vg_camera vg_camera;
typedef struct {
    uint64_t submitted, accepted, skipped, processed, succeeded, rejected, failed;
    int first_error;
    double capture_seconds, analysis_seconds;
} vg_camera_stats;
/* One producer, one lifecycle owner; finish/destroy must not race submit.
 * Two owned RGB slots, including the one in inference. No overwrite on full.
 * Paths must name distinct regular output files, never stdout. New runs truncate
 * JSONL and remove old latest before model initialization. NULL on init failure.
 * All vision operations run exclusively on the worker thread. */
vg_camera *vg_camera_open(const char *detector_directory, const char *model,
                          const char *jsonl_path, const char *latest_path);
/* RGB888 and all metadata MUST come from the same completed camera/PL frame.
 * mean_luma = mean(actual PL gray)/255; ts is capture/sample time, not inference
 * completion time. Copies synchronously; caller may reuse RGB on return.
 * 1=accepted, 0=full/drop, negative=error. Never waits for inference or file IO.
 * Camera capture rate and successful analysis update rate are separate metrics. */
int vg_camera_submit(vg_camera *, const unsigned char *rgb, int w, int h,
                     double mean_luma, uint32_t motion_q16, int motion_valid,
                     uint64_t frame_id, double ts);
/* Zero-copy pool lease. Caller keeps RGB + optional PL gray immutable until
 * release(context). Exactly one callback on an accepted frame, including on
 * runtime/publish failure. On 0/error ownership stays with caller (no callback).
 * Finish drains and releases all accepted leases before returning. */
int vg_camera_submit_leased(vg_camera *,const unsigned char *rgb,int w,int h,
    const unsigned char *gray,int gray_w,int gray_h,double mean,uint32_t motion,int valid,
    uint64_t id,double ts,void (*release)(void *),void *context);
/* Stops admission, drains queued frames and joins; repeat calls are safe.
 * Returns first hard error or 0. Motion-not-valid (-2) and PnP (-4) rejections
 * clear latest and count as rejected. All other vision/IO errors are failures.
 * Stats are actual execution counts; this API makes no inference FPS promise. */
int vg_camera_finish(vg_camera *, vg_camera_stats *);
void vg_camera_destroy(vg_camera *);
#ifdef __cplusplus
}
#endif
#endif
