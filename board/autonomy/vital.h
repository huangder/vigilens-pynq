#ifndef VG_VITAL_H
#define VG_VITAL_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Native PS rPPG estimator, ported from backend/vital.py + backend/rppg_color.py.
 *
 * Green/POS selection follows config.yaml. Both frame-id playback and guarded
 * source-timestamp re-gridding are supported; invalid windows return a reason. */
typedef struct {
    double fps;                 /* fps_nominal, must match the frozen FIR design rate */
    double fs_tol;              /* rppg_fs_tol */
    double window_seconds;      /* rppg_window_seconds; legacy falls back to window_seconds */
    double color_window_seconds;/* POS local window (rppg_color_window_seconds) */
    double f_lo, f_hi;          /* hr_band_hz */
    double harmonic_ratio_min;  /* rppg_harmonic_ratio_min */
    double max_gap_s;           /* rppg_max_gap_ms / 1000 */
    double jitter_ratio_max;    /* realtime_jitter_ratio_max */
    double recovery_s;          /* rppg_recovery_ms / 1000; 0 disables soft pause */
    const short *coeffs;        /* FIR_COEFF_Q15, taps entries */
    int taps;
    int shift;
    int recovery_samples;       /* rppg_recovery_samples */
    int method;                 /* 0 = POS (legacy ABI default), 1 = green */
    double short_window_seconds, short_window_min_conf; /* both zero: legacy guard */
    double history_seconds;     /* 0: fixed window; otherwise bounded growing overlap */
    double source_min_hz;       /* 0: strict nominal tolerance; >0: timestamp re-grid input minimum */
    int green_center_before_fir; /* explicit candidate; 0 keeps the frozen reference */
} vg_vital_config;

typedef struct {
    int present;                /* 1 = accepted, 0 = refused (reject names why) */
    double hr_bpm;
    double hr_conf;
    const char *reject;         /* NULL when present */
} vg_vital_result;

typedef struct vg_vital vg_vital;
vg_vital *vg_vital_create(const vg_vital_config *config);
void vg_vital_destroy(vg_vital *vital);
/* 0 = sample accepted, -1 = invalid argument, -2 = rejected (window cleared). */
int vg_vital_push_rgb(vg_vital *vital, const uint32_t sums[3], uint32_t count,
                      uint64_t frame_id, double ts);
/* Mirror backend.vital.GreenRppg.invalidate: with allow_recovery and a short gap
 * the window is kept but estimation is blocked for `recovery_samples` pushes;
 * otherwise the window is cleared. `ts` may be NAN for "no timestamp". */
void vg_vital_invalidate(vg_vital *vital, const char *reason, double ts, int allow_recovery);
/* Discard all retained samples and FIR history (Python's reset(reason)).
 * Used when face/ROI is lost: the reference clears the window immediately and
 * never reports an estimate built across the gap. */
void vg_vital_reset(vg_vital *vital);
/* Sample count currently retained for the measurement window. */
int vg_vital_samples(const vg_vital *vital);
int vg_vital_history_samples(const vg_vital *vital);
/* Minimum captured-sample density: explicit source_min_hz in timestamp mode,
 * otherwise fps * (1 - fs_tol). The processing/FIR grid remains cfg.fps. */
double vg_vital_required_hz(const vg_vital *vital);
/* 0 = evaluated (check result->present), -1 = invalid argument. */
int vg_vital_estimate(vg_vital *vital, vg_vital_result *out);
#ifdef __cplusplus
}
#endif
#endif
