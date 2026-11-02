/* Experimental all-board ERT: current pixels produce every landmark observation.
 * A bounded detector worker supplies appearance/ROI anchors only. No historical
 * eye/mouth measurements are replayed, and a predictor is not a face detector. */
#include "vision_runtime.h"
#include "ert_landmark.hpp"
#include "ert_guard.hpp"
#include "track_box.hpp"
#include "face_detector.h"
#include "detect_policy.h"
#include "autonomy_engine.h"
#include "vision_roi.hpp"
#include "vg_config.h"
#include "vg_vital_config.h"
#include "vg_vision_config.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <thread>
#include <sys/resource.h>
#include <time.h>

namespace {
std::atomic<bool> active{false};
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point t){return std::chrono::duration<double,std::milli>(Clock::now()-t).count();}
double cpu_ms(){timespec t{};if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))return 0;return t.tv_sec*1000.+t.tv_nsec/1e6;}
struct Anchor {
    uint64_t id=0,epoch=0;double ts=0;int width=0,height=0;bool visible=false;
    std::array<int,4> box{};std::array<float,136> points{};vg_ert::Patch patch;
};
struct Job {Anchor source;std::vector<unsigned char> rgb;vg_fd_box hint{};bool hinted=false;};
class Detector {
    vg_fd *fd=nullptr;bool have_box=false;
    int width=0,height=0;vg_fd_box last{};
    vg_detect_state state{-1e300,-1e300,-1e300,0};
public:
    ~Detector(){if(fd)vg_fd_destroy(fd);}
    bool open(const char *directory){fd=vg_fd_create_ex(directory,VG_DETECTOR_THREADS,VG_NCNN_PACKING);return fd;}
    Anchor run(const Job& job,const vg_ert::Model& model) {
        Anchor out=job.source;const auto started=Clock::now();vg_fd_box b{};int count=0,path=0;
        if(width!=out.width||height!=out.height) {
            state={-1e300,-1e300,-1e300,0};have_box=false;width=out.width;height=out.height;
        }
        const vg_detect_policy policy{VG_REDETECT_SECONDS,VG_DEEP_DETECT_SECONDS};
        const bool hinted=job.hinted||(have_box&&width==out.width&&height==out.height);
        const auto hint=job.hinted?job.hint:last;
        if(hinted&&out.ts>=state.last_full_ts&&out.ts-state.last_full_ts<VG_TRACKER_REDETECT_SECONDS) {
            path=2;
            count=vg_fd_refine(fd,job.rgb.data(),out.width,out.height,out.width*3,&vg_exported_detector,&hint,&b,1);
        }
        if(!count) {
            auto p=vg_exported_detector;
            const int tier=vg_detect_tier(&state,&policy,out.ts);
            if(tier==VG_DETECT_FAST)p.min_face=vg_detect_fast_min_face(
                &state,VG_MIN_FACE_FAST,p.min_face,VG_FAST_SIDE_MARGIN);
            path=tier==VG_DETECT_DEEP?4:1;
            count=vg_fd_detect(fd,job.rgb.data(),out.width,out.height,out.width*3,&p,&b,1);
            state.last_full_ts=out.ts;
            if(tier==VG_DETECT_DEEP)state.last_deep_ts=out.ts;
            if(!count&&tier==VG_DETECT_FAST&&hinted&&p.min_face>vg_exported_detector.min_face&&
               vg_detect_loss_allowed(&state,&policy,out.ts)) {
                path=5;
                count=vg_fd_detect(fd,job.rgb.data(),out.width,out.height,out.width*3,&vg_exported_detector,&b,1);
                state.last_deep_ts=out.ts;
            }
        }
        if(count<0)throw std::runtime_error("ERT face detector inference failed");
        const double detect_ms=elapsed(started);const auto landmark_start=Clock::now();
        int geometry_failure=-1;
        if(count>0) {
            out.box={{b.x1,b.y1,b.x2,b.y2}};
            if(model.run({job.rgb.data(),out.width,out.height},out.box,out.points)) {
                geometry_failure=vg_ert::geometry_failure(out.points.data(),out.width,out.height);
                out.visible=!geometry_failure&&out.patch.make(job.rgb.data(),out.width,out.height,out.box,VG_ERT_PATCH_SIDE);
            }
        }
        // Detector confirmation and landmark validity are separate decisions.
        // A rejected landmark frame must not force another full PNet search
        // when RNet/ONet can still confirm the face in the next source image.
        // The retained box is only a search hint; it never makes out.visible true.
        have_box=count>0;
        if(have_box){state.last_face_ts=out.ts;state.last_face_side=std::max(b.x2-b.x1,b.y2-b.y1);last=b;}
        std::fprintf(stdout,"ERT_MODEL_FRAME frame_id=%llu ts=%.9f visible=%d detector_ms=%.3f landmark_ms=%.3f detections=%d geometry_failure=%d detector_path=%d\n",
            static_cast<unsigned long long>(out.id),out.ts,int(out.visible),detect_ms,elapsed(landmark_start),count,geometry_failure,path);
        return out;
    }
};
}
struct vg_vision {
    vg_autonomy *engine=nullptr;std::unique_ptr<vg_ert::Model> model;
    std::unique_ptr<Detector> detector;std::thread worker;std::mutex mutex;std::condition_variable condition;
    std::unique_ptr<Job> queued;std::unique_ptr<Anchor> ready;std::unique_ptr<Anchor> anchor;
    bool stopping=false,busy=false,synchronous=false,have_previous=false,started=false,worker_failed=false;
    uint64_t epoch=0,last_id=0;double last_ts=0,scheduled=-1e300;
    std::array<int,4> previous_box{};std::array<float,136> previous_points{};
    vg_ert::Patch previous_patch;
#if defined(VIGI_ERT_HYBRID)
    vg_track::Pyramid previous_image;
    uint64_t hybrid_inferences=0,hybrid_tracked=0;
#endif
    uint64_t submitted=0,completed=0,observations=0,visible_count=0,losses=0;
    ~vg_vision(){if(engine)vg_autonomy_destroy(engine);}
};
namespace {
void worker(vg_vision *v) {
    if(setpriority(PRIO_PROCESS,0,VG_TRACKER_WORKER_NICE)) {
        std::lock_guard<std::mutex> lock(v->mutex);v->worker_failed=true;return;
    }
    try {
        for(;;) {
            std::unique_ptr<Job> job;
            {std::unique_lock<std::mutex> lock(v->mutex);
                v->condition.wait(lock,[&]{return v->stopping||v->queued;});
                if(v->stopping)return;
                job=std::move(v->queued);v->busy=true;}
            auto out=std::make_unique<Anchor>(v->detector->run(*job,*v->model));
            {std::lock_guard<std::mutex> lock(v->mutex);v->busy=false;++v->completed;
                if(!v->stopping&&out->epoch==v->epoch)v->ready=std::move(out);}
        }
    } catch(const std::exception& e) {
        std::fprintf(stderr,"ERT_WORKER_FAIL %s\n",e.what());
        std::lock_guard<std::mutex> lock(v->mutex);
        v->worker_failed=true;v->busy=false;v->ready.reset();v->queued.reset();
    }
}
void invalidate(vg_vision *v) {
    v->anchor.reset();v->have_previous=false;v->scheduled=-1e300;
    std::lock_guard<std::mutex> lock(v->mutex);++v->epoch;v->ready.reset();v->queued.reset();
    vg_autonomy_invalidate(v->engine);
}
void schedule(vg_vision *v,const vg_vision_frame& f) {
    if(f.ts-v->scheduled<1./VG_TRACKER_MODEL_HZ)return;
    std::unique_lock<std::mutex> lock(v->mutex);
    if(v->queued||v->busy||v->stopping||v->worker_failed)return;
    auto job=std::make_unique<Job>();job->source.id=f.frame_id;job->source.ts=f.ts;
    job->source.width=f.width;job->source.height=f.height;job->source.epoch=v->epoch;
    job->hinted=v->have_previous;
    const auto& b=v->previous_box;job->hint={b[0],b[1],b[2],b[3],1.f};
    job->rgb.assign(f.rgb,f.rgb+static_cast<size_t>(f.width)*f.height*3);
    v->scheduled=f.ts;++v->submitted;
    if(v->synchronous) {
        lock.unlock();auto result=std::make_unique<Anchor>(v->detector->run(*job,*v->model));
        lock.lock();v->ready=std::move(result);++v->completed;
    } else {v->queued=std::move(job);v->condition.notify_one();}
}
void forehead(const vg_vision_frame& f,const float *p,vg_autonomy_frame& input) {
    int b[4];if(!vg_forehead_bounds(p,f.width,f.height,VG_FOREHEAD_INSET,VG_FOREHEAD_TOP,
        VG_FOREHEAD_BOTTOM,VG_FOREHEAD_MIN_PIXELS,b))return;
    uint64_t sums[3]={};
    for(int y=b[1];y<b[3];++y)for(int x=b[0];x<b[2];++x)for(int c=0;c<3;++c)
        sums[c]+=f.rgb[(static_cast<size_t>(y)*f.width+x)*3+c];
    for(auto value:sums)if(value>UINT32_MAX)return;
    for(int c=0;c<3;++c)input.sums[c]=sums[c];
    input.count=(b[2]-b[0])*(b[3]-b[1]);
}
}
extern "C" vg_vision *vg_vision_create(const char *directory,const char *path) {
    if(!directory||!path)return nullptr;
    bool expected=false;if(!active.compare_exchange_strong(expected,true))return nullptr;
    try {
        auto v=std::make_unique<vg_vision>();v->model=std::make_unique<vg_ert::Model>(path);
        v->detector=std::make_unique<Detector>();if(!v->detector->open(directory)){active=false;return nullptr;}
        v->engine=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,VG_POSE_REPROJ_MAX_FRAC,VG_VITAL_EVERY,1);
        if(!v->engine){active=false;return nullptr;}
        if(vg_autonomy_configure_hr_filter(v->engine,&vg_exported_hr_filter_config)){active=false;return nullptr;}
#if defined(VIGI_ERT_TEMPORAL)
        if(vg_autonomy_configure_temporal(v->engine,nullptr,1,1)){active=false;return nullptr;}
#endif
        v->synchronous=std::getenv("VIGI_ERT_SYNC")!=nullptr;
        if(!v->synchronous)v->worker=std::thread(worker,v.get());
        std::fprintf(stdout,"ERT_READY mode=%s config_sha256=%s accuracy_verified=0\n",
            v->synchronous?"offline_synchronous":"board_async",VG_VISION_CONFIG_SHA256);
        return v.release();
    } catch(const std::exception& e){std::fprintf(stderr,"ERT_INIT_FAIL %s\n",e.what());active=false;return nullptr;}
}
extern "C" void vg_vision_destroy(vg_vision *v) {
    if(!v)return;
    {std::lock_guard<std::mutex> lock(v->mutex);v->stopping=true;v->queued.reset();v->condition.notify_all();}
    if(v->worker.joinable())v->worker.join();
#if defined(VIGI_ERT_HYBRID)
    std::fprintf(stdout,"ERT_HYBRID_SUMMARY foreground_inferences=%llu accepted_lk_frames=%llu\n",
        static_cast<unsigned long long>(v->hybrid_inferences),static_cast<unsigned long long>(v->hybrid_tracked));
#endif
    std::fprintf(stdout,"ERT_SUMMARY observations=%llu visible=%llu lost=%llu model_submitted=%llu model_completed=%llu\n",
        static_cast<unsigned long long>(v->observations),static_cast<unsigned long long>(v->visible_count),
        static_cast<unsigned long long>(v->losses),static_cast<unsigned long long>(v->submitted),static_cast<unsigned long long>(v->completed));
    delete v;active=false;
}
extern "C" int vg_vision_update(vg_vision *v,const vg_vision_frame *f,char *json,size_t size,vg_vision_info *info) {
    if(json&&size)json[0]=0;
    if(info)*info={};
    if(!v||!f||!json||!size||!f->rgb||!std::isfinite(f->ts)||!f->motion_valid||
       f->width<32||f->height<32||f->width>4096||f->height>4096||
       (v->started&&(f->frame_id<=v->last_id||f->ts<=v->last_ts))) {
        if(v)invalidate(v);
        return -2;
    }
    const auto start=Clock::now();const double cpu_start=cpu_ms();vg_vision_info timing{};
    try {
        v->last_id=f->frame_id;v->last_ts=f->ts;v->started=true;
        if(v->anchor&&(v->anchor->width!=f->width||v->anchor->height!=f->height))invalidate(v);
        schedule(v,*f);
        std::unique_ptr<Anchor> ready;
        {std::lock_guard<std::mutex> lock(v->mutex);
            // A latched worker failure takes precedence over a completed older
            // success. Never reinstall a pending anchor after clearing failure.
            if(v->worker_failed){v->anchor.reset();v->have_previous=false;v->ready.reset();}
            else ready=std::move(v->ready);}
        bool corrected=false;
#if defined(VIGI_ERT_TEMPORAL)
        std::unique_ptr<Anchor> fallback_anchor;
        bool fallback_previous=false;
#endif
        if(ready) {
            if(ready->visible&&ready->ts<=f->ts&&f->ts-ready->ts<=VG_TRACKER_ANCHOR_MAX_AGE_S&&
                ready->width==f->width&&ready->height==f->height) {
#if defined(VIGI_ERT_TEMPORAL)
                // Install a positive correction transactionally. A source-frame
                // confirmation may fail validation in this current image; it
                // must not erase a still-live track before that validation.
                fallback_anchor=std::move(v->anchor);fallback_previous=v->have_previous;
#endif
                v->anchor=std::move(ready);corrected=true;v->have_previous=false;
            } else {v->anchor.reset();v->have_previous=false;}
        }
        if(v->anchor&&f->ts-v->anchor->ts>VG_TRACKER_ANCHOR_MAX_AGE_S) {
            v->anchor.reset();v->have_previous=false;
        }
        timing.detector_ms=elapsed(start);timing.detector_caller_cpu_ms=cpu_ms()-cpu_start;
        const auto landmark_start=Clock::now();const double landmark_cpu=cpu_ms();
        bool visible=false;std::array<float,136> points{};std::array<int,4> box{};double error=-1,model_ms=0;
        vg_ert::Patch observed_patch;
#if defined(VIGI_ERT_HYBRID)
        vg_track::Pyramid current_image;
        bool used_lk=false;
#endif
        const char *track_reject="no_anchor";
        int point_failure=-1;
        for(int attempt=0;attempt<2;++attempt) {
        if(v->anchor) {
            track_reject="prediction_failed";
            const auto& a=*v->anchor;
            const auto& seed=v->have_previous?v->previous_box:a.box;
            bool predicted=false;
#if defined(VIGI_ERT_HYBRID)
            current_image=vg_track::from_rgb(f->rgb,f->width,f->height,VG_TRACKER_LEVELS);
            const vg_track::Policy policy{VG_TRACKER_LEVELS,VG_TRACKER_RADIUS,VG_TRACKER_ITERATIONS,
                VG_TRACKER_MIN_EIGEN,VG_TRACKER_MAX_ERROR,VG_TRACKER_FB_ERROR,
                VG_TRACKER_MIN_FRACTION,VG_TRACKER_MAX_MOTION,VG_TRACKER_EPSILON};
            vg_track::Result tracked;
            if(v->have_previous&&!corrected&&vg_track::update(v->previous_image,current_image,
                v->previous_points.data(),policy,tracked)&&vg_ert::geometry(tracked.points.data(),f->width,f->height)) {
                points=tracked.points;predicted=true;used_lk=true;
            }
#endif
            if(!predicted) {
                const auto model_start=Clock::now();
                predicted=v->model->run({f->rgb,f->width,f->height},seed,points);
                model_ms=elapsed(model_start);
#if defined(VIGI_ERT_HYBRID)
                ++v->hybrid_inferences;
#endif
            }
            point_failure=predicted?vg_ert::geometry_failure(points.data(),f->width,f->height):-1;
            if(predicted&&!point_failure) {
                track_reject="appearance_or_transform";
                vg_ert::Transform transform;
                visible=vg_ert::fit(a.points.data(),points.data(),transform);
                // Match consecutive observed images while retaining the
                // independent detector anchor's age limit. A one-second-old
                // whole-face template rejects ordinary expression/pose changes.
                // The template advances only after actual current pixels pass;
                // it cannot extend detector confirmation or survive a negative.
                vg_ert::Transform appearance_transform=transform;
                if(visible&&v->have_previous)
                    visible=vg_ert::fit(v->previous_points.data(),points.data(),appearance_transform);
                const auto& patch=v->have_previous?v->previous_patch:a.patch;
                bool local_evidence=false;
#if defined(VIGI_ERT_HYBRID)
                // LK already requires the configured observed majority and
                // ALL 12 eye + 4 MAR points to pass current local texture,
                // photometric and forward/backward checks. This local pixel
                // evidence replaces the whole-box appearance check on LK
                // frames only. ERT recovery still uses the original guard.
                local_evidence=used_lk;
#endif
                visible=visible&&(local_evidence||patch.matches(f->rgb,f->width,f->height,appearance_transform,
                    VG_ERT_PATCH_MIN_STD,VG_ERT_PATCH_MAX_ERROR,error));
                if(visible&&v->have_previous) {
                    constexpr int ids[]={27,28,29,30,36,39,42,45};
                    for(int i:ids)if(std::hypot(points[2*i]-v->previous_points[2*i],
                        points[2*i+1]-v->previous_points[2*i+1])>VG_TRACKER_MAX_MOTION){visible=false;track_reject="point_motion";}
                }
                if(visible) {
                    vg_ert::Transform roi_transform=transform;
#if defined(VIGI_ERT_TRANSLATION_ROI)
                    // Experimental: detector controls ROI scale/rotation.
                    // Between confirmations only translate its source box,
                    // avoiding regression/ROI scale feedback. All 68 points
                    // and the appearance check still use current pixels.
                    visible=vg_ert::fit_translation(a.points.data(),points.data(),roi_transform);
#endif
                    vg_track::Result r;r.a=roi_transform.a;r.b=roi_transform.b;r.tx=roi_transform.tx;r.ty=roi_transform.ty;
                    box=vg_track::Quad::rectangle(a.box[0],a.box[1],a.box[2],a.box[3]).moved(r).bounds(f->width,f->height);
                    visible=visible&&box[2]>box[0]&&box[3]>box[1];
                    visible=visible&&observed_patch.make(f->rgb,f->width,f->height,box,VG_ERT_PATCH_SIDE);
                }
            } else if(predicted)track_reject="geometry";
        }
#if defined(VIGI_ERT_TEMPORAL)
        if(!visible&&corrected&&fallback_anchor&&fallback_previous&&
           fallback_anchor->width==f->width&&fallback_anchor->height==f->height&&
           fallback_anchor->ts<=f->ts&&f->ts-fallback_anchor->ts<=VG_TRACKER_ANCHOR_MAX_AGE_S) {
            std::fprintf(stdout,"ERT_CORRECTION_FALLBACK frame_id=%llu source_id=%llu reason=%s geometry_failure=%d\n",
                static_cast<unsigned long long>(f->frame_id),static_cast<unsigned long long>(v->anchor->id),track_reject,point_failure);
            v->anchor=std::move(fallback_anchor);v->have_previous=true;corrected=false;
            // The next attempt observes this frame through the ordinary LK/ERT
            // and pixel guards. No previous measurements are emitted directly.
            used_lk=false;continue;
        }
#endif
        break;
        }
        timing.landmark_ms=elapsed(landmark_start);timing.landmark_model_ms=model_ms;
        timing.landmark_caller_cpu_ms=cpu_ms()-landmark_cpu;timing.detector_path=visible?(corrected?9:8):0;
#if defined(VIGI_ERT_TEMPORAL)
        // A detector result confirms its source image. If it cannot establish
        // a usable face in the current image on arrival, discard that result.
        // Re-running ERT against the same never-validated anchor on every frame
        // wastes the foreground budget until a later detector result arrives.
        // Existing usable anchors retain their normal current-image recovery.
        if(corrected&&!visible) {
            std::fprintf(stdout,"ERT_CURRENT_ANCHOR_REJECT frame_id=%llu source_id=%llu\n",
                static_cast<unsigned long long>(f->frame_id),static_cast<unsigned long long>(v->anchor->id));
            v->anchor.reset();
        }
#endif
        vg_autonomy_frame input{};input.frame_id=f->frame_id;input.ts=f->ts;input.width=f->width;input.height=f->height;
        input.pose_force_cold=corrected;
        input.mean_luma=f->mean_luma;input.motion_q16=f->motion_q16;input.motion_valid=f->motion_valid;
        if(visible) {
            input.visible=1;input.bbox[0]=box[0];input.bbox[1]=box[1];input.bbox[2]=box[2]-box[0];input.bbox[3]=box[3]-box[1];
            for(int i=0;i<68;++i)for(int c=0;c<2;++c)input.points[i][c]=points[2*i+c];
            forehead(*f,points.data(),input);
        }
        vg_autonomy_result output{};int rc=vg_autonomy_update(v->engine,&input,&output);
        if(rc==-4) {
            track_reject="pose_failed";
            input.visible=0;input.count=0;std::memset(input.bbox,0,sizeof(input.bbox));
            std::memset(input.points,0,sizeof(input.points));std::memset(input.sums,0,sizeof(input.sums));
            visible=false;rc=vg_autonomy_update(v->engine,&input,&output);
        }
#if defined(VIGI_ERT_HYBRID)
        if(visible){v->previous_image=std::move(current_image);if(used_lk){++v->hybrid_tracked;timing.detector_path=10;}}
#endif
        v->have_previous=visible;
        if(visible){v->previous_box=box;v->previous_points=points;v->previous_patch=std::move(observed_patch);++v->visible_count;}else ++v->losses;
        ++v->observations;if(!rc)rc=vg_autonomy_line(json,size,&output);
        if(rc){json[0]=0;invalidate(v);}
        timing.visible=visible;timing.detections=visible;timing.total_ms=elapsed(start);
        timing.vital_rate_hz=vg_autonomy_vital_rate_hz(v->engine);timing.vital_reject=vg_autonomy_vital_reject(v->engine);
        timing.roi_pixels=input.count;timing.vital_samples=vg_autonomy_vital_samples(v->engine);
        timing.vital_gate=vg_autonomy_vital_gate(v->engine);
        timing.raw_hr=vg_autonomy_raw_hr(v->engine);timing.raw_hr_conf=vg_autonomy_raw_hr_conf(v->engine);
        timing.hr_filter_reason=vg_autonomy_hr_filter_reason(v->engine);
        if(!visible)std::fprintf(stdout,"ERT_TRACK_REJECT frame_id=%llu reason=%s geometry_failure=%d corrected=%d\n",
            static_cast<unsigned long long>(f->frame_id),track_reject,point_failure,int(corrected));
        if(std::getenv("VIGI_ERT_TRACE"))std::fprintf(stdout,"ERT_OBSERVATION frame_id=%llu visible=%d patch_error=%.6f anchor_age=%.6f\n",
            static_cast<unsigned long long>(f->frame_id),int(visible),error,v->anchor?f->ts-v->anchor->ts:-1.);
        if(info)*info=timing;
        return rc;
    } catch(const std::exception& e) {
        std::fprintf(stderr,"ERT_UPDATE_FAIL %s\n",e.what());invalidate(v);return -1;
    }
}
