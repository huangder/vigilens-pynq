#include "behavior.h"

#include <math.h>
#include <stddef.h>
#include <stdlib.h>

typedef struct { double ts; int closed; } sample;
typedef struct { sample *data; size_t head, count, capacity; } queue;
struct vg_behavior {
    vg_behavior_config cfg;
    vg_eye_state state;
    int below_valid, open_valid, closed_valid, mouth_valid, yawn_counted;
    int started;
    double below_since, open_since, closed_since, mouth_since, t0, last_ts;
    uint64_t blink_count, long_close_count, yawn_count;
    queue window, blinks;
    size_t closed_samples;
};

static double distance(vg_point a, vg_point b) { return hypot(a.x-b.x, a.y-b.y); }
double vg_ear(const vg_eye *e) {
    double width;
    if (!e || !e->present) return 0.0;
    width = distance(e->p[0], e->p[3]);
    /* Numerical degeneracy guard from face_landmark.ear, not a tuning threshold. */
    if (width <= 1e-9) return 0.0;
    return (distance(e->p[1], e->p[5]) + distance(e->p[2], e->p[4])) / (2.0*width);
}
double vg_mar(const vg_mouth *m) {
    double width;
    if (!m || !m->present) return 0.0;
    width = distance(m->right, m->left);
    if (width <= 1e-9) return 0.0;
    return distance(m->bottom, m->top) / width;
}
const char *vg_eye_state_name(vg_eye_state s) {
    switch (s) {
    case VG_EYE_OPEN: return "OPEN";
    case VG_EYE_CLOSING: return "CLOSING";
    case VG_EYE_CLOSED: return "CLOSED";
    case VG_EYE_OPENING: return "OPENING";
    default: return NULL;
    }
}
static int reserve(queue *q) {
    size_t i, capacity;
    sample *data;
    if (q->count < q->capacity) return 0;
    if (q->capacity > SIZE_MAX / 2 / sizeof(sample)) return -1;
    capacity = q->capacity ? q->capacity*2 : 16; /* storage growth, not measurement limit */
    data = malloc(capacity*sizeof(*data));
    if (!data) return -1;
    for (i=0; i<q->count; ++i) data[i] = q->data[(q->head+i)%q->capacity];
    free(q->data);
    q->data=data; q->head=0; q->capacity=capacity;
    return 0;
}
static void append(queue *q, double ts, int closed) {
    size_t index=(q->head+q->count)%q->capacity;
    q->data[index].ts=ts; q->data[index].closed=closed; ++q->count;
}
static void evict(queue *q, double cutoff, size_t *closed) {
    while (q->count && q->data[q->head].ts < cutoff) {
        if (closed && q->data[q->head].closed) --*closed;
        q->head=(q->head+1)%q->capacity; --q->count;
    }
}
vg_behavior *vg_behavior_create(const vg_behavior_config *c) {
    vg_behavior *t;
    if (!c || !isfinite(c->ear_close_threshold) || !isfinite(c->min_close_ms) ||
        !isfinite(c->window_seconds) || !isfinite(c->long_close_ms) ||
        !isfinite(c->mar_threshold) || !isfinite(c->yawn_min_duration_ms) ||
        c->window_seconds<=0 || c->min_close_ms<0 || c->long_close_ms<0 ||
        c->yawn_min_duration_ms<0) return NULL;
    t=calloc(1, sizeof(*t));
    if (t) t->cfg=*c;
    return t;
}
void vg_behavior_destroy(vg_behavior *t) {
    if (!t) return;
    free(t->window.data); free(t->blinks.data); free(t);
}
int vg_behavior_update(vg_behavior *t, double ts, const vg_behavior_observation *o,
                       vg_behavior_result *out) {
    vg_behavior_result r={0};
    int has_face, closed, opened, mouth_open;
    double ear_avg, threshold_s, elapsed, rate_window;
    if (!t || !o || !out || !isfinite(ts) || (t->started && ts<t->last_ts)) return -1;
    /* Reserve before state transitions: allocation failure cannot half-apply a frame. */
    if (reserve(&t->window) || reserve(&t->blinks)) return -1;
    has_face=o->eyes_present && o->visible>0.0;
    if (has_face) {
        r.ear_left=vg_ear(&o->left); r.ear_right=vg_ear(&o->right);
        r.mar=vg_mar(&o->mouth);
    }
    ear_avg=0.5*(r.ear_left+r.ear_right);
    closed=has_face && ear_avg<t->cfg.ear_close_threshold;
    opened=has_face && ear_avg>=t->cfg.ear_close_threshold;
    threshold_s=t->cfg.min_close_ms/1000.0;
    if (closed) {
        t->open_valid=0;
        if (!t->below_valid) { t->below_since=ts; t->below_valid=1; }
        if (t->state==VG_EYE_OPEN) t->state=VG_EYE_CLOSING;
        if (t->state!=VG_EYE_CLOSED && ts-t->below_since>=threshold_s) {
            t->state=VG_EYE_CLOSED;
            t->closed_since=t->below_since; t->closed_valid=1;
        }
    } else if (opened) {
        t->below_valid=0;
        if (!t->open_valid) { t->open_since=ts; t->open_valid=1; }
        if (t->state==VG_EYE_CLOSING || t->state==VG_EYE_CLOSED) t->state=VG_EYE_OPENING;
        if (t->state==VG_EYE_OPENING && ts-t->open_since>=threshold_s) t->state=VG_EYE_OPEN;
        if (t->closed_valid) {
            if ((ts-t->closed_since)*1000.0>=t->cfg.long_close_ms) ++t->long_close_count;
            else { ++t->blink_count; append(&t->blinks, ts, 0); }
            t->closed_valid=0;
        }
    } else {
        t->below_valid=t->open_valid=t->closed_valid=0; t->state=VG_EYE_OPEN;
    }
    mouth_open=has_face && r.mar>t->cfg.mar_threshold;
    if (mouth_open) {
        if (!t->mouth_valid) { t->mouth_since=ts; t->mouth_valid=1; t->yawn_counted=0; }
        else if (!t->yawn_counted && (ts-t->mouth_since)*1000.0>=t->cfg.yawn_min_duration_ms) {
            ++t->yawn_count; t->yawn_counted=1;
        }
    } else { t->mouth_valid=0; t->yawn_counted=0; }
    append(&t->window, ts, closed);
    if (closed) ++t->closed_samples;
    evict(&t->window, ts-t->cfg.window_seconds, &t->closed_samples);
    evict(&t->blinks, ts-t->cfg.window_seconds, NULL);
    r.dt=t->started ? fmax(0.0, ts-t->last_ts) : 0.0;
    if (!t->started) { t->t0=ts; t->started=1; }
    t->last_ts=ts;
    elapsed=fmax(1e-6, ts-t->t0); /* Python's startup denominator guard. */
    rate_window=fmin(t->cfg.window_seconds, elapsed);
    r.perclos=t->window.count ? (double)t->closed_samples/t->window.count : 0.0;
    r.blink_rate_per_min=((double)t->blinks.count/rate_window)*60.0;
    r.blink_state=t->state; r.blink_count=t->blink_count;
    r.long_close_count=t->long_close_count; r.yawn_count=t->yawn_count;
    *out=r;
    return 0;
}
