/* Bounded same-frame handoff. Only worker owns vision and output streams. */
#define _POSIX_C_SOURCE 200809L
#include "autonomy_camera.h"
#include "vision_runtime.h"
#ifdef VIGI_CAMERA_PREVIEW
#include "camera_preview.h"
#include "vg_vision_config.h"
#endif
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

#ifndef VG_ANALYSIS_SLOTS
#define VG_ANALYSIS_SLOTS 2
#endif
_Static_assert(VG_ANALYSIS_SLOTS >= 2 && VG_ANALYSIS_SLOTS <= 8, "bounded analysis queue");
enum { SLOTS = VG_ANALYSIS_SLOTS, JSON_CAPACITY = 8192 };
typedef struct {
    unsigned char *rgb;
    size_t capacity;
    int state; /* 0=free, 1=queued, 2=processing */
    uint64_t order;
    double enqueued_at;
    vg_vision_frame frame;
    void (*release)(void *);
    void *release_context;
} slot;
struct vg_camera {
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t worker;
    int initialized, init_error, stopping, joined;
    char *directory, *model, *jsonl_path, *latest_path;
    slot slots[SLOTS];
    vg_camera_stats stats;
    double first_capture, last_capture, analysis_start;
    uint64_t last_id;
};
static double monotonic(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + t.tv_nsec / 1e9;
}
static int clear_latest(vg_camera *c) {
    if (unlink(c->latest_path) && errno != ENOENT) return -EIO;
    return 0;
}
/* mkstemp is in the same directory: rename is atomic for local POSIX readers. */
static int publish(vg_camera *c, FILE *log, const char *json) {
    size_t n = strlen(json), len = strlen(c->latest_path) + 12;
    char *temp = malloc(len);
    FILE *latest = NULL;
    int fd, rc = -EIO;
    if (!temp) return -ENOMEM;
    snprintf(temp, len, "%s.tmp.XXXXXX", c->latest_path);
    fd = mkstemp(temp);
    if (fd < 0) goto done;
    latest = fdopen(fd, "w");
    if (!latest) { close(fd); goto done; }
    if (fwrite(json, 1, n, latest) != n || fputc('\n', latest) == EOF ||
        fflush(latest)) goto done;
    if (fclose(latest)) { latest = NULL; goto done; }
    latest = NULL;
    /* Write and flush evidence before making latest visible. */
    if (fwrite(json, 1, n, log) != n || fputc('\n', log) == EOF || fflush(log)) goto done;
    if (rename(temp, c->latest_path)) goto done;
    rc = 0;
done:
    if (latest) fclose(latest);
    unlink(temp);
    free(temp);
    return rc;
}
static void *run(void *arg) {
#ifdef __linux__
    (void)prctl(PR_SET_NAME,"vg_analysis",0,0,0);
#endif
    vg_camera *c = arg;
    vg_vision *v = NULL;
    FILE *log = NULL;
    int init_rc = clear_latest(c);
#ifdef VIGI_CAMERA_PREVIEW
    const char *preview = getenv("VIGI_PREVIEW_LATEST");
#if !VG_PREVIEW_CAMERA_MJPEG
    double preview_at=-INFINITY;
#endif
    if (preview && *preview) (void)unlink(preview);
#if VG_PREVIEW_ASYNC && !VG_PREVIEW_CAMERA_MJPEG
    vg_preview_async *preview_worker=NULL;
    if(!init_rc&&preview&&*preview) {
        preview_worker=vg_preview_async_open(preview,VG_PREVIEW_JPEG_QUALITY,VG_PREVIEW_WORKER_NICE);
        if(!preview_worker)init_rc=-ENOMEM;
    }
#endif
#endif
    if (!init_rc) { log = fopen(c->jsonl_path, "w"); if (!log) init_rc = -EIO; }
    if (!init_rc) { v = vg_vision_create(c->directory, c->model); if (!v) init_rc = -ENODEV; }
    pthread_mutex_lock(&c->mutex);
    c->init_error = init_rc;
    if (init_rc) c->stats.first_error = init_rc;
    c->initialized = 1;
    pthread_cond_broadcast(&c->ready);
    pthread_mutex_unlock(&c->mutex);
    if (!init_rc) for (;;) {
        slot *s = NULL;
        int i, rc, rejected;
        char json[JSON_CAPACITY] = {0};
        vg_vision_info info = {0};
        pthread_mutex_lock(&c->mutex);
        for (;;) {
            for (i = 0; i < SLOTS; ++i)
                if (c->slots[i].state == 1 && (!s || c->slots[i].order < s->order)) s = &c->slots[i];
            if (s || c->stopping) break;
            pthread_cond_wait(&c->ready, &c->mutex);
        }
        if (!s) { pthread_mutex_unlock(&c->mutex); break; }
        s->state = 2;
        if (!c->stats.processed) c->analysis_start = monotonic();
        pthread_mutex_unlock(&c->mutex);
        const double worker_started=monotonic();
        const double queue_ms=(worker_started-s->enqueued_at)*1000;
        double preview_ms=0,publish_ms=0;
        rc = vg_vision_update(v, &s->frame, json, sizeof(json), &info);
#if defined(VIGI_CAMERA_PREVIEW) && !VG_PREVIEW_CAMERA_MJPEG
        if (preview && *preview && s->frame.ts-preview_at>=VG_PREVIEW_INTERVAL_S) {
            preview_at=s->frame.ts;
            const double preview_started=monotonic();
#if VG_PREVIEW_ASYNC
            if(vg_preview_async_submit(preview_worker,s->frame.rgb,s->frame.width,s->frame.height))
#else
            if (vg_preview_publish(preview, s->frame.rgb,
                    s->frame.width, s->frame.height, VG_PREVIEW_JPEG_QUALITY))
#endif
                fputs("PREVIEW_PUBLISH_FAILED\n", stderr);
            preview_ms=(monotonic()-preview_started)*1000;
        }
#endif
        rejected = rc == -4 || (rc == -2 && !s->frame.motion_valid);
        if (!rc) {
            if (!memchr(json, 0, sizeof(json)) || !json[0] || strchr(json, '\n')) rc = -EPROTO;
            else { const double published=monotonic();rc=publish(c,log,json);publish_ms=(monotonic()-published)*1000; }
        }
        if (rc) {
            int cleanup = clear_latest(c);
            fprintf(stdout, "AUTONOMY_SAMPLE frame_id=%" PRIu64 " ts=%.9f rc=%d rejected=%d clear_rc=%d mean_luma=%.9f motion_q16=%u motion_valid=%d\n",
                s->frame.frame_id, s->frame.ts, rc, rejected, cleanup,
                s->frame.mean_luma, s->frame.motion_q16, s->frame.motion_valid);
            if (cleanup) { rejected = 0; rc = cleanup; }
        } else {
            /* Capture logs use stdout too. One shared FILE lock prevents a
             * redirected stderr fragment from splitting another frame's line. */
            fprintf(stdout, "AUTONOMY_SAMPLE frame_id=%" PRIu64 " ts=%.9f rc=0 detections=%d visible=%d detector_ms=%.3f landmark_ms=%.3f total_ms=%.3f detect_path=%d landmark_model_ms=%.3f detector_caller_cpu_ms=%.3f landmark_caller_cpu_ms=%.3f vital_hz=%.3f vital_reject=%s roi_pixels=%u vital_samples=%d vital_history_samples=%d vital_gate=%s raw_hr=%.6f raw_hr_conf=%.6f hr_filter=%s preview_ms=%.3f publish_ms=%.3f worker_ms=%.3f queue_ms=%.3f roi_sum_r=%u roi_sum_g=%u roi_sum_b=%u\n",
                s->frame.frame_id, s->frame.ts, info.detections, info.visible,
                info.detector_ms, info.landmark_ms, info.total_ms,info.detector_path,
                info.landmark_model_ms,info.detector_caller_cpu_ms,info.landmark_caller_cpu_ms,
                info.vital_rate_hz, info.vital_reject ? info.vital_reject : "-",info.roi_pixels,info.vital_samples,info.vital_history_samples,
                info.vital_gate ? info.vital_gate : "-",info.raw_hr,info.raw_hr_conf,
                info.hr_filter_reason ? info.hr_filter_reason : "-",preview_ms,publish_ms,(monotonic()-worker_started)*1000,queue_ms,
                info.roi_sums[0],info.roi_sums[1],info.roi_sums[2]);
        }
        pthread_mutex_lock(&c->mutex);
        ++c->stats.processed;
        if (!rc) ++c->stats.succeeded;
        else if (rejected) ++c->stats.rejected;
        else { ++c->stats.failed; if (!c->stats.first_error) c->stats.first_error = rc; }
        c->stats.analysis_seconds = monotonic() - c->analysis_start;
        void (*release)(void *)=s->release;
        void *release_context=s->release_context;
        s->release=NULL;s->release_context=NULL;s->state = 0;
        pthread_mutex_unlock(&c->mutex);
        if(release)release(release_context);
    }
    vg_vision_destroy(v);
#ifdef VIGI_CAMERA_PREVIEW
#if VG_PREVIEW_ASYNC && !VG_PREVIEW_CAMERA_MJPEG
    vg_preview_async_close(preview_worker);
#endif
    if (preview && *preview) (void)unlink(preview);
#endif
    if (log && fclose(log)) {
        clear_latest(c);
        pthread_mutex_lock(&c->mutex);
        ++c->stats.failed;
        if (!c->stats.first_error) c->stats.first_error = -EIO;
        pthread_mutex_unlock(&c->mutex);
    }
    return NULL;
}
vg_camera *vg_camera_open(const char *directory, const char *model,
                          const char *jsonl_path, const char *latest_path) {
    vg_camera *c;
    int rc;
    if (!directory || !model || !jsonl_path || !latest_path || !*jsonl_path ||
        !*latest_path || !strcmp(jsonl_path, latest_path) || !strcmp(jsonl_path, "-")) return NULL;
    c = calloc(1, sizeof(*c));
    if (!c) return NULL;
    c->directory = strdup(directory); c->model = strdup(model);
    c->jsonl_path = strdup(jsonl_path); c->latest_path = strdup(latest_path);
    if (!c->directory || !c->model || !c->jsonl_path || !c->latest_path) goto fail;
    if (pthread_mutex_init(&c->mutex, NULL)) goto fail;
    if (pthread_cond_init(&c->ready, NULL)) { pthread_mutex_destroy(&c->mutex); goto fail; }
    rc = pthread_create(&c->worker, NULL, run, c);
    if (rc) { pthread_cond_destroy(&c->ready); pthread_mutex_destroy(&c->mutex); goto fail; }
    pthread_mutex_lock(&c->mutex);
    while (!c->initialized) pthread_cond_wait(&c->ready, &c->mutex);
    rc = c->init_error;
    pthread_mutex_unlock(&c->mutex);
    if (rc) {
        fprintf(stderr, "AUTONOMY_INIT rc=%d\n", rc);
        vg_camera_destroy(c); return NULL;
    }
    return c;
fail:
    free(c->directory); free(c->model); free(c->jsonl_path); free(c->latest_path); free(c);
    return NULL;
}
static int submit(vg_camera *c, const unsigned char *rgb, int w, int h,
    const unsigned char *gray,int gray_w,int gray_h,double mean,uint32_t motion,int valid,
    uint64_t id,double ts,void (*release)(void *),void *context) {
    slot *s = NULL;
    size_t n;
    int i;
    if (!c || !rgb || w <= 0 || h <= 0 || w > 16384 || h > 16384 ||
        !isfinite(mean) || mean < 0 || mean > 1 || !isfinite(ts) || ts < 0 ||
        motion > 65536u || (valid != 0 && valid != 1)||
        (gray&&(!release||gray_w<=0||gray_h<=0))) return -EINVAL;
    n = (size_t)w * (size_t)h * 3;
    pthread_mutex_lock(&c->mutex);
    if (c->stopping || c->init_error) { pthread_mutex_unlock(&c->mutex); return -ESHUTDOWN; }
    if (c->stats.submitted && (id <= c->last_id || ts <= c->last_capture)) {
        pthread_mutex_unlock(&c->mutex); return -EINVAL;
    }
    if (!c->stats.submitted) c->first_capture = ts;
    c->last_capture = ts; c->last_id = id;
    ++c->stats.submitted;
    c->stats.capture_seconds = ts - c->first_capture;
    for (i = 0; i < SLOTS; ++i) if (!c->slots[i].state) { s = &c->slots[i]; break; }
    if (!s) { ++c->stats.skipped; pthread_mutex_unlock(&c->mutex); return 0; }
    if (!release && n > s->capacity) {
        unsigned char *p = realloc(s->rgb, n);
        if (!p) { pthread_mutex_unlock(&c->mutex); return -ENOMEM; }
        s->rgb = p; s->capacity = n;
    }
    if(!release)memcpy(s->rgb, rgb, n);
    s->frame = (vg_vision_frame){id,ts,w,h,release?rgb:s->rgb,mean,motion,valid,gray,gray_w,gray_h};
    s->release=release;s->release_context=context;
    s->order = c->stats.submitted; s->enqueued_at=monotonic(); s->state = 1;
    ++c->stats.accepted;
    pthread_cond_signal(&c->ready);
    pthread_mutex_unlock(&c->mutex);
    return 1;
}
int vg_camera_submit(vg_camera *c,const unsigned char *rgb,int w,int h,double mean,
                     uint32_t motion,int valid,uint64_t id,double ts) {
    return submit(c,rgb,w,h,NULL,0,0,mean,motion,valid,id,ts,NULL,NULL);
}
int vg_camera_submit_leased(vg_camera *c,const unsigned char *rgb,int w,int h,
    const unsigned char *gray,int gray_w,int gray_h,double mean,uint32_t motion,int valid,
    uint64_t id,double ts,void (*release)(void *),void *context) {
    if(!release)return -EINVAL;
    return submit(c,rgb,w,h,gray,gray_w,gray_h,mean,motion,valid,id,ts,release,context);
}
int vg_camera_finish(vg_camera *c, vg_camera_stats *stats) {
    if (!c) return -EINVAL;
    if (!c->joined) {
        pthread_mutex_lock(&c->mutex); c->stopping = 1;
        pthread_cond_broadcast(&c->ready); pthread_mutex_unlock(&c->mutex);
        pthread_join(c->worker, NULL); c->joined = 1;
        /* A run with a hard error must not finish advertising an earlier
         * success (even when another queued frame succeeded after the error). */
        if (c->stats.first_error && clear_latest(c))
            fprintf(stderr, "AUTONOMY_CLEANUP rc=%d path=%s\n", -EIO, c->latest_path);
        fprintf(stderr, "AUTONOMY_SUMMARY submitted=%" PRIu64 " accepted=%" PRIu64 " skipped=%" PRIu64
            " processed=%" PRIu64 " succeeded=%" PRIu64 " rejected=%" PRIu64 " failed=%" PRIu64
            " rc=%d camera_submit_fps=%.6f analysis_update_fps=%.6f\n",
            c->stats.submitted,c->stats.accepted,c->stats.skipped,c->stats.processed,
            c->stats.succeeded,c->stats.rejected,c->stats.failed,c->stats.first_error,
            c->stats.capture_seconds > 0 ? (double)(c->stats.submitted-1)/c->stats.capture_seconds : 0,
            c->stats.analysis_seconds > 0 ? c->stats.succeeded/c->stats.analysis_seconds : 0);
    }
    if (stats) *stats = c->stats;
    return c->init_error ? c->init_error : c->stats.first_error;
}
void vg_camera_destroy(vg_camera *c) {
    int i;
    if (!c) return;
    vg_camera_finish(c, NULL);
    for (i = 0; i < SLOTS; ++i) free(c->slots[i].rgb);
    pthread_cond_destroy(&c->ready); pthread_mutex_destroy(&c->mutex);
    free(c->directory); free(c->model); free(c->jsonl_path); free(c->latest_path); free(c);
}
