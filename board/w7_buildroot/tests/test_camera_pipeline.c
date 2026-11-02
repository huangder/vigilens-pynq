/* Host-only tests of the production queues. No MMIO, camera, or board claims.
 * Include the C implementation so the exact worker/finish code is exercised;
 * decoding and PL are callbacks and deliberately replaced with slow/failing jobs.
 */
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <pthread.h>
static int create_worker(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);
#define pthread_create create_worker
#include "../camera_pl.c"
#undef pthread_create

static unsigned create_calls, fail_create, end_calls, consumed;
static int bad_decode, bad_consume;
static int observed_reorder;
static unsigned char *previous;

static int create_worker(pthread_t *t, const pthread_attr_t *a, void *(*fn)(void *), void *arg) {
    if (++create_calls == fail_create) return EAGAIN;
    return pthread_create(t, a, fn, arg);
}
int camera_stream_finish(unsigned frames) { assert(frames == consumed); ++end_calls; return 0; }
void camera_stream_close(void) {}

static int decode_test(job *j) {
    struct timespec delay = {.tv_nsec = 2000000};
    if (!j->ticket && p.decode_workers == 2) {
        /* Force frame 1 to publish first. PL must still consume frame 0 first. */
        pthread_mutex_lock(&p.mu);
        while (!p.ready[1] && !p.failed) pthread_cond_wait(&p.cv, &p.mu);
        observed_reorder = 1;
        pthread_mutex_unlock(&p.mu);
    }
    nanosleep(&delay, NULL);
    if ((int)j->sequence == bad_decode) return -1;
    memset(j->rgb, j->sequence, 16);
    return 0;
}
static int consume_test(job *j) {
    struct timespec delay = {.tv_nsec = 4000000};
    nanosleep(&delay, NULL);
    if ((int)j->sequence == bad_consume) return -1;
    assert(j->sequence == consumed);
    for (unsigned i = 0; i < 16; ++i) {
        assert(j->rgb[i] == (unsigned char)consumed);
        if (previous) assert(previous[i] == (unsigned char)(consumed - 1));
    }
    previous = j->rgb;
    ++consumed; ++p.frames;
    return 0;
}
static void setup(unsigned workers) {
    memset(&p, 0, sizeof(p)); p.mem = p.lock = -1;
    p.pipeline = p.sync_ready = 1;
    p.decode_workers = workers; p.history_slot = -1;
    assert(!pthread_mutex_init(&p.mu, NULL));
    assert(!pthread_cond_init(&p.cv, NULL));
    for (unsigned i = 0; i < POOL_BUFFERS; ++i) assert((p.pool[i] = malloc(16)));
    create_calls = end_calls = consumed = fail_create = 0;
    bad_decode = bad_consume = -1; previous = NULL; observed_reorder = 0;
}
static void submit(unsigned frames) {
    for (unsigned i = 0; i < frames; ++i) {
        job *j = calloc(1, sizeof(*j)); assert(j);
        j->sequence = j->ticket = i;
        if (queue_push(j)) { job_free(j); break; }
        ++p.submitted;
    }
}
static void drained(unsigned workers, unsigned frames) {
    setup(workers); assert(!pipeline_start(decode_test, consume_test)); submit(frames);
    assert(!camera_pl_finish());
    assert(consumed == frames && end_calls == 1);
    assert(!p.decode_started && !p.pl_started && !p.dec_count && !p.ready_count);
    assert(p.decode_peak == workers && !p.decode_active);
    if (workers == 2) assert(observed_reorder);
    camera_pl_close();
}
static void failed(unsigned workers, int decoder) {
    setup(workers);
    if (decoder) bad_decode = 2; else bad_consume = 2;
    assert(!pipeline_start(decode_test, consume_test)); submit(12);
    assert(camera_pl_finish() == -1 && end_calls == 0);
    assert(!p.dec_count && !p.ready_count && !p.decode_started && !p.pl_started);
    camera_pl_close();
}
int main(void) {
    alarm(15);
    for (unsigned workers = 1; workers <= 2; ++workers) {
        drained(workers, 2); drained(workers, 240);
        failed(workers, 1); failed(workers, 0);
        for (unsigned i = 1; i <= workers + 1; ++i) {
            setup(workers); fail_create = i;
            assert(pipeline_start(decode_test, consume_test) == -1);
            camera_pl_close(); /* Partial decoder/PL startup must be cleaned. */
        }
    }
    setup(2); assert(!pipeline_start(decode_test, consume_test)); submit(12);
    camera_pl_close(); /* Abort with jobs in flight, no successful end marker. */
    assert(!end_calls && !p.dec_count && !p.ready_count && !p.decode_started && !p.pl_started);
    unsetenv("VIGI_VERIFY"); unsetenv("VIGI_AUDIT_EVERY");
    setenv("VIGI_PIPELINE", "serial", 1); assert(!pipeline_config() && !p.pipeline);
    setenv("VIGI_PIPELINE", "threaded", 1); assert(!pipeline_config() && p.pipeline);
    setenv("VIGI_PIPELINE", "0", 1); assert(!pipeline_config() && !p.pipeline);
    setenv("VIGI_PIPELINE", "1", 1); assert(!pipeline_config() && p.pipeline);
    setenv("VIGI_PIPELINE", "typo", 1); assert(pipeline_config() == -1);
    setenv("VIGI_PIPELINE", "threaded", 1);
    setenv("VIGI_AUDIT_EVERY", "-1", 1); assert(pipeline_config() == -1);
    setenv("VIGI_AUDIT_EVERY", "1001", 1); assert(pipeline_config() == -1);
    setenv("VIGI_AUDIT_EVERY", "30junk", 1); assert(pipeline_config() == -1);
    setenv("VIGI_AUDIT_EVERY", "0", 1); assert(!pipeline_config() && p.audit_every == 0);
    unsetenv("VIGI_DECODE_WORKERS"); assert(!pipeline_config() && p.decode_workers == 2);
    setenv("VIGI_DECODE_WORKERS", "1", 1); assert(!pipeline_config() && p.decode_workers == 1);
    setenv("VIGI_DECODE_WORKERS", "2", 1); assert(!pipeline_config() && p.decode_workers == 2);
    setenv("VIGI_DECODE_WORKERS", "3", 1); assert(pipeline_config() == -1);
    setenv("VIGI_DECODE_WORKERS", "0", 1); assert(pipeline_config() == -1);
    setenv("VIGI_DECODE_WORKERS", "2junk", 1); assert(pipeline_config() == -1);
    setenv("VIGI_VERIFY", "1", 1);
    assert(!pipeline_config() && !p.pipeline && p.audit_every == 1 && p.decode_workers == 1);
    setenv("VIGI_PL_WAIT", "spin", 1); assert(!pipeline_config() && !p.poll_yield);
    setenv("VIGI_PL_WAIT", "yield", 1); assert(!pipeline_config() && p.poll_yield);
    setenv("VIGI_PL_WAIT", "typo", 1); assert(pipeline_config() == -1);
    puts("CAMERA_PIPELINE_HOST_PASS workers=1,2 ordered_drain=2,240 reorder=forced history=retained worker_failures=4 startup_failures=5 abort=1");
    return 0;
}
