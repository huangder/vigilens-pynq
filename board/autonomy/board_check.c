/* Board-side self-check for the autonomy core: vital estimate + contract lines.
 *
 * Deterministic synthetic input only (no camera, no model, no personal data).
 * Output is meant to be captured over the serial console and validated on the PC:
 *   - `vital`    prints HR/conf from the same 1000-sample stream the host test uses;
 *   - `contract` prints eight contract JSON lines for backend/contract.py.
 * Exit code 0 means every internal call succeeded; it does not assert accuracy. */
#include "contract_json.h"
#include "decision.h"
#include "measure.h"
#include "vital.h"
#include "src_fir.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

/* Mirrors the host test's geometry: open eye, closed mouth. */
static vg_behavior_observation observation(double ear, double visible)
{
    vg_behavior_observation o;
    int i;
    memset(&o, 0, sizeof(o));
    o.visible = visible;
    o.eyes_present = visible > 0.0;
    o.left.present = o.right.present = o.eyes_present;
    o.mouth.present = o.eyes_present;
    for (i = 0; i < 6; ++i) {
        double x = i == 0 ? 0.0 : (i == 3 ? 1.0 : (i == 1 || i == 5 ? 0.25 : 0.75));
        double y = (i == 1 || i == 2) ? -ear / 2.0 : ((i == 4 || i == 5) ? ear / 2.0 : 0.0);
        o.left.p[i].x = x; o.left.p[i].y = y;
        o.right.p[i].x = x; o.right.p[i].y = y;
    }
    o.mouth.left.x = 0.0; o.mouth.left.y = 0.0;
    o.mouth.right.x = 1.0; o.mouth.right.y = 0.0;
    o.mouth.top.x = 0.5; o.mouth.top.y = -0.15;
    o.mouth.bottom.x = 0.5; o.mouth.bottom.y = 0.15;
    return o;
}

static vg_measure_config measure_config(void)
{
    vg_measure_config c;
    memset(&c, 0, sizeof(c));
    c.behavior.ear_close_threshold = 0.21;
    c.behavior.min_close_ms = 100.0;
    c.behavior.window_seconds = 30.0;
    c.behavior.long_close_ms = 500.0;
    c.behavior.mar_threshold = 0.6;
    c.behavior.yawn_min_duration_ms = 800.0;
    c.quality.light_target = 0.5;
    c.quality.motion_score_gain = 1.0;
    c.quality.weight_light = 0.5;
    c.quality.weight_motion = 0.3;
    c.quality.weight_face = 0.2;
    c.decision.quality_min_score = 0.6;
    c.decision.face_visible_min = 0.7;
    c.decision.pose_yaw_max_deg = 30.0;
    c.decision.pose_pitch_max_deg = 25.0;
    c.decision.perclos_warning = 0.25;
    c.decision.fatigue_blink_rate_low = 10.0;
    c.decision.vital_require_quality = 0.75;
    c.decision.fatigue_yawn_count = 2;
    c.decision.fatigue_long_close_count = 1;
    c.decision.pose_hold_frames = 3;
    return c;
}

static int run_vital(void)
{
    vg_vital_config cfg;
    vg_vital *vital;
    vg_vital_result result;
    int i;
    memset(&cfg, 0, sizeof(cfg));
    cfg.fps = 30.0;
    cfg.fs_tol = 0.10;
    cfg.window_seconds = 30.0;
    cfg.color_window_seconds = 1.6;
    cfg.f_lo = 0.7;
    cfg.f_hi = 3.5;
    cfg.harmonic_ratio_min = 0.05;
    cfg.max_gap_s = 0.1;
    cfg.jitter_ratio_max = 1.25;
    cfg.recovery_s = 0.1;
    cfg.coeffs = FIR_COEFF_Q15;
    cfg.taps = FIR_NUM_TAPS;
    cfg.shift = FIR_COEFF_SHIFT;
    cfg.recovery_samples = 3;
    vital = vg_vital_create(&cfg);
    if (!vital) { printf("VITAL create failed\n"); return 1; }
    for (i = 1; i <= 1000; ++i) {
        double t = (double)i / 30.0;
        double pulse = 0.012 * sin(2.0 * 3.14159265358979323846 * 1.2 * t);
        uint32_t sums[3];
        sums[0] = (uint32_t)(120.0 * (1.0 + pulse) * 1000.0);
        sums[1] = (uint32_t)(118.0 * (1.0 - pulse) * 1000.0);
        sums[2] = (uint32_t)(112.0 * (1.0 + 0.5 * pulse) * 1000.0);
        if (vg_vital_push_rgb(vital, sums, 1000, (uint64_t)i, t)) return 1;
    }
    vg_vital_estimate(vital, &result);
    printf("VITAL present=%d reject=%s hr=%.6f conf=%.6f\n", result.present,
           result.reject ? result.reject : "-",
           result.present ? result.hr_bpm : 0.0, result.present ? result.hr_conf : 0.0);
    vg_vital_destroy(vital);
    return result.present ? 0 : 2;
}

static int run_contract(void)
{
    vg_measure_config config = measure_config();
    vg_measure *engine = vg_measure_create(&config);
    int i;
    if (!engine) { printf("MEASURE create failed\n"); return 1; }
    for (i = 1; i <= 8; ++i) {
        vg_measure_input input;
        vg_measure_result result;
        vg_contract_face face;
        int rc;
        memset(&input, 0, sizeof(input));
        input.frame_id = (uint64_t)i;
        input.ts = (double)i / 30.0;
        input.mean_luma = 0.5;
        input.motion_q16 = 1000;
        input.motion_valid = 1;
        input.observation = observation(0.3, 1.0);
        input.face.visible = 1.0;
        input.face.yaw = 0.0;
        input.face.pitch = 0.0;
        rc = vg_measure_update(engine, &input, &result);
        if (rc) { printf("MEASURE rc=%d at %d\n", rc, i); vg_measure_destroy(engine); return 1; }
        memset(&face, 0, sizeof(face));
        face.bbox[2] = 200; face.bbox[3] = 200;
        face.visible = 1.0;
        printf("CONTRACT ");
        if (vg_contract_write(stdout, &result, &face, NULL)) return 1;
    }
    vg_measure_destroy(engine);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 2) { fprintf(stderr, "usage: vg_board_check vital|contract\n"); return 2; }
    if (!strcmp(argv[1], "vital")) return run_vital();
    if (!strcmp(argv[1], "contract")) return run_contract();
    fprintf(stderr, "unknown mode: %s\n", argv[1]);
    return 2;
}
