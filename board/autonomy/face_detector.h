#ifndef VG_FACE_DETECTOR_H
#define VG_FACE_DETECTOR_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Each context belongs to one thread. No retained image or previous bbox. */
typedef struct vg_fd vg_fd;
typedef struct {
    float min_face, scale_factor;
    float score_p, score_r, score_o;
    float nms_p_scale, nms_p, nms_r, nms_o;
    int max_candidates;
} vg_fd_params;
typedef struct { int x1, y1, x2, y2; float score; } vg_fd_box;
/* dir contains det{1,2,3}.{param,bin}. NULL on failure. */
vg_fd *vg_fd_create(const char *dir, int threads);
/* packing must be 0 or 1. create() preserves its historical packing=0 mode.
 * extract() returns unpacked float outputs in either mode. */
vg_fd *vg_fd_create_ex(const char *dir, int threads, int packing);
void vg_fd_destroy(vg_fd *ctx);
/* RGB888, stride in bytes. Integer clipped half-open boxes in score order.
 * Returns number written, 0 for no face, -1 invalid input, -2 inference error.
 * Every threshold and candidate budget must be supplied by config.yaml caller.
 * Resource validation: max_candidates <= 65536 and <=256 full-image pyramid
 * levels; oversize budgets / near-one factors return -1. */
int vg_fd_detect(vg_fd *ctx, const uint8_t *rgb, int width, int height,
                 int stride, const vg_fd_params *params,
                 vg_fd_box *boxes, int capacity);
/* Validate ONE prior-frame candidate on THIS frame with RNet+ONet. The input
 * must be an in-image half-open box. Its score is ignored. No PNet is run;
 * geometry is squared before RNet, then regressed normally. Same return codes
 * as detect; returns at most one box. A 0 result requires fresh detection,
 * never reuse the candidate as a successful result. Input/output may alias. */
int vg_fd_refine(vg_fd *ctx, const uint8_t *rgb, int width, int height,
                 int stride, const vg_fd_params *params,
                 const vg_fd_box *candidate, vg_fd_box *boxes, int capacity);
#ifdef __cplusplus
}
#endif
#endif
