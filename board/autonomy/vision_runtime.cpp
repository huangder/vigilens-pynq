/* Same-frame production composition. No Python, network or camera ownership. */
#include "vision_runtime.h"
#include "detect_policy.h"
#include "face_detector.h"
#include "landmark_runner.h"
#include "autonomy_engine.h"
#include "vg_config.h"
#include "vg_vital_config.h"
#include "vg_vision_config.h"
#include "vision_roi.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <vector>
#include <time.h>
#include <sys/resource.h>

struct vg_vision {
    vg_fd *detector = nullptr;
    vg_autonomy *engine = nullptr;
    std::vector<vg_fd_box> boxes;
    vg_fd_box previous{};
    /* Finite "never" sentinel: -1e300 is a valid double for the policy guard. */
    vg_detect_state detect{-1e300, -1e300, -1e300, 0};
    int previous_width = 0, previous_height = 0;
    bool have_previous = false;
};
namespace {
bool active = false;
using Clock = std::chrono::steady_clock;
double elapsed(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
double caller_cpu_ms() {
    struct timespec time;
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&time))return 0;
    return time.tv_sec*1000.0+time.tv_nsec/1000000.0;
}
bool geometry(const float *p) {
    for (int i = 0; i < 136; ++i) if (!std::isfinite(p[i])) return false;
    /* iBUG ordering, not a recognition threshold or accuracy test. */
    return p[72] < p[78] && p[84] < p[90] &&
        p[96] < p[108] && p[103] < p[115] &&
        p[0] < p[16] && p[16] < p[32] &&
        p[61] > (p[73] + p[85]) / 2 && p[61] < p[17];
}
void forehead(const unsigned char *rgb, const float *p, int w, int h,
               vg_autonomy_frame *out) {
    int box[4];
    if(!vg_forehead_bounds(p,w,h,VG_FOREHEAD_INSET,VG_FOREHEAD_TOP,
                           VG_FOREHEAD_BOTTOM,VG_FOREHEAD_MIN_PIXELS,box))return;
    const int x0=box[0],y0=box[1],x1=box[2],y1=box[3];
    uint64_t sums[3] = {0,0,0};
    for (int y = y0; y < y1; ++y) for (int x = x0; x < x1; ++x)
        for (int c = 0; c < 3; ++c) sums[c] += rgb[(static_cast<size_t>(y)*w+x)*3+c];
    for (int c = 0; c < 3; ++c) if (sums[c] > UINT32_MAX) return;
    for (int c = 0; c < 3; ++c) out->sums[c] = static_cast<uint32_t>(sums[c]);
    out->count = static_cast<uint32_t>((x1-x0)*(y1-y0));
}
}
extern "C" vg_vision *vg_vision_create(const char *directory, const char *model) {
    if (active || !directory || !model) return nullptr;
    /* Linux nice is per-thread. Called by the dedicated inference worker before
       creating runtimes so capture/decode/PL keep their own scheduling weight. */
    if(setpriority(PRIO_PROCESS,0,VG_ANALYSIS_NICE)) {
        std::perror("VISION_SCHED_FAIL");
        return nullptr;
    }
    std::unique_ptr<vg_vision> v(new vg_vision);
    v->detector = vg_fd_create_ex(directory, VG_DETECTOR_THREADS, VG_NCNN_PACKING);
    if (!v->detector) return nullptr;
    if (vg_landmark_init(model, VG_LANDMARK_THREADS)) { vg_fd_destroy(v->detector); return nullptr; }
    v->engine = vg_autonomy_create(&vg_exported_config, &vg_exported_vital_config,
                                   VG_POSE_REPROJ_MAX_FRAC, VG_VITAL_EVERY, 1);
    if (!v->engine) { vg_fd_destroy(v->detector); vg_landmark_close(); return nullptr; }
    if(vg_autonomy_configure_hr_filter(v->engine,&vg_exported_hr_filter_config)) { vg_fd_destroy(v->detector); vg_landmark_close(); vg_autonomy_destroy(v->engine);return nullptr; }
    v->boxes.resize(vg_exported_detector.max_candidates);
    active = true;
    std::fprintf(stderr,"VISION_CONFIG detector_threads=%d landmark_threads=%d ncnn_packing=%d analysis_nice=%d config_sha256=%s\n",
        VG_DETECTOR_THREADS,VG_LANDMARK_THREADS,VG_NCNN_PACKING,getpriority(PRIO_PROCESS,0),VG_VISION_CONFIG_SHA256);
    return v.release();
}
extern "C" void vg_vision_destroy(vg_vision *v) {
    if (!v) return;
    vg_fd_destroy(v->detector);
    vg_autonomy_destroy(v->engine);
    vg_landmark_close();
    delete v;
    active = false;
}
extern "C" int vg_vision_update(vg_vision *v, const vg_vision_frame *f,
                                char *json, size_t capacity, vg_vision_info *info) {
    if (json && capacity) json[0] = 0;
    if (info) *info=vg_vision_info{};
    if (!v || !f || !f->rgb || !json || !capacity || f->width <= 0 || f->height <= 0 ||
        f->width > 16384 || f->height > 16384 || !std::isfinite(f->ts)) {
        if(v) { vg_autonomy_invalidate(v->engine); v->have_previous=false; }
        return -1;
    }
    if (!f->motion_valid) {
        vg_autonomy_invalidate(v->engine);
        v->have_previous=false;
        return -2;
    }
    vg_vision_info timing{};
    auto started = Clock::now();
    const double detector_cpu_started=caller_cpu_ms();
    timing.detections = 0;
    const vg_detect_policy policy{VG_REDETECT_SECONDS,VG_DEEP_DETECT_SECONDS};
    if (v->have_previous && v->previous_width == f->width && v->previous_height == f->height &&
        f->ts >= v->detect.last_full_ts && f->ts-v->detect.last_full_ts < VG_REDETECT_SECONDS) {
        timing.detector_path=2;
        timing.detections=vg_fd_refine(v->detector,f->rgb,f->width,f->height,f->width*3,
            &vg_exported_detector,&v->previous,v->boxes.data(),static_cast<int>(v->boxes.size()));
    }
    if (timing.detections == 0) {
        /* Same trigger as before: every frame the refine rejects, plus one
           refresh per VG_REDETECT_SECONDS. Only the search width changes. */
        vg_fd_params params=vg_exported_detector;
        int tier=vg_detect_tier(&v->detect,&policy,f->ts);
        if (tier==VG_DETECT_FAST)
            params.min_face=static_cast<float>(vg_detect_fast_min_face(
                &v->detect,VG_MIN_FACE_FAST,vg_exported_detector.min_face,VG_FAST_SIDE_MARGIN));
        timing.detector_path=timing.detector_path==2 ? 3 : (tier==VG_DETECT_DEEP ? 4 : 1);
        timing.detections = vg_fd_detect(v->detector, f->rgb, f->width, f->height,
            f->width*3, &params, v->boxes.data(), static_cast<int>(v->boxes.size()));
        v->detect.last_full_ts=f->ts;
        if (tier==VG_DETECT_DEEP) v->detect.last_deep_ts=f->ts;
        /* Losing a tracked box to a narrow search is a loss event: widen now
           instead of waiting deep_seconds blind. Same deep clock, so a face
           that keeps failing still costs at most one wide search per period. */
        if (timing.detections==0 && tier==VG_DETECT_FAST && v->have_previous &&
            vg_detect_loss_allowed(&v->detect,&policy,f->ts)) {
            params=vg_exported_detector;
            timing.detector_path=5;
            timing.detections = vg_fd_detect(v->detector, f->rgb, f->width, f->height,
                f->width*3, &params, v->boxes.data(), static_cast<int>(v->boxes.size()));
            v->detect.last_deep_ts=f->ts;
        }
    }
    timing.detector_ms = elapsed(started);
    timing.detector_caller_cpu_ms=caller_cpu_ms()-detector_cpu_started;
    if (timing.detections < 0) {
        v->have_previous=false;
        vg_autonomy_invalidate(v->engine);
        if(info)*info=timing;
        return -10;
    }
    vg_autonomy_frame input{};
    v->have_previous=timing.detections>0;
    if (v->have_previous) {
        v->previous=v->boxes[0];
        v->previous_width=f->width; v->previous_height=f->height;
        v->detect.last_face_ts=f->ts;
        v->detect.last_face_side=std::max(v->boxes[0].x2-v->boxes[0].x1,
                                          v->boxes[0].y2-v->boxes[0].y1);
    }
    input.frame_id=f->frame_id; input.ts=f->ts; input.width=f->width; input.height=f->height;
    input.mean_luma=f->mean_luma; input.motion_q16=f->motion_q16; input.motion_valid=f->motion_valid;
    if (timing.detections > 0) {
        const vg_fd_box &b = v->boxes[0];
        const int box[] = {b.x1,b.y1,b.x2,b.y2};
        float points[136], values[143];
        auto landmark_started = Clock::now();
        const double landmark_cpu_started=caller_cpu_ms();
        int rc=vg_landmark_run_box(f->rgb,f->width,f->height,box,VG_CROP_SCALE,points,values,143);
        timing.landmark_ms=elapsed(landmark_started);
        timing.landmark_caller_cpu_ms=caller_cpu_ms()-landmark_cpu_started;
        timing.landmark_model_ms=rc==0 ? vg_landmark_last_ms() : 0;
        if (rc == 0 && geometry(points)) {
            input.visible=1;
            input.bbox[0]=b.x1; input.bbox[1]=b.y1;
            input.bbox[2]=b.x2-b.x1; input.bbox[3]=b.y2-b.y1;
            for(int i=0;i<68;++i) for(int c=0;c<2;++c) input.points[i][c]=points[2*i+c];
            forehead(f->rgb,points,f->width,f->height,&input);
            timing.visible=1;
        }
    }
    vg_autonomy_result result;
    int rc=vg_autonomy_update(v->engine,&input,&result);
    timing.vital_rate_hz=vg_autonomy_vital_rate_hz(v->engine);
    timing.vital_reject=vg_autonomy_vital_reject(v->engine);
    timing.raw_hr=vg_autonomy_raw_hr(v->engine);timing.raw_hr_conf=vg_autonomy_raw_hr_conf(v->engine);
    timing.hr_filter_reason=vg_autonomy_hr_filter_reason(v->engine);
    if (!rc) rc=vg_autonomy_line(json,capacity,&result);
    if(rc) { json[0]=0; vg_autonomy_invalidate(v->engine); }
    timing.total_ms=elapsed(started);
    if(info)*info=timing;
    return rc;
}
