/* Policy-only tests: no model, no pixels, no clock. */
#include "detect_policy.h"
#include <math.h>
#include <stdio.h>

#define NEVER (-1e300)

static int failures;

static vg_detect_policy policy(double refresh, double deep) {
    vg_detect_policy p;
    p.refresh_seconds = refresh;
    p.deep_seconds = deep;
    return p;
}

static vg_detect_state state(double deep_ts, double face, double side) {
    vg_detect_state s;
    s.last_full_ts = 0;
    s.last_deep_ts = deep_ts;
    s.last_face_ts = face;
    s.last_face_side = side;
    return s;
}

static void expect(const char *name, int got, int want) {
    if (got != want) {
        fprintf(stderr, "FAIL %s got=%d want=%d\n", name, got, want);
        ++failures;
    }
}

static void expect_close(const char *name, double got, double want) {
    if (!(fabs(got - want) < 1e-9)) {
        fprintf(stderr, "FAIL %s got=%.6f want=%.6f\n", name, got, want);
        ++failures;
    }
}

int main(void) {
    const vg_detect_policy cadence = policy(5.0, 30.0);

    /* Cold start: no face ever seen must earn the widest search once. */
    {
        const vg_detect_state s = state(NEVER, NEVER, 0);
        expect("cold-start", vg_detect_tier(&s, &cadence, 0.0), VG_DETECT_DEEP);
    }
    /* A blind run retries narrow and only widens once per deep period. */
    {
        const vg_detect_state s = state(0.0, NEVER, 0);
        expect("blind-narrow", vg_detect_tier(&s, &cadence, 0.5), VG_DETECT_FAST);
        expect("blind-narrow-late", vg_detect_tier(&s, &cadence, 29.9), VG_DETECT_FAST);
        expect("blind-wide-due", vg_detect_tier(&s, &cadence, 30.0), VG_DETECT_DEEP);
        const vg_detect_state t = state(29.0, NEVER, 0);
        expect("blind-wide-rate-limited", vg_detect_tier(&t, &cadence, 30.0), VG_DETECT_FAST);
    }
    /* While a face was seen recently, every search stays narrow. */
    {
        const vg_detect_state s = state(NEVER, 100.0, 200);
        expect("fresh-face", vg_detect_tier(&s, &cadence, 100.5), VG_DETECT_FAST);
        expect("fresh-face-late", vg_detect_tier(&s, &cadence, 129.9), VG_DETECT_FAST);
        expect("stale-face", vg_detect_tier(&s, &cadence, 130.0), VG_DETECT_DEEP);
    }
    /* After a deep search, the deep clock restarts even when nothing was found. */
    {
        const vg_detect_state s = state(30.0, NEVER, 0);
        expect("post-deep-narrow", vg_detect_tier(&s, &cadence, 31.0), VG_DETECT_FAST);
        expect("post-deep-wide", vg_detect_tier(&s, &cadence, 60.0), VG_DETECT_DEEP);
    }
    /* A lost tracked box may widen after one refresh interval, not one deep period. */
    {
        const vg_detect_state recent = state(0.0, NEVER, 0);
        const vg_detect_state due = state(0.0, NEVER, 0);
        expect("loss-allowed", vg_detect_loss_allowed(&due, &cadence, 5.0), 1);
        expect("loss-too-soon", vg_detect_loss_allowed(&recent, &cadence, 4.0), 0);
        expect("routine-deep-too-soon", vg_detect_deep_allowed(&recent, &cadence, 5.0), 0);
        expect("routine-deep-due", vg_detect_deep_allowed(&due, &cadence, 30.0), 1);
        expect("loss-broken-policy", vg_detect_loss_allowed(&due, &cadence, NAN), 0);
    }
    /* The narrow width follows the tracked face instead of a fixed constant. */
    {
        const vg_detect_state unknown = state(NEVER, NEVER, 0);
        const vg_detect_state big = state(NEVER, 100.0, 250);
        const vg_detect_state mid = state(NEVER, 100.0, 100);
        const vg_detect_state small = state(NEVER, 100.0, 50);
        expect_close("fast-unknown", vg_detect_fast_min_face(&unknown, 80, 40, 0.6), 80);
        expect_close("fast-big-face", vg_detect_fast_min_face(&big, 80, 40, 0.6), 80);
        expect_close("fast-mid-face", vg_detect_fast_min_face(&mid, 80, 40, 0.6), 60);
        expect_close("fast-small-face", vg_detect_fast_min_face(&small, 80, 40, 0.6), 40);
        expect_close("fast-null-state", vg_detect_fast_min_face(NULL, 80, 40, 0.6), 80);
        expect_close("fast-bad-config", vg_detect_fast_min_face(&big, 20, 40, 0.6), 20);
        expect_close("fast-bad-margin", vg_detect_fast_min_face(&small, 80, 40, 0.0), 80);
    }
    /* Broken clock or config still searches, but never at the widest width. */
    {
        const vg_detect_state s = state(NEVER, NEVER, 0);
        const vg_detect_policy zero = policy(0.0, 30.0);
        const vg_detect_policy nan = policy(5.0, NAN);
        expect("no-state", vg_detect_tier(NULL, &cadence, 10.0), VG_DETECT_FAST);
        expect("no-policy", vg_detect_tier(&s, NULL, 10.0), VG_DETECT_FAST);
        expect("nan-ts", vg_detect_tier(&s, &cadence, NAN), VG_DETECT_FAST);
        expect("zero-refresh", vg_detect_tier(&s, &zero, 10.0), VG_DETECT_FAST);
        expect("nonfinite-policy", vg_detect_tier(&s, &nan, 10.0), VG_DETECT_FAST);
    }
    if (failures) {
        fprintf(stderr, "DETECT_POLICY_FAIL count=%d\n", failures);
        return 1;
    }
    printf("DETECT_POLICY_PASS cases=%d\n", 31);
    return 0;
}
