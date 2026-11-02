#ifndef VG_LANDMARK_RUNNER_H
#define VG_LANDMARK_RUNNER_H
#ifdef __cplusplus
extern "C" {
#endif

/* Board-side 68-point runner used by the camera pipeline.
 *
 * Input is a 1280x720 RGB frame (already decoded). The current crop is a CENTRE
 * crop, not a detector box: this is the integration seam until detection lands.
 * Returns 0 and fills `values` (143 floats) on success. */
int vg_landmark_init(const char *model_path, int threads);
int vg_landmark_run(const unsigned char *rgb, int width, int height,
                    float *values, int capacity);
/* Production path: detected half-open xyxy box -> author-style padded crop ->
 * image-coordinate points. crop_scale is supplied by exported config.yaml.
 * No previous box/points are retained on failure. Instance is thread-confined. */
int vg_landmark_run_box(const unsigned char *rgb, int width, int height,
                        const int *bbox, double crop_scale, float *points136,
                        float *values, int capacity);
/* Two-pass ROI bootstrap: centre crop -> points -> bbox -> padded crop -> points.
 * No detector: valid when the face is roughly centred and large enough.
 * Fills points136 with image-coordinate (x,y) pairs and bbox with x0,y0,x1,y1.
 * Returns 0 when both passes agree on a plausible face region, -1 otherwise. */
int vg_landmark_run_auto(const unsigned char *rgb, int width, int height,
                         float *points136, int *bbox, float *values, int capacity);
double vg_landmark_last_ms(void);
void vg_landmark_close(void);

#ifdef __cplusplus
}
#endif
#endif
