#ifndef VG_DETECT_POLICY_H
#define VG_DETECT_POLICY_H
/* How wide the full-frame search is allowed to be.
 *
 * The cascade verifies the tracked box with a cheap RNet/ONet refine on every
 * frame, so a full-frame search only runs when re-discovery is due or the
 * refine rejected the box. Its cost is set by the smallest face the pyramid
 * must cover: a wide (deep) search costs several times a narrow (fast) one.
 * Two rules keep that cost near the face that is actually tracked:
 *   - the narrow tier follows the last accepted face size, so a far or small
 *     face is not searched with a width that cannot contain it;
 *   - losing a tracked box may widen immediately (bounded by the refresh
 *     interval), instead of staying blind for the whole deep period.
 * This module owns no detector, pixels or clock, so it is testable without
 * models. All values come from config.yaml (board_vision). */
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    double last_full_ts;  /* last full-frame search, any tier (runtime-owned) */
    double last_deep_ts;  /* last deep-tier search */
    double last_face_ts;  /* last accepted detection or refine */
    double last_face_side; /* longest side of the last accepted box, 0 = none */
} vg_detect_state;

typedef struct {
    double refresh_seconds; /* full-frame refresh interval while tracking */
    double deep_seconds;    /* blind period that earns a deep search */
} vg_detect_policy;

enum { VG_DETECT_FAST = 1, VG_DETECT_DEEP = 2 };

/* Returns the tier for a search that is already due. Non-finite or
 * non-positive inputs return VG_DETECT_FAST: a broken clock or config must
 * still search, just not at the most expensive width. */
int vg_detect_tier(const vg_detect_state *state, const vg_detect_policy *policy, double ts);

/* May a routine (blind or cold-start) deep search run now? One per deep period. */
int vg_detect_deep_allowed(const vg_detect_state *state, const vg_detect_policy *policy, double ts);

/* May a lost tracked box be re-searched at the configured width now? Bounded
 * by the refresh interval: a widening replaces the next refresh instead of
 * adding to it, so even a pathologically alternating face never costs more
 * than one deep search per refresh - what the untiered build did every time. */
int vg_detect_loss_allowed(const vg_detect_state *state, const vg_detect_policy *policy, double ts);

/* min_face for a narrow search: the configured narrow width, lowered towards
 * the configured value when the tracked face is small, never finer than the
 * configured value. A search narrower than the face cannot contain it.
 * side_margin comes from config.yaml (board_vision.fast_side_margin). */
double vg_detect_fast_min_face(const vg_detect_state *state, double configured_fast,
                               double configured_deep, double side_margin);

#ifdef __cplusplus
}
#endif
#endif
