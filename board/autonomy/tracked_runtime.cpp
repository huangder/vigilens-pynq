/* Full-rate image observations with an independently scheduled model worker.
 * No borrowed pixels, no PC producer, no repeated old landmark measurements.
 * Late model anchors are re-observed in the current image before use. */
#include "vision_runtime.h"
#include "point_tracker.hpp"
#include "track_history.hpp"
#include "track_box.hpp"
#include "face_detector.h"
#include "landmark_runner.h"
#include "autonomy_engine.h"
#include "vision_roi.hpp"
#include "vg_config.h"
#include "vg_vital_config.h"
#include "vg_vision_config.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <time.h>
#include <sys/resource.h>

namespace {
std::atomic<bool> active{false};
using Clock=std::chrono::steady_clock;
double elapsed(Clock::time_point t) {
    return std::chrono::duration<double,std::milli>(Clock::now()-t).count();
}
double caller_cpu_ms() {
    struct timespec t{};
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))return 0;
    return t.tv_sec*1000.0+t.tv_nsec/1000000.0;
}
const vg_track::Policy tracking{VG_TRACKER_LEVELS,VG_TRACKER_RADIUS,VG_TRACKER_ITERATIONS,
    VG_TRACKER_MIN_EIGEN,VG_TRACKER_MAX_ERROR,VG_TRACKER_FB_ERROR,
    VG_TRACKER_MIN_FRACTION,VG_TRACKER_MAX_MOTION,VG_TRACKER_EPSILON};
#if defined(VIGI_CONTOUR_TRACKER)
constexpr auto track_update=vg_track::update_contours;
constexpr const char *track_method="contour_experiment";
#else
constexpr auto track_update=vg_track::update;
constexpr const char *track_method="lk";
#endif
struct Anchor {
    uint64_t id=0;double ts=0,model_ts=0;
    std::shared_ptr<const vg_track::Pyramid> image;
    std::array<float,136> points{};
    vg_fd_box box{};
    vg_track::Quad corners;
    bool visible=false;
};
struct Job {
    Anchor source;
    std::vector<uint8_t> rgb;
    vg_fd_box hint{};
    bool have_hint=false;
};
bool geometry(const float *p) {
    for(int i=0;i<136;++i)if(!std::isfinite(p[i]))return false;
    return p[72]<p[78]&&p[84]<p[90]&&p[96]<p[108]&&p[103]<p[115]&&
        p[0]<p[16]&&p[16]<p[32]&&p[61]>(p[73]+p[85])/2&&p[61]<p[17];
}
vg_fd_box box_for(const vg_track::Quad& corners,float score,int w,int h) {
    const auto b=corners.bounds(w,h);
    return {b[0],b[1],b[2],b[3],score};
}
void forehead(const vg_vision_frame& f,const float *points,vg_autonomy_frame& out) {
    int box[4];
    if(!vg_forehead_bounds(points,f.width,f.height,VG_FOREHEAD_INSET,VG_FOREHEAD_TOP,
                          VG_FOREHEAD_BOTTOM,VG_FOREHEAD_MIN_PIXELS,box))return;
    uint64_t sums[3]={};
    for(int y=box[1];y<box[3];++y)for(int x=box[0];x<box[2];++x)
        for(int c=0;c<3;++c)sums[c]+=f.rgb[(static_cast<size_t>(y)*f.width+x)*3+c];
    for(int c=0;c<3;++c)if(sums[c]>UINT32_MAX)return;
    for(int c=0;c<3;++c)out.sums[c]=static_cast<uint32_t>(sums[c]);
    out.count=static_cast<uint32_t>((box[2]-box[0])*(box[3]-box[1]));
}
class Models {
    vg_fd *detector=nullptr;
    double searched=-1e300,last_face=-1e300;
    bool ever_seen=false;
    vg_fd_box last_box{};
    int last_width=0,last_height=0;
    bool have_box=false;
public:
    ~Models(){if(detector)vg_fd_destroy(detector);vg_landmark_close();}
    bool open(const std::string& directory,const std::string& model) {
        detector=vg_fd_create_ex(directory.c_str(),VG_DETECTOR_THREADS,VG_NCNN_PACKING);
        return detector&&vg_landmark_init(model.c_str(),VG_LANDMARK_THREADS)==0;
    }
    Anchor run(const Job& job) {
        Anchor out=job.source;
        const int w=out.image->source_width,h=out.image->source_height;
        std::array<vg_fd_box,1> boxes{};
        int count=0;auto started=Clock::now();
        const bool hinted=job.have_hint||(have_box&&last_width==w&&last_height==h);
        const vg_fd_box hint=job.have_hint?job.hint:last_box;
        if(hinted&&out.ts>=searched&&out.ts-searched<VG_TRACKER_REDETECT_SECONDS)
            count=vg_fd_refine(detector,job.rgb.data(),w,h,w*3,&vg_exported_detector,&hint,boxes.data(),1);
        if(count==0) {
            vg_fd_params p=vg_exported_detector;
            if(!ever_seen||out.ts-last_face<VG_DEEP_DETECT_SECONDS) {
                const double side=hinted?std::max(hint.x2-hint.x1,hint.y2-hint.y1):0;
                p.min_face=side>0 ? static_cast<float>(std::clamp(side*VG_FAST_SIDE_MARGIN,
                    double(vg_exported_detector.min_face),double(VG_MIN_FACE_FAST))) : VG_MIN_FACE_FAST;
            }
            count=vg_fd_detect(detector,job.rgb.data(),w,h,w*3,&p,boxes.data(),1);
            searched=out.ts;
            if(count==0&&p.min_face>vg_exported_detector.min_face)
                count=vg_fd_detect(detector,job.rgb.data(),w,h,w*3,&vg_exported_detector,boxes.data(),1);
        }
        const double detect_ms=elapsed(started);
        started=Clock::now();
        if(count>0) {
            float values[143];const auto& b=boxes[0];const int box[]={b.x1,b.y1,b.x2,b.y2};
            if(!vg_landmark_run_box(job.rgb.data(),w,h,box,VG_CROP_SCALE,out.points.data(),values,143)&&
               geometry(out.points.data())) {
                out.visible=true;out.box=b;ever_seen=true;last_face=out.ts;
                out.corners=vg_track::Quad::rectangle(b.x1,b.y1,b.x2,b.y2);
                out.model_ts=out.ts;
                last_box=b;last_width=w;last_height=h;have_box=true;
            }
        }
        if(!out.visible)have_box=false;
        std::fprintf(stdout,"TRACK_MODEL_FRAME frame_id=%llu ts=%.9f visible=%d detector_ms=%.3f landmark_ms=%.3f\n",
            static_cast<unsigned long long>(out.id),out.ts,int(out.visible),detect_ms,elapsed(started));
        return out;
    }
};
}
struct vg_vision {
    std::string directory,model;
    vg_autonomy *engine=nullptr;
    std::thread worker;
    std::mutex mutex;
    std::condition_variable condition;
    std::unique_ptr<Job> queued;
    std::unique_ptr<Anchor> ready;
    std::unique_ptr<Models> synchronous_models;
    bool stopping=false,initialized=false,model_ok=false,model_busy=false;
    bool synchronous=false,have_previous=false;
    Anchor previous;
    vg_track::History history{VG_TRACKER_ANCHOR_MAX_AGE_S,VG_TRACKER_HISTORY_FRAMES};
    double scheduled=-1e300;
    uint64_t model_submitted=0,model_completed=0,observations=0,tracked=0,corrected=0,lost=0;
};
namespace {
bool advance(vg_vision *v,Anchor& anchor,bool background) {
    for(;;) {
    std::vector<vg_track::Sample> frames;
    {
        std::lock_guard<std::mutex> lock(v->mutex);
        if(v->stopping||!v->history.since(anchor.id,anchor.ts,frames))return false;
    }
    // Only the background worker may replay a backlog. The live path observes
    // at most one new frame, so a late result cannot create a long foreground
    // stall and silently turn into skipped camera analyses.
    if(frames.empty())return true;
    if(!background&&frames.size()>1)return false;
    vg_track::Result step;
    for(const auto& next:frames) {
        if(next.ts-anchor.model_ts>VG_TRACKER_ANCHOR_MAX_AGE_S||
           !track_update(*anchor.image,*next.image,anchor.points.data(),tracking,step)||
           !geometry(step.points.data()))return false;
        anchor.points=step.points;
        anchor.corners=anchor.corners.moved(step);
        anchor.box=box_for(anchor.corners,anchor.box.score,next.image->source_width,next.image->source_height);
        anchor.id=next.id;anchor.ts=next.ts;anchor.image=next.image;
    }
    if(!background)return true;
    }
}
void worker(vg_vision *v) {
    Models models;
    const bool ok=setpriority(PRIO_PROCESS,0,VG_TRACKER_WORKER_NICE)==0&&models.open(v->directory,v->model);
    {
        std::lock_guard<std::mutex> lock(v->mutex);
        v->initialized=true;v->model_ok=ok;v->condition.notify_all();
    }
    if(!ok)return;
    for(;;) {
        std::unique_ptr<Job> job;
        {
            std::unique_lock<std::mutex> lock(v->mutex);
            v->condition.wait(lock,[&]{return v->stopping||v->queued;});
            if(v->stopping)break;
            job=std::move(v->queued);v->model_busy=true;
        }
        auto result=std::make_unique<Anchor>(models.run(*job));
        const auto source_id=result->id;
        const bool bridged=result->visible&&advance(v,*result,true);
        result->visible=bridged;
        std::fprintf(stdout,"TRACK_MODEL_BRIDGE source_id=%llu current_id=%llu accepted=%d\n",
            static_cast<unsigned long long>(source_id),static_cast<unsigned long long>(result->id),int(bridged));
        {
            std::lock_guard<std::mutex> lock(v->mutex);
            ++v->model_completed;v->model_busy=false;
            if(!v->stopping)v->ready=std::move(result);
        }
    }
}
void schedule(vg_vision *v,const vg_vision_frame& f,const std::shared_ptr<vg_track::Pyramid>& image,
              bool visible,const vg_fd_box& box) {
    if(f.ts-v->scheduled<1.0/VG_TRACKER_MODEL_HZ)return;
    std::unique_lock<std::mutex> lock(v->mutex);
    if(v->queued||v->model_busy||v->stopping)return;
    auto job=std::make_unique<Job>();
    job->source.id=f.frame_id;job->source.ts=f.ts;job->source.image=image;
    job->have_hint=visible;job->hint=box;
    job->rgb.assign(f.rgb,f.rgb+static_cast<size_t>(f.width)*f.height*3);
    v->scheduled=f.ts;++v->model_submitted;
    if(v->synchronous) {
        lock.unlock();
        auto result=std::make_unique<Anchor>(v->synchronous_models->run(*job));
        lock.lock();v->ready=std::move(result);++v->model_completed;
    } else {v->queued=std::move(job);v->condition.notify_one();}
}
}
extern "C" vg_vision *vg_vision_create(const char *directory,const char *model) {
    if(!directory||!model)return nullptr;
    bool expected=false;
    if(!active.compare_exchange_strong(expected,true))return nullptr;
    auto v=std::make_unique<vg_vision>();v->directory=directory;v->model=model;
    v->engine=vg_autonomy_create(&vg_exported_config,&vg_exported_vital_config,VG_POSE_REPROJ_MAX_FRAC,VG_VITAL_EVERY,1);
    if(!v->engine){active=false;return nullptr;}
    if(vg_autonomy_configure_hr_filter(v->engine,&vg_exported_hr_filter_config)){active=false;return nullptr;}
    v->synchronous=std::getenv("VIGI_TRACKER_SYNC")!=nullptr;
    if(v->synchronous) {
        v->synchronous_models=std::make_unique<Models>();
        v->model_ok=v->synchronous_models->open(v->directory,v->model);
    } else {
        try {v->worker=std::thread(worker,v.get());}
        catch(...) {vg_autonomy_destroy(v->engine);active=false;return nullptr;}
        std::unique_lock<std::mutex> lock(v->mutex);
        v->condition.wait(lock,[&]{return v->initialized;});
    }
    if(!v->model_ok){vg_vision_destroy(v.release());return nullptr;}
    std::fprintf(stdout,"TRACKER_READY mode=%s model_hz=%.3f config_sha256=%s method=%s\n",
        v->synchronous?"offline_synchronous":"board_async",double(VG_TRACKER_MODEL_HZ),VG_VISION_CONFIG_SHA256,track_method);
    return v.release();
}
extern "C" void vg_vision_destroy(vg_vision *v) {
    if(!v)return;
    {
        std::lock_guard<std::mutex> lock(v->mutex);v->stopping=true;v->queued.reset();v->condition.notify_all();
    }
    if(v->worker.joinable())v->worker.join();
    std::fprintf(stdout,"TRACKER_SUMMARY observations=%llu tracked=%llu corrected=%llu lost=%llu model_submitted=%llu model_completed=%llu\n",
        static_cast<unsigned long long>(v->observations),static_cast<unsigned long long>(v->tracked),
        static_cast<unsigned long long>(v->corrected),static_cast<unsigned long long>(v->lost),
        static_cast<unsigned long long>(v->model_submitted),static_cast<unsigned long long>(v->model_completed));
    vg_autonomy_destroy(v->engine);delete v;active=false;
}
extern "C" int vg_vision_update(vg_vision *v,const vg_vision_frame *f,char *json,size_t size,vg_vision_info *info) {
    if(json&&size)json[0]=0;
    if(info)*info=vg_vision_info{};
    if(!v||!f||!json||!size||!f->rgb||f->width<32||f->height<32||
       f->width>4096||f->height>4096||!std::isfinite(f->ts)||!f->motion_valid) {
        if(v){v->have_previous=false;vg_autonomy_invalidate(v->engine);
            std::lock_guard<std::mutex> lock(v->mutex);v->history.clear();v->ready.reset();}
        return -2;
    }
    auto started=Clock::now();const double cpu_started=caller_cpu_ms();vg_vision_info timing{};
    std::unique_ptr<Anchor> correction;
    {std::lock_guard<std::mutex> lock(v->mutex);correction=std::move(v->ready);}
    auto image=std::make_shared<vg_track::Pyramid>(
        vg_track::from_rgb(f->rgb,f->width,f->height,VG_TRACKER_LEVELS));
    if(image->images.empty()){vg_autonomy_invalidate(v->engine);v->have_previous=false;return -1;}
    {
        std::lock_guard<std::mutex> lock(v->mutex);
        if(!v->history.push({f->frame_id,f->ts,image})) {
            v->have_previous=false;v->ready.reset();vg_autonomy_invalidate(v->engine);return -2;
        }
    }
    vg_track::Result result;std::array<float,136> points{};vg_fd_box box{};
    vg_track::Quad corners;bool visible=false;
    if(v->have_previous&&f->ts>v->previous.ts&&
       /* A failed frame is never emitted as a face. A retained image can
        * nevertheless be re-observed after a brief loss, under the SAME age,
        * displacement and forward/backward checks as a late model anchor.
        * rPPG's separate gap gate still clears its measurement window. */
       f->ts-v->previous.model_ts<=VG_TRACKER_ANCHOR_MAX_AGE_S&&
       track_update(*v->previous.image,*image,v->previous.points.data(),tracking,result)&&geometry(result.points.data())) {
        points=result.points;corners=v->previous.corners.moved(result);
        box=box_for(corners,v->previous.box.score,f->width,f->height);visible=true;
        timing.detector_path=6;++v->tracked;
    }
    schedule(v,*f,image,visible,box);
    {
        std::lock_guard<std::mutex> lock(v->mutex);
        if(v->ready)correction=std::move(v->ready);
    }
    if(correction&&correction->visible&&f->ts>=correction->ts&&
       f->ts-correction->ts<=VG_TRACKER_ANCHOR_MAX_AGE_S&&
       correction->image->source_width==f->width&&correction->image->source_height==f->height) {
        bool accepted=false;
        if(correction->id==f->frame_id) {points=correction->points;box=correction->box;corners=correction->corners;accepted=true;}
        else if(advance(v,*correction,false)) {
            points=correction->points;box=correction->box;corners=correction->corners;accepted=true;
        }
        if(accepted){visible=true;timing.detector_path=7;++v->corrected;}
        else if(std::getenv("VIGI_TRACKER_TRACE"))std::fprintf(stdout,
            "TRACK_ANCHOR_REJECT frame_id=%llu source_id=%llu age_s=%.6f count=%d index=%d stage=%d\n",
            static_cast<unsigned long long>(f->frame_id),static_cast<unsigned long long>(correction->id),
            f->ts-correction->ts,result.count,result.rejected_index,result.rejected_stage);
    }
    visible=visible&&box.x2>box.x1&&box.y2>box.y1;
    timing.detector_ms=elapsed(started);timing.detector_caller_cpu_ms=caller_cpu_ms()-cpu_started;
    timing.visible=int(visible);timing.detections=int(visible);
    vg_autonomy_frame input{};
    input.frame_id=f->frame_id;input.ts=f->ts;input.width=f->width;input.height=f->height;
    input.mean_luma=f->mean_luma;input.motion_q16=f->motion_q16;input.motion_valid=f->motion_valid;
    if(visible) {
        input.visible=1;input.bbox[0]=box.x1;input.bbox[1]=box.y1;input.bbox[2]=box.x2-box.x1;input.bbox[3]=box.y2-box.y1;
        for(int i=0;i<68;++i)for(int c=0;c<2;++c)input.points[i][c]=points[i*2+c];
        forehead(*f,points.data(),input);
        v->previous.id=f->frame_id;v->previous.ts=f->ts;v->previous.image=image;v->previous.points=points;v->previous.box=box;
        v->previous.corners=corners;
        if(timing.detector_path==7)v->previous.model_ts=correction->model_ts;
        v->have_previous=true;
    } else {
        ++v->lost;
        if(std::getenv("VIGI_TRACKER_TRACE"))std::fprintf(stdout,
            "TRACK_LOSS frame_id=%llu gap=%.6f count=%d index=%d stage=%d\n",
            static_cast<unsigned long long>(f->frame_id),f->ts-v->previous.ts,
            result.count,result.rejected_index,result.rejected_stage);
    }
    ++v->observations;
    vg_autonomy_result output{};int rc=vg_autonomy_update(v->engine,&input,&output);
    if(rc==-4) {
        // An unsolved pose invalidates this face observation. Still publish a
        // current, explicitly unavailable frame rather than freeze old data.
        input.visible=0;input.count=0;
        std::memset(input.bbox,0,sizeof(input.bbox));std::memset(input.sums,0,sizeof(input.sums));
        std::memset(input.points,0,sizeof(input.points));
        v->have_previous=false;timing.visible=0;
        rc=vg_autonomy_update(v->engine,&input,&output);
    }
    if(!rc)rc=vg_autonomy_line(json,size,&output);
    if(rc){json[0]=0;v->have_previous=false;vg_autonomy_invalidate(v->engine);}
    timing.vital_rate_hz=vg_autonomy_vital_rate_hz(v->engine);
    timing.vital_reject=vg_autonomy_vital_reject(v->engine);timing.total_ms=elapsed(started);
    timing.raw_hr=vg_autonomy_raw_hr(v->engine);timing.raw_hr_conf=vg_autonomy_raw_hr_conf(v->engine);
    timing.hr_filter_reason=vg_autonomy_hr_filter_reason(v->engine);
    if(info)*info=timing;
    return rc;
}
