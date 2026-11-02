#include <assert.h>
#include <stdio.h>
#include "../sample_gate.h"

int main(void) {
    struct sample_gate fast = {.fps = 30}, slow = {.fps = 30};
    assert(!sample_gate_set_interval(&fast,1,60));
    assert(!sample_gate_set_interval(&slow,1,10));
    for (unsigned i = 0; i < 120; ++i)
        assert(sample_gate_push(&fast, 1000000 + (uint64_t)i * 1000000 / 60) >= 0);
    /* Microsecond timestamps round down; boundary ties must stay in one slot.
       Real cadence/jitter is audited separately from exact selected count. */
    assert(fast.kept == 60 && fast.dropped == 60 && fast.empty == 0);
    for (unsigned i = 0; i < 11; ++i)
        assert(sample_gate_push(&slow, 1000000 + (uint64_t)i * 100000) == 1);
    assert(slow.kept == 11 && slow.empty == 20 && slow.dropped == 0);
    assert(sample_gate_push(&slow, slow.last_us) == -1);
    assert(sample_gate_push(&slow, 1) == -1);
    struct sample_gate native={.fps=30};
    assert(!sample_gate_set_interval(&native,1001,30030));
    assert(native.native_cadence);
    for(unsigned i=0;i<900;++i) {
        /* +/- 3ms source jitter, including two buffers in one 30Hz slot. */
        uint64_t ts=1000000+(uint64_t)i*1000000/30+(i%2?3000:0);
        assert(sample_gate_push(&native,ts)==1);
    }
    assert(native.kept==900&&native.dropped==0);
    assert(sample_gate_push(&native,native.last_us)==-1);
    assert(sample_gate_set_interval(&native,1,30)==-1);
    struct sample_gate invalid={.fps=30};
    assert(sample_gate_set_interval(&invalid,0,30)==-1);
    assert(sample_gate_set_interval(&invalid,1,0)==-1);
    puts("SAMPLE_GATE_TEST_PASS fast=60/120 slow=11/11 empty_slots=20 native_jitter=900/900 invalid_time=rejected");
    return 0;
}
