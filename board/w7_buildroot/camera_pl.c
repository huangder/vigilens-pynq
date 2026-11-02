/* Linux bring-up only: UVC MJPG -> RGB -> existing PL chain -> byte comparison.
 * Pixels remain in RAM. This is a full-frame hardware test, not face analysis.
 * Requires direct_loop.dtsi, a matching 720p bitstream, and no DMA kernel owner.
 *
 * 2026-10-29 S1/S2/S3 (PC-simulated first, see backend/board_sim.py):
 *   S1  VIGI_STREAM_CODEC=jpeg forwards the camera's own MJPEG instead of RGB;
 *   S2  VIGI_AUDIT_EVERY=N keeps the scalar reference only every N frames;
 *       VIGI_VERIFY=1 restores the old every-frame reference (serial);
 *   S3  VIGI_PIPELINE=1 (default unless verifying) runs capture/decode/PL as
 *       two worker threads with bounded queues, so frame time becomes
 *       max(decode, PL) instead of decode + reference + PL.
 * Verification status and board evidence: docs/47.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <jpeglib.h>
#include "camera_pl.h"
#include "dma_gray.h"
#include "camera_geometry.h"
#include "yuyv_rgb.h"
#ifdef VIGI_LANDMARK
#include "../autonomy/landmark_runner.h"
static void landmark_submit(const unsigned char *rgb);
static void *landmark_worker(void *unused);
#endif
#include "camera_stream.h"
#ifdef VIGI_AUTONOMY
#include "../autonomy/autonomy_camera.h"
#endif

/* Existing S4/contract register map; geometry matches system_wrapper_720p60.bit. */
#define RAM_PHYS 0x12000000u
#define RAM_SIZE 0x01000000u
#define GRAY_OFF 0x00400000u
#define GRAY_SIZE (p.g.gray_bytes)
_Static_assert(GRAY_OFF % 4 == 0, "aligned gray DDR word reads");
#define SINK_MARGIN 384u
#define QUEUE_CAP 2
#define POOL_BUFFERS 6
#define MAX_DECODE_WORKERS 2
/* Explicit ownership protects both active decoders and PL's current/history. */
_Static_assert(POOL_BUFFERS >= MAX_DECODE_WORKERS + 2, "RGB history must outlive decoding");
#define POISON_BYTE 0xA5u

typedef struct job {
    unsigned char *jpeg;
    size_t len;
    uint64_t ts_us;
    unsigned sequence;
    unsigned ticket, slot;
    unsigned char *rgb;
    double decode_ms, decode_cpu_ms;
} job;

static struct {
    int mem, lock;
    struct vg_camera_geometry g;
    int input_yuyv,limited;unsigned input_stride;
    unsigned char *ram, *rgb, *gold, *prev;
    volatile uint32_t *dma, *roi, *gray, *motion;
    unsigned threshold, frames;
    uint32_t base_fid;
#ifdef VIGI_AUTONOMY
    vg_camera *autonomy;
#endif
    /* S2 */
    unsigned audit_every;
#ifdef VIGI_LANDMARK
    unsigned landmark_every;
    pthread_t landmark_thread;
    pthread_mutex_t landmark_mu;
    pthread_cond_t landmark_cv;
    unsigned char *landmark_buf[2];
    int landmark_write, landmark_ready_index, landmark_ready, landmark_stop, landmark_started;
    unsigned long long landmark_runs;
#endif
    unsigned char *prev_rgb, *prev_gold, *prev_copy;
    unsigned char *poison;
    unsigned long long audits, audit_failures, ref_ms;
    /* S3 */
    int pipeline, input_closed, failed, sync_ready;
    int poll_yield;
    unsigned decode_started, decode_workers, decode_live, decode_active, decode_peak;
    int pl_started, history_slot;
    unsigned submitted;
    int (*decode_job)(job *);
    int (*consume_job)(job *);
    pthread_t decode_threads[MAX_DECODE_WORKERS], pl_thread;
    pthread_mutex_t mu;
    pthread_cond_t cv;
    job *dec_q[QUEUE_CAP], *ready[POOL_BUFFERS];
    unsigned dec_head, dec_tail, dec_count;
    unsigned next_consume, ready_count;
    unsigned char *pool[POOL_BUFFERS];
    unsigned char pool_used[POOL_BUFFERS];
    unsigned pool_readers[POOL_BUFFERS];
#ifdef VIGI_AUTONOMY
    unsigned char *pool_gray[POOL_BUFFERS];
#endif
    /* provenance */
    char codec[16];
} p = {.mem = -1, .lock = -1};

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static double cpu_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static void poll_pause(void) {
    /* Scheduling interval, not a measurement threshold. Let JPEG/USB run
       while hardware works; the DMA completion tests/deadlines stay intact. */
    if (p.poll_yield) {
        struct timespec pause = {.tv_nsec = 100000};
        nanosleep(&pause, NULL);
    }
}
static uint32_t rd(volatile uint32_t *base, unsigned off) { return base[off / 4]; }
static void wr(volatile uint32_t *base, unsigned off, uint32_t value) {
    base[off / 4] = value;
    __sync_synchronize();
}
static int wait_bits(volatile uint32_t *base, unsigned off, uint32_t mask, uint32_t value) {
    double until = now() + 2;
    do {
        if ((rd(base, off) & mask) == value) return 0;
        poll_pause();
    } while (now() < until);
    fprintf(stderr, "PL_TIMEOUT offset=0x%x value=0x%x\n", off, rd(base, off));
    return -1;
}
static int reset_dma(void) {
    wr(p.dma, 0, 4);
    if (wait_bits(p.dma, 0, 4, 0)) return -1;
    wr(p.dma, 0x30, 4);
    return wait_bits(p.dma, 0x30, 4, 0);
}
static volatile uint32_t *map_regs(unsigned addr) {
    void *v = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, p.mem, addr);
    return v == MAP_FAILED ? NULL : v;
}
static int reserved_profile(void) {
    static const unsigned char expected[8] = {0x12,0,0,0, 0x01,0,0,0};
    unsigned char actual[8];
    char profile[32] = {0}, line[256];
    int saw_ram = 0;
    FILE *f = fopen("/proc/device-tree/reserved-memory/vigilens-dma@12000000/reg", "rb");
    if (!f) return -1;
    size_t n = fread(actual, 1, 8, f); fclose(f);
    if (n != 8 || memcmp(actual, expected, 8)) return -1;
    f = fopen("/proc/device-tree/reserved-memory/vigilens-dma@12000000/no-map", "rb");
    if (!f) return -1;
    fclose(f);
    f = fopen("/proc/device-tree/chosen/vigilens,pl-profile", "rb");
    if (!f) return -1;
    n = fread(profile, 1, sizeof(profile)-1, f); fclose(f);
    if (!n || strcmp(profile, "720p-3of8")) return -1;
    f = fopen("/proc/iomem", "r");
    if (!f) return -1;
    /* ARM removes no-map ranges from System RAM rather than necessarily
       printing a separate "reserved" resource. Reject any allocator overlap. */
    while (fgets(line, sizeof(line), f)) {
        unsigned long long lo, hi;
        if (sscanf(line, "%llx-%llx", &lo, &hi) == 2 && strstr(line, "System RAM")) {
            saw_ram = 1;
            if (lo < (uint64_t)RAM_PHYS+RAM_SIZE && hi >= RAM_PHYS) {
                fclose(f); return -1;
            }
        }
    }
    fclose(f);
    return saw_ram ? 0 : -1;
}

/* ------------------------------------------------------------------ */
/* bounded queues: one mutex + one cond is enough at 30 fps            */
/* ------------------------------------------------------------------ */
static void wake_all(void) { pthread_cond_broadcast(&p.cv); }

static void job_free(job *j) {
    if (!j) return;
    free(j->jpeg);
    free(j);
}
#ifdef VIGI_AUTONOMY
static int retain_rgb(const unsigned char *rgb) {
    if(!p.pipeline)return -1;
    for(unsigned slot=0;slot<POOL_BUFFERS;++slot)if(p.pool[slot]==rgb) {
        pthread_mutex_lock(&p.mu);++p.pool_readers[slot];pthread_mutex_unlock(&p.mu);
        return (int)slot;
    }
    return -1;
}
static void release_rgb(void *context) {
    const unsigned slot=(unsigned)((uintptr_t)context-1);
    pthread_mutex_lock(&p.mu);
    if(slot<POOL_BUFFERS&&p.pool_readers[slot])--p.pool_readers[slot];
    else {p.failed=1;fputs("AUTONOMY_LEASE_FAIL\n",stderr);}
    wake_all();pthread_mutex_unlock(&p.mu);
}
#endif

static int decode(const unsigned char *data, size_t length, unsigned char *out);

/* One PL pass over an already decoded RGB buffer. `jpeg` is forwarded untouched
 * when S1 is on; it is never written to. */
static int process_rgb(const unsigned char *rgb, const unsigned char *jpeg, size_t jpeg_size,
                       uint64_t ts_us, unsigned sequence, double decode_ms, double decode_cpu_ms) {
    double t0 = now(), referenced = t0, finished;
#ifdef VIGI_LANDMARK
    if (p.landmark_every && (p.frames % p.landmark_every) == 0)
        landmark_submit(rgb);
#endif
    uint32_t sums[3] = {0}, pixels = 0, ratio = 0, ref_diff = 0, ref_pixels = 0, ref_ratio = 0;
    unsigned k = 0, mismatch = 0, done = 0;
    /* Audit only every N frames (S2). 0 means "never", which is only safe
       together with the register-level checks below. */
    int audit = p.audit_every && ((p.frames % p.audit_every) == 0);

    if (audit) {
        for (unsigned y = 0; y < p.g.height; ++y) for (unsigned x = 0; x < p.g.width; ++x) {
            const unsigned char *r = rgb + (y * p.g.width + x) * 3;
            sums[0] += r[0]; sums[1] += r[1]; sums[2] += r[2];
            if (y % 8 < 3 && x % 8 < 3) {
                p.gold[k++] = (unsigned char)((77u*r[0] + 150u*r[1] + 29u*r[2] + 128u) >> 8);
            }
        }
        if (k != GRAY_SIZE) return -1;
        if (p.prev_rgb) {
            /* PL retains ownership of this history buffer until this pass
               finishes, independently of decode completion order. */
            unsigned j = 0;
            unsigned char *prev_gold = p.prev_gold;
            for (unsigned y = 0; y < p.g.height; ++y) for (unsigned x = 0; x < p.g.width; ++x) {
                const unsigned char *r = p.prev_rgb + (y * p.g.width + x) * 3;
                if (y % 8 < 3 && x % 8 < 3) {
                    prev_gold[j++] = (unsigned char)((77u*r[0] + 150u*r[1] + 29u*r[2] + 128u) >> 8);
                }
            }
            for (k = 0; k < GRAY_SIZE; ++k) {
                unsigned d = p.gold[k] > prev_gold[k] ? p.gold[k] - prev_gold[k]
                                                      : prev_gold[k] - p.gold[k];
                ref_diff += d;
                if (d > p.threshold) ++ref_pixels;
            }
            ref_ratio = (uint32_t)(((uint64_t)ref_pixels << 16) / GRAY_SIZE);
        }
        referenced = now();
        p.ref_ms += (unsigned long long)((referenced - t0) * 1000.0);
    }

    double transfer_cpu_started = cpu_now();
    if (reset_dma()) return -1;
    memcpy(p.ram, rgb, p.g.rgb_bytes);
    if (audit) {
        /* Every initial byte differs from its expected output, including a
           uniform image. A stale/unwritten buffer can never pass. */
        for (k = 0; k < GRAY_SIZE; ++k) p.ram[GRAY_OFF + k] = p.gold[k] ^ 0xff;
    }
    /* Non-audit passes check the IP counts/frame ids and the tail sentinel,
       never the unused full-buffer poison. Keep full inversion for audits;
       avoid clearing 129600 uncached bytes on every other pass. */
    /* ap_done precedes the last S2MM DDR write. Even without a full audit,
       wait for a changed last byte before stopping this TLAST-less sink. */
    const unsigned char *last = rgb + ((p.g.height - 8u + 2u) * p.g.width + p.g.width - 8u + 2u) * 3u;
    unsigned char last_gray = (77u*last[0] + 150u*last[1] + 29u*last[2] + 128u) >> 8;
    volatile unsigned char *sink_last = p.ram + GRAY_OFF + GRAY_SIZE - 1;
    *sink_last = last_gray ^ 0xff;
    __sync_synchronize();
    wr(p.dma, 4, 0x7000); wr(p.dma, 0x34, 0x7000);
    wr(p.motion, 0, 1); wr(p.gray, 0, 1); wr(p.roi, 0, 1);
    wr(p.dma, 0x30, 1); wr(p.dma, 0x48, RAM_PHYS + GRAY_OFF);
    wr(p.dma, 0x58, GRAY_SIZE + SINK_MARGIN);
    wr(p.dma, 0, 1); wr(p.dma, 0x18, RAM_PHYS); wr(p.dma, 0x28, p.g.rgb_bytes);
    if (wait_bits(p.dma, 4, 0x1000, 0x1000)) goto fail;
    double until = now() + 2;
    do {
        if (rd(p.roi, 0) & 2) done |= 1;
        if (rd(p.gray, 0) & 2) done |= 2;
        if (rd(p.motion, 0) & 2) done |= 4;
        if (done != 7) poll_pause();
    } while (done != 7 && now() < until);
    if (done != 7) goto fail;
    /* TLAST is suppressed by this bitstream, and S2MM is deliberately armed
       with extra headroom. It need not assert HALTED at the frame boundary.
       Observe actual DDR write completion before stopping/resetting the sink. */
    until = now() + 2;
    while (*sink_last != last_gray && now() < until) {
        if ((rd(p.dma, 4) | rd(p.dma, 0x34)) & 0x70) goto fail;
        poll_pause();
    }
    if (*sink_last != last_gray || ((rd(p.dma, 4) | rd(p.dma, 0x34)) & 0x70)) goto fail;
    wr(p.dma, 0x30, 0);
    /* This TLAST-less, oversized transfer does not assert HALTED here.
       DDR completion was checked above; the next frame explicitly resets DMA. */
    __sync_synchronize();
    finished = now();
    double transfer_cpu_ms = (cpu_now() - transfer_cpu_started) * 1000.0;

    /* Register-level invariants hold on every frame, audit or not. */
    mismatch += rd(p.roi, 0x58) != p.g.width*p.g.height;
    mismatch += rd(p.gray, 0x30) != GRAY_SIZE || rd(p.motion, 0x40) != GRAY_SIZE;
    mismatch += rd(p.roi, 0x60) != p.base_fid + p.frames + 1;
    mismatch += rd(p.gray, 0x40) != p.base_fid + p.frames + 1;
    mismatch += rd(p.motion, 0x48) != p.base_fid + p.frames + 1;
    if (p.frames) {
        pixels = rd(p.motion, 0x30); ratio = rd(p.motion, 0x38);
        mismatch += pixels > GRAY_SIZE || rd(p.motion, 0x28) > GRAY_SIZE * 255u;
        mismatch += ratio != (uint32_t)(((uint64_t)pixels << 16) / GRAY_SIZE);
    }
    if (audit) {
        mismatch += memcmp(p.ram + GRAY_OFF, p.gold, GRAY_SIZE) != 0;
        for (k = 0; k < 3; ++k) mismatch += rd(p.roi, 0x40 + k*8) != sums[k];
        if (p.prev_rgb) {
            mismatch += rd(p.motion, 0x28) != ref_diff;
            mismatch += rd(p.motion, 0x30) != ref_pixels;
            mismatch += rd(p.motion, 0x38) != ref_ratio;
        }
        ++p.audits;
        if (mismatch) ++p.audit_failures;
    }
    printf("CAMERA_PL_FRAME n=%u seq=%u ts_us=%llu mismatch=%u audit=%u roi_g=%u roi_count=%u "
           "motion_valid=%u motion_ratio_q16=%u decode_ms=%.3f reference_ms=%.3f transfer_pl_ms=%.3f "
           "decode_cpu_ms=%.3f transfer_pl_cpu_ms=%.3f\n",
           p.frames+1, sequence, (unsigned long long)ts_us, mismatch, audit ? 1u : 0u,
           rd(p.roi,0x48), rd(p.roi,0x58), p.frames != 0, rd(p.motion,0x38),
           decode_ms, (referenced-t0)*1000, (finished-referenced)*1000, decode_cpu_ms, transfer_cpu_ms);
    if (!mismatch) {
#ifdef VIGI_AUTONOMY
        /* Snapshot statistics only AFTER PL completion. RGB and metadata are
           copied together into the bridge's owned slot before DDR/pool reuse. */
        double gray_started = now();
        int lease=-1;
#ifdef VIGI_TRACKED_RUNTIME
        lease=retain_rgb(rgb);
#endif
        uint64_t gray_sum;
#if defined(__ARM_NEON)
        /* Real reserved-DDR mapping parity/timing was verified before enabling
           this path. All samples still come from the completed PL output. */
        gray_sum=vg_dma_gray_copy_sum_neon((uint32_t *)(lease>=0?p.pool_gray[lease]:p.prev),
            (const volatile uint32_t *)(p.ram+GRAY_OFF),GRAY_SIZE/4);
#else
        gray_sum=lease>=0 ? vg_dma_gray_copy_sum_words((uint32_t *)p.pool_gray[lease],
            (const volatile uint32_t *)(p.ram+GRAY_OFF),GRAY_SIZE/4) :
            vg_dma_gray_sum_words((const volatile uint32_t *)(p.ram+GRAY_OFF),GRAY_SIZE/4);
#endif
        double gray_ms = (now() - gray_started) * 1000.0;
        double submit_started = now();
        int accepted;
        if(lease>=0) {
            void *context=(void *)(uintptr_t)(lease+1);
            accepted=vg_camera_submit_leased(p.autonomy,rgb,p.g.width,p.g.height,p.pool_gray[lease],p.g.gray_width,p.g.gray_height,
                (double)gray_sum/((double)GRAY_SIZE*255.0),p.frames?ratio:0,p.frames!=0,
                p.frames,(double)ts_us/1000000.0,release_rgb,context);
            if(accepted!=1)release_rgb(context);
        } else accepted=vg_camera_submit(p.autonomy,rgb,p.g.width,p.g.height,
            (double)gray_sum/((double)GRAY_SIZE*255.0),p.frames?ratio:0,p.frames!=0,
            p.frames,(double)ts_us/1000000.0);
        if (accepted < 0) {
            fprintf(stderr, "AUTONOMY_SUBMIT_FAIL frame=%u rc=%d\n", p.frames, accepted);
            return -1;
        }
        printf("AUTONOMY_HANDOFF frame_id=%u gray_ms=%.3f submit_ms=%.3f accepted=%d\n",
               p.frames, gray_ms, (now()-submit_started)*1000.0, accepted);
#endif
        char metadata[1024];
        /* Undefined BRAM history is never transmitted as a measured value. */
        int size = snprintf(metadata, sizeof(metadata),
            "{\"profile\":\"720p-3of8\",\"width\":%u,\"height\":%u,"
            "\"gray_width\":%u,\"gray_height\":%u,\"source_sequence\":%u,"
            "\"source_ts_us\":%llu,\"pl_frame_id\":%u,\"motion_valid\":%s,"
            "\"motion_ratio_q16\":%u,\"motion_threshold\":%u,"
            "\"sum_r\":%u,\"sum_g\":%u,\"sum_b\":%u,\"count\":%u,"
            "\"diff_total\":%u,\"motion_pixels\":%u}",
            p.g.width,p.g.height,p.g.gray_width,p.g.gray_height,
            sequence, (unsigned long long)ts_us, rd(p.roi,0x60), p.frames ? "true" : "false",
            p.frames ? rd(p.motion,0x38) : 0, p.threshold,
            rd(p.roi,0x40), rd(p.roi,0x48), rd(p.roi,0x50), rd(p.roi,0x58),
            p.frames ? rd(p.motion,0x28) : 0, p.frames ? rd(p.motion,0x30) : 0);
        if (size <= 0 || (size_t)size >= sizeof(metadata) ||
            camera_stream_send(metadata, (size_t)size, rgb, p.g.rgb_bytes, jpeg, jpeg_size)) {
            fprintf(stderr, "BOARD_STREAM_FAIL seq=%u errno=%d\n", sequence, errno);
            return -1;
        }
    }
    p.prev_rgb = (unsigned char *)rgb;
    if (!p.pipeline) {
        /* Serial mode reuses p.rgb, so the previous frame must be copied or
           the motion audit would compare a frame with itself. Threaded mode
           retains the previous buffer explicitly until the next pass ends. */
        memcpy(p.prev_copy, rgb, p.g.rgb_bytes);
        p.prev_rgb = p.prev_copy;
    }
    ++p.frames;
    return mismatch ? -1 : 0;
fail:
    fprintf(stderr, "CAMERA_PL_FAIL done_mask=%u mm2s=0x%x s2mm=0x%x\n",
            done, rd(p.dma,4), rd(p.dma,0x34));
    return -1;
}

static int decode_into(const unsigned char *data, size_t length, unsigned char *out) {
    return decode(data, length, out);
}

/* ------------------------------------------------------------------ */
/* S3 workers                                                          */
/* ------------------------------------------------------------------ */
static int queue_push(job *j) {
    pthread_mutex_lock(&p.mu);
    while (p.dec_count == QUEUE_CAP && !p.failed && !p.input_closed)
        pthread_cond_wait(&p.cv, &p.mu);
    if (p.failed || p.input_closed) { pthread_mutex_unlock(&p.mu); return -1; }
    p.dec_q[p.dec_tail] = j; p.dec_tail = (p.dec_tail + 1) % QUEUE_CAP; ++p.dec_count;
    wake_all();
    pthread_mutex_unlock(&p.mu);
    return 0;
}

static job *queue_take(void) {
    job *out = NULL;
    pthread_mutex_lock(&p.mu);
    while (!p.failed) {
        if (p.dec_count) {
            job *j = p.dec_q[p.dec_head];
            j->slot = j->ticket % POOL_BUFFERS;
            if (!p.pool_used[j->slot] && !p.pool_readers[j->slot]) {
                /* Reserve in dequeue order, before another decoder can take
                   a later ticket that wraps to this same buffer slot. */
                p.pool_used[j->slot] = 1;
                j->rgb = p.pool[j->slot];
                if (++p.decode_active > p.decode_peak) p.decode_peak = p.decode_active;
                out = j; p.dec_head = (p.dec_head + 1) % QUEUE_CAP; --p.dec_count;
                wake_all();
                break;
            }
        } else if (p.input_closed) break;
        pthread_cond_wait(&p.cv, &p.mu);
    }
    pthread_mutex_unlock(&p.mu);
    return out;
}

static job *ready_take(void) {
    job *j = NULL;
    unsigned slot = p.next_consume % POOL_BUFFERS;
    pthread_mutex_lock(&p.mu);
    /* Later frames may finish first. Only the next input ticket may enter PL,
       preserving motion history and source identity without inventing frames. */
    while (!p.ready[slot] && !p.failed && p.decode_live) pthread_cond_wait(&p.cv, &p.mu);
    if (!p.failed && p.ready[slot]) {
        if (p.ready[slot]->ticket != p.next_consume) {
            fprintf(stderr, "CAMERA_PL_FAIL out_of_order expected=%u actual=%u\n",
                    p.next_consume, p.ready[slot]->ticket);
            p.failed = 1; wake_all();
        } else {
            j = p.ready[slot]; p.ready[slot] = NULL; --p.ready_count; ++p.next_consume;
        }
    }
    pthread_mutex_unlock(&p.mu);
    return j;
}

static void *decode_worker(void *arg) {
    (void)arg;
    for (;;) {
        job *j = queue_take();
        if (!j) break;
        double started = now(), cpu_started = cpu_now();
        int rc = p.decode_job(j);
        j->decode_ms = (now() - started) * 1000.0;
        j->decode_cpu_ms = (cpu_now() - cpu_started) * 1000.0;
        pthread_mutex_lock(&p.mu);
        --p.decode_active;
        if (rc || p.failed) {
            p.pool_used[j->slot] = 0;
            p.failed = 1;
            wake_all();
            pthread_mutex_unlock(&p.mu);
            if (rc) fprintf(stderr, "JPEG_DECODE_FAIL sequence=%u\n", j->sequence);
            job_free(j);
            break;
        }
        p.ready[j->slot] = j;
        ++p.ready_count;
        wake_all();
        pthread_mutex_unlock(&p.mu);
    }
    pthread_mutex_lock(&p.mu);
    --p.decode_live;
    wake_all();
    pthread_mutex_unlock(&p.mu);
    return NULL;
}

static void *pl_worker(void *arg) {
    (void)arg;
    for (;;) {
        job *j = ready_take();
        if (!j) break;
        int rc = p.consume_job(j);
        pthread_mutex_lock(&p.mu);
        if (p.history_slot >= 0) p.pool_used[p.history_slot] = 0;
        p.history_slot = (int)j->slot;
        if (rc) p.failed = 1;
        wake_all();
        pthread_mutex_unlock(&p.mu);
        job_free(j);
        if (rc) break;
    }
    return NULL;
}

static void pipeline_join(int aborting) {
    pthread_mutex_lock(&p.mu);
    p.input_closed = 1;
    if (aborting) p.failed = 1;
    wake_all();
    pthread_mutex_unlock(&p.mu);
    for (unsigned i = 0; i < p.decode_started; ++i) pthread_join(p.decode_threads[i], NULL);
    p.decode_started = 0;
    if (p.pl_started) { pthread_join(p.pl_thread, NULL); p.pl_started = 0; }
    p.decode_live = 0;
    while (p.dec_count) {
        job_free(p.dec_q[p.dec_head]);
        p.dec_head = (p.dec_head + 1) % QUEUE_CAP; --p.dec_count;
    }
    for (unsigned i = 0; i < POOL_BUFFERS; ++i) {
        job_free(p.ready[i]); p.ready[i] = NULL; p.pool_used[i] = 0;
    }
    p.ready_count = 0; p.history_slot = -1;
}

static int pipeline_start(int (*decode_job)(job *), int (*consume_job)(job *)) {
    if (!p.decode_workers || p.decode_workers > MAX_DECODE_WORKERS) return -1;
    p.decode_job = decode_job; p.consume_job = consume_job;
    p.decode_live = p.decode_workers;
    for (unsigned i = 0; i < p.decode_workers; ++i) {
        if (pthread_create(&p.decode_threads[i], NULL, decode_worker, NULL)) {
            pipeline_join(1); return -1;
        }
        ++p.decode_started;
    }
    if (pthread_create(&p.pl_thread, NULL, pl_worker, NULL)) { pipeline_join(1); return -1; }
    p.pl_started = 1;
    return 0;
}

static int decode_job(job *j) { return decode_into(j->jpeg, j->len, j->rgb); }
static int consume_job(job *j) {
    return process_rgb(j->rgb, j->jpeg, j->len, j->ts_us, j->sequence, j->decode_ms, j->decode_cpu_ms);
}

static int pipeline_config(void) {
    const char *verify = getenv("VIGI_VERIFY"), *audit = getenv("VIGI_AUDIT_EVERY");
    const char *pipeline = getenv("VIGI_PIPELINE");
    const char *workers = getenv("VIGI_DECODE_WORKERS");
    const char *wait = getenv("VIGI_PL_WAIT");
    p.poll_yield = 1;
    if (wait) {
        if (!strcmp(wait, "spin")) p.poll_yield = 0;
        else if (strcmp(wait, "yield")) return -1;
    }
#ifdef VIGI_LANDMARK
    p.landmark_every = 0;
    const char *landmark = getenv("VIGI_LANDMARK_EVERY");
    if (landmark && *landmark) {
        char *end;
        if (strspn(landmark, "0123456789") != strlen(landmark)) return -1;
        unsigned long n = strtoul(landmark, &end, 10);
        if (*end || n > 120) return -1;
        p.landmark_every = (unsigned)n;
    }
#endif
    int verifying = verify && !strcmp(verify, "1");
    p.audit_every = verifying ? 1u : 30u;
    if (audit && !verifying) {
        char *end;
        if (!*audit || strspn(audit, "0123456789") != strlen(audit)) return -1;
        unsigned long n = strtoul(audit, &end, 10);
        if (*end || n > 1000) return -1;
        p.audit_every = (unsigned)n;
    }
    p.pipeline = !verifying;
    if (pipeline && !verifying) {
        if (!strcmp(pipeline, "0") || !strcmp(pipeline, "serial")) p.pipeline = 0;
        else if (!strcmp(pipeline, "1") || !strcmp(pipeline, "threaded")) p.pipeline = 1;
        else return -1;
    }
    p.decode_workers = p.pipeline ? MAX_DECODE_WORKERS : 1;
    if (workers && !verifying && p.pipeline) {
        if (!strcmp(workers, "1")) p.decode_workers = 1;
        else if (!strcmp(workers, "2")) p.decode_workers = 2;
        else return -1;
    }
    return 0;
}

int camera_pl_open_format(unsigned width, unsigned height, unsigned motion_threshold,
                          int yuyv,unsigned stride,int limited) {
    struct stat st;
    if (pipeline_config()) { fprintf(stderr, "PL_PROFILE_FAIL invalid pipeline/audit setting\n"); return -1; }
    if (vg_camera_geometry_init(&p.g,width,height) || motion_threshold > 255 ||
        (yuyv!=0&&yuyv!=1) || (limited!=0&&limited!=1) || (yuyv&&stride<width*2)) {
        fprintf(stderr, "PL_PROFILE_FAIL unsupported 3of8 geometry/input\n"); return -1;
    }
#ifndef VIGI_AUTONOMY
    if (width!=1280 || height!=720 || yuyv) {
        fputs("PL_PROFILE_FAIL alternate input requires local autonomy build\n",stderr);return -1;
    }
#endif
    p.input_yuyv=yuyv;p.input_stride=stride;p.limited=limited;
    if (reserved_profile()) {
        fprintf(stderr, "PL_PROFILE_FAIL reserved no-map RAM/profile absent; no registers touched\n"); return -1;
    }
    if (!lstat("/sys/bus/platform/devices/41e00000.dma/driver", &st)) {
        fprintf(stderr, "PL_PROFILE_FAIL DMA belongs to a kernel driver\n"); return -1;
    }
    p.lock = open("/tmp/vigilens_camera_pl.lock", O_CREAT | O_RDWR, 0600);
    if (p.lock < 0 || flock(p.lock, LOCK_EX | LOCK_NB)) return -1;
    p.mem = open("/dev/mem", O_RDWR | O_SYNC);
    if (p.mem < 0) return -1;
    p.ram = mmap(NULL, RAM_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, p.mem, RAM_PHYS);
    if (p.ram == MAP_FAILED) { p.ram = NULL; return -1; }
    p.dma = map_regs(0x41e00000); p.roi = map_regs(0x40040000);
    p.gray = map_regs(0x40030000); p.motion = map_regs(0x40020000);
    p.rgb = malloc(p.g.rgb_bytes); p.gold = malloc(GRAY_SIZE); p.prev = malloc(GRAY_SIZE);
    p.prev_gold = malloc(GRAY_SIZE); p.poison = malloc(GRAY_SIZE);
    p.prev_copy = malloc(p.g.rgb_bytes);
    if (!p.dma || !p.roi || !p.gray || !p.motion || !p.rgb || !p.gold || !p.prev ||
        !p.prev_gold || !p.poison || !p.prev_copy) return -1;
    memset(p.poison, POISON_BYTE, GRAY_SIZE);
    for (unsigned i = 0; i < POOL_BUFFERS; ++i) {
        p.pool[i] = malloc(p.g.rgb_bytes);
        if (!p.pool[i]) return -1;
#ifdef VIGI_AUTONOMY
        p.pool_gray[i]=malloc(GRAY_SIZE);
        if(!p.pool_gray[i])return -1;
#endif
    }
    p.threshold = motion_threshold;
    p.history_slot = -1;
    if (reset_dma()) return -1;
    wr(p.roi, 0x10, 0); wr(p.roi, 0x18, 0);
    wr(p.roi, 0x20, width); wr(p.roi, 0x28, height);
    wr(p.roi, 0x30, width); wr(p.roi, 0x38, height);
    wr(p.gray, 0x10, width); wr(p.gray, 0x18, height);
    wr(p.motion, 0x10, p.g.gray_width); wr(p.motion, 0x18, p.g.gray_height); wr(p.motion, 0x20, motion_threshold);
    p.base_fid = rd(p.roi, 0x60);

    snprintf(p.codec, sizeof(p.codec), "%s",
             getenv("VIGI_STREAM_CODEC") ? getenv("VIGI_STREAM_CODEC") : "raw");

    if (pthread_mutex_init(&p.mu, NULL)) return -1;
    if (pthread_cond_init(&p.cv, NULL)) { pthread_mutex_destroy(&p.mu); return -1; }
    p.sync_ready = 1;
#ifdef VIGI_AUTONOMY
    {
        const char *detector_dir = getenv("VIGI_DETECTOR_DIR");
        const char *model = getenv("VIGI_LANDMARK_MODEL");
        const char *jsonl = getenv("VIGI_CONTRACT_LOG");
        const char *latest = getenv("VIGI_CONTRACT_LATEST");
        if (!detector_dir || !model) {
            fprintf(stderr, "AUTONOMY_INIT_FAIL set VIGI_DETECTOR_DIR and VIGI_LANDMARK_MODEL\n");
            return -1;
        }
        p.autonomy = vg_camera_open(detector_dir, model,
            jsonl ? jsonl : "/tmp/vigilens_contract.jsonl",
            latest ? latest : "/tmp/vigilens_latest.json");
        if (!p.autonomy) { fprintf(stderr, "AUTONOMY_INIT_FAIL\n"); return -1; }
        puts("AUTONOMY_READY measurement=board_native no_pc_producer=1");
    }
#endif
#ifdef VIGI_LANDMARK
    if (p.landmark_every) {
        const char *model = getenv("VIGI_LANDMARK_MODEL");
        if (!model) model = "/tmp/mnn/peppa_pruned.mnn";
        if (vg_landmark_init(model, 2)) { fprintf(stderr, "LANDMARK_INIT_FAIL\n"); return -1; }
        p.landmark_buf[0] = malloc(p.g.rgb_bytes);
        p.landmark_buf[1] = malloc(p.g.rgb_bytes);
        if (!p.landmark_buf[0] || !p.landmark_buf[1]) return -1;
        pthread_mutex_init(&p.landmark_mu, NULL);
        pthread_cond_init(&p.landmark_cv, NULL);
        if (pthread_create(&p.landmark_thread, NULL, landmark_worker, NULL)) return -1;
        p.landmark_started = 1;
        printf("LANDMARK_READY every=%u model=%s async=1 auto=1\n", p.landmark_every, model);
    }
#endif

    printf("PL_READY profile=720p-3of8 ram=0x%08x bytes=%u fid0=%u motion_threshold=%u "
           "pipeline=%s audit_every=%u codec=%s decode_workers=%u pl_wait=%s\n",
           RAM_PHYS, RAM_SIZE, p.base_fid, motion_threshold,
           p.pipeline ? "threaded" : "serial", p.audit_every, p.codec, p.decode_workers,
           p.poll_yield ? "yield" : "spin");
    printf("CAMERA_GEOMETRY width=%u height=%u gray_width=%u gray_height=%u input=%s stride=%u limited=%d\n",
           width,height,p.g.gray_width,p.g.gray_height,yuyv?"yuyv":"mjpg",stride,limited);
    printf("JPEG_CONFIG method=%s fancy_upsampling=%u cpu_reference=%s\n",
           getenv("VIGI_JPEG_FAST") ? "IFAST" : "ISLOW", getenv("VIGI_JPEG_FAST") ? 0u : 1u,
           p.audit_every == 1 ? "every_frame" : "audit");
    /* Transport first: workers must never see a half-initialised socket state. */
    if (camera_stream_open()) return -1;
    if (p.pipeline && pipeline_start(decode_job, consume_job)) {
        fprintf(stderr, "PIPELINE_START_FAIL\n");
        camera_stream_close();
        return -1;
    }
    return 0;
}

int camera_pl_open(unsigned w,unsigned h,unsigned t) {return camera_pl_open_format(w,h,t,0,0,1);}

struct decode_error { struct jpeg_error_mgr pub; jmp_buf jump; };
static void decode_fail(j_common_ptr cinfo) {
    struct decode_error *e = (struct decode_error *)cinfo->err;
    (*cinfo->err->output_message)(cinfo);
    longjmp(e->jump, 1);
}
static int decode(const unsigned char *data, size_t length, unsigned char *out) {
    if(p.input_yuyv)return vg_yuyv_rgb(data,length,p.input_stride,out,p.g.width,p.g.height,p.limited);
    struct jpeg_decompress_struct d = {0};
    struct decode_error e;
    d.err = jpeg_std_error(&e.pub); e.pub.error_exit = decode_fail;
    if (setjmp(e.jump)) { jpeg_destroy_decompress(&d); return -1; }
    jpeg_create_decompress(&d);
    jpeg_mem_src(&d, data, length);
    if (jpeg_read_header(&d, TRUE) != JPEG_HEADER_OK || d.image_width != p.g.width || d.image_height != p.g.height) {
        jpeg_destroy_decompress(&d); return -1;
    }
    d.out_color_space = JCS_RGB;
    /* Explicit diagnostic option; RGB output may differ from accurate IDCT.
       PL and the software reference still consume the very same decoded RGB. */
    if (getenv("VIGI_JPEG_FAST")) {
        d.dct_method = JDCT_IFAST;
        d.do_fancy_upsampling = FALSE;
    }
    jpeg_start_decompress(&d);
    if (d.output_components != 3 || d.output_width != p.g.width || d.output_height != p.g.height) {
        jpeg_destroy_decompress(&d); return -1;
    }
    while (d.output_scanline < d.output_height) {
        JSAMPROW row = out + d.output_scanline * p.g.width * 3u;
        if (jpeg_read_scanlines(&d, &row, 1) != 1) { jpeg_destroy_decompress(&d); return -1; }
    }
    jpeg_finish_decompress(&d); jpeg_destroy_decompress(&d);
    return 0;
}

int camera_pl_frame(const unsigned char *jpeg, size_t length, uint64_t ts_us, unsigned sequence) {
    if (!p.pipeline) {
        double started = now(), cpu_started = cpu_now();
        if (decode(jpeg, length, p.rgb)) {
            fprintf(stderr, "JPEG_DECODE_FAIL sequence=%u\n", sequence);
            return -1;
        }
        return process_rgb(p.rgb, jpeg, length, ts_us, sequence, (now() - started) * 1000.0,
                           (cpu_now() - cpu_started) * 1000.0);
    }
    job *j = calloc(1, sizeof(*j));
    if (!j) return -1;
    j->jpeg = malloc(length);
    if (!j->jpeg) { free(j); return -1; }
    memcpy(j->jpeg, jpeg, length);
    j->len = length; j->ts_us = ts_us; j->sequence = sequence;
    j->ticket = p.submitted;
    if (queue_push(j)) { job_free(j); return -1; }
    ++p.submitted;
    return 0;
}

int camera_pl_finish(void) {
    if (p.pipeline) pipeline_join(0);
#ifdef VIGI_AUTONOMY
    if (p.autonomy && vg_camera_finish(p.autonomy, NULL)) p.failed = 1;
#endif
    if (p.pipeline) printf("DECODE_SUMMARY workers=%u frames=%u peak_active=%u\n",
                           p.decode_workers, p.frames, p.decode_peak);
    printf("AUDIT_SUMMARY frames=%u audits=%llu failures=%llu reference_ms_total=%llu\n",
           p.frames, p.audits, p.audit_failures, p.ref_ms);
    if (p.failed || p.audit_failures || (p.pipeline && p.frames != p.submitted)) {
        fprintf(stderr, "CAMERA_PL_FAIL incomplete_pipeline submitted=%u processed=%u\n", p.submitted, p.frames);
        return -1;
    }
    int rc = camera_stream_finish(p.frames);
    return rc || p.failed || p.audit_failures ? -1 : 0;
}

#ifdef VIGI_LANDMARK
/* Landmark inference runs on its own thread: a 30-90 ms synchronous call inside
 * process_rgb stalls the PL worker and drops frames (25.2 fps at 5 Hz measured).
 * Producers hand over a frame copy through a two-slot buffer and never block. */
static void *landmark_worker(void *unused) {
    (void)unused;
    for (;;) {
        int index;
        pthread_mutex_lock(&p.landmark_mu);
        while (!p.landmark_ready && !p.landmark_stop)
            pthread_cond_wait(&p.landmark_cv, &p.landmark_mu);
        if (p.landmark_stop) { pthread_mutex_unlock(&p.landmark_mu); break; }
        index = p.landmark_ready_index;
        p.landmark_ready = 0;
        pthread_mutex_unlock(&p.landmark_mu);
        {
            float values[143], points[136];
            int box[4];
            if (vg_landmark_run_auto(p.landmark_buf[index], p.g.width, p.g.height, points, box, values, 143) == 0)
                printf("LANDMARK frame=%llu ms=%.3f bbox=%d,%d,%d,%d p30=(%.1f,%.1f)\n",
                       p.landmark_runs, vg_landmark_last_ms(), box[0], box[1], box[2], box[3],
                       points[60], points[61]);
            else
                printf("LANDMARK_MISS frame=%llu ms=%.3f\n", p.landmark_runs, vg_landmark_last_ms());
            ++p.landmark_runs;
        }
    }
    return NULL;
}
static void landmark_submit(const unsigned char *rgb) {
    pthread_mutex_lock(&p.landmark_mu);
    if (!p.landmark_ready) {
        memcpy(p.landmark_buf[p.landmark_write], rgb, p.g.rgb_bytes);
        p.landmark_ready_index = p.landmark_write;
        p.landmark_ready = 1;
        p.landmark_write ^= 1;
        pthread_cond_signal(&p.landmark_cv);
    }
    pthread_mutex_unlock(&p.landmark_mu);
}
#endif

void camera_pl_close(void) {
#ifdef VIGI_LANDMARK
    if (p.landmark_started) {
        pthread_mutex_lock(&p.landmark_mu);
        p.landmark_stop = 1;
        pthread_cond_signal(&p.landmark_cv);
        pthread_mutex_unlock(&p.landmark_mu);
        pthread_join(p.landmark_thread, NULL);
        p.landmark_started = 0;
        free(p.landmark_buf[0]); free(p.landmark_buf[1]);
    }
    vg_landmark_close();
#endif
    if (p.sync_ready) {
        if (p.decode_started || p.pl_started) pipeline_join(1);
    }
#ifdef VIGI_AUTONOMY
    /* Lease release callbacks still need the pool mutex while vision drains. */
    vg_camera_destroy(p.autonomy);
    p.autonomy = NULL;
#endif
    if(p.sync_ready) {
        pthread_cond_destroy(&p.cv);pthread_mutex_destroy(&p.mu);p.sync_ready=0;
    }
    camera_stream_close();
    if (p.dma) { wr(p.dma, 0, 0); wr(p.dma, 0x30, 0); }
    if (p.ram) munmap(p.ram, RAM_SIZE);
    if (p.dma) munmap((void *)p.dma, 4096);
    if (p.roi) munmap((void *)p.roi, 4096);
    if (p.gray) munmap((void *)p.gray, 4096);
    if (p.motion) munmap((void *)p.motion, 4096);
    if (p.mem >= 0) close(p.mem);
    if (p.lock >= 0) close(p.lock);
    for (unsigned i = 0; i < POOL_BUFFERS; ++i) free(p.pool[i]);
#ifdef VIGI_AUTONOMY
    for(unsigned i=0;i<POOL_BUFFERS;++i)free(p.pool_gray[i]);
#endif
    free(p.rgb); free(p.gold); free(p.prev); free(p.prev_gold); free(p.poison); free(p.prev_copy);
}
