/* Timestamp slot selection. No invented timestamps and no duplicate frames. */
#ifndef VIGI_SAMPLE_GATE_H
#define VIGI_SAMPLE_GATE_H
#include <stdint.h>

struct sample_gate {
    uint64_t first_us, last_us, slot, kept, dropped, empty;
    unsigned fps;
    int started, native_cadence;
};

/* A source already negotiated at the target rate must not be sampled a second
 * time. Real microsecond timestamp jitter can put two distinct buffers in one
 * floor-rounded slot. Keep those original frames; downstream timing/gap checks
 * still use their actual timestamps. Faster negotiated modes keep slot gating. */
static inline int sample_gate_set_interval(struct sample_gate *g,unsigned numerator,unsigned denominator) {
    if(!g||!g->fps||g->started||!numerator||!denominator)return -1;
    g->native_cadence=(uint64_t)denominator==(uint64_t)g->fps*numerator;
    return 0;
}

/* -1: invalid time; 0: same slot; 1: keep original buffer and timestamp. */
static int sample_gate_push(struct sample_gate *g, uint64_t ts_us) {
    uint64_t slot;
    if (!g->fps || (g->started && ts_us <= g->last_us)) return -1;
    if (!g->started) {
        g->first_us = g->last_us = ts_us;
        g->started = 1;
        g->kept = 1;
        return 1;
    }
    g->last_us = ts_us;
    if(g->native_cadence){++g->kept;return 1;}
    if (ts_us - g->first_us > UINT64_MAX / g->fps) return -1;
    slot = (ts_us - g->first_us) * g->fps / 1000000;
    if (slot <= g->slot) { ++g->dropped; return 0; }
    g->empty += slot - g->slot - 1;
    g->slot = slot;
    ++g->kept;
    return 1;
}
#endif
