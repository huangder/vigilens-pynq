/* Model-dependent lifecycle regression, local fixture only. Including the
 * implementation permits deterministic synchronization with actual worker
 * completion and scheduling, without sleeps that guess whether inference ended
 * or adding production-only test hooks. No camera/accuracy/performance claim. */
#include "../ert_runtime.cpp"
#include <functional>

struct vg_fd {};
namespace {
struct Control {
    std::mutex mutex;std::condition_variable condition;
    bool blocked=false,fail=false;unsigned entered=0;
    vg_fd_box box{};
} control;
void require(bool ok,const char *message){if(!ok)throw std::runtime_error(message);}
template<class Predicate> void await(Predicate ready) {
    const auto deadline=Clock::now()+std::chrono::seconds(10);
    while(!ready()) {
        require(Clock::now()<deadline,"worker synchronization timed out");
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}
void block(bool value) {
    std::lock_guard<std::mutex> lock(control.mutex);control.blocked=value;control.condition.notify_all();
}
void wait_entries(unsigned count) {
    await([&]{std::lock_guard<std::mutex> lock(control.mutex);return control.entered>=count;});
}
int detection(vg_fd_box *box) {
    std::unique_lock<std::mutex> lock(control.mutex);++control.entered;
    control.condition.wait(lock,[]{return !control.blocked;});
    if(control.fail)return -2;
    *box=control.box;return 1;
}
void ready(vg_vision *v) {
    await([&]{std::lock_guard<std::mutex> lock(v->mutex);return bool(v->ready);});
    std::lock_guard<std::mutex> lock(v->mutex);
    require(v->ready->visible,"fixture must produce a valid detector/ERT anchor");
}
void reset_control() {
    std::lock_guard<std::mutex> lock(control.mutex);
    control.blocked=false;control.fail=false;control.entered=0;
}
}
extern "C" vg_fd *vg_fd_create_ex(const char*,int,int){return new vg_fd;}
extern "C" void vg_fd_destroy(vg_fd *fd){delete fd;}
extern "C" int vg_fd_detect(vg_fd*,const uint8_t*,int,int,int,const vg_fd_params*,vg_fd_box *b,int){return detection(b);}
extern "C" int vg_fd_refine(vg_fd*,const uint8_t*,int,int,int,const vg_fd_params*,const vg_fd_box*,vg_fd_box *b,int){return detection(b);}

int main(int argc,char **argv) {
    if(argc!=3)return 2;
    try {
        require(unsetenv("VIGI_ERT_SYNC")==0,"unset synchronous mode failed");
        FILE *input=std::fopen(argv[2],"rb");require(input,"local fixture unavailable");
        char line[512];require(std::fgets(line,sizeof(line),input),"missing fixture header");
        unsigned long long id;double ts;int w,h,x,y,bw,bh;
        require(std::sscanf(line,"%llu %lf %d %d %d %d %d %d",&id,&ts,&w,&h,&x,&y,&bw,&bh)==8&&
            w>0&&h>0&&w<=4096&&h<=4096,"invalid fixture header");
        std::vector<unsigned char> rgb(size_t(w)*h*3);
        require(std::fread(rgb.data(),1,rgb.size(),input)==rgb.size(),"truncated fixture");std::fclose(input);
        control.box={x,y,x+bw,y+bh,1};
        auto frame=[&](unsigned fid,double time){return vg_vision_frame{fid,time,w,h,rgb.data(),.5,0,1,nullptr,0,0};};
        auto observe=[&](vg_vision *v,unsigned fid,double time,bool visible) {
            char json[8192];vg_vision_info info{};const auto f=frame(fid,time);
            require(vg_vision_update(v,&f,json,sizeof(json),&info)==0,"observation failed");
            require(bool(info.visible)==visible,"unexpected face visibility");
            if(!visible)require(std::strstr(json,"\"hr_bpm\":null"),"lost face must clear vital output");
        };

        // The old completed result is still pending when the next job fails.
        // Calling the real scheduler here fixes the interleaving that can occur
        // after scheduling and before the public update drains ready results.
        reset_control();block(true);auto *v=vg_vision_create("stub",argv[1]);require(v,"create failed");
        observe(v,1,.1,false);block(false);ready(v);
        {std::lock_guard<std::mutex> lock(control.mutex);control.fail=true;}
        schedule(v,frame(2,1.2));
        await([&]{std::lock_guard<std::mutex> lock(v->mutex);return v->worker_failed;});
        observe(v,3,1.3,false);vg_vision_destroy(v);
        std::puts("ERT_LIFECYCLE_CASE worker_failure_discards_pending_success PASS");

        // A busy worker cannot keep the last confirmation alive past its age.
        reset_control();block(true);v=vg_vision_create("stub",argv[1]);require(v,"create failed");
        observe(v,1,.1,false);block(false);ready(v);observe(v,2,.2,true);
        block(true);observe(v,3,1.2,true);wait_entries(2);
        const double expired=1.2+VG_TRACKER_ANCHOR_MAX_AGE_S+.1;
        observe(v,4,expired,false);block(false);ready(v);
        block(true);observe(v,5,expired+.1,false);wait_entries(3);
        block(false);ready(v);observe(v,6,expired+.2,true);vg_vision_destroy(v);
        std::puts("ERT_LIFECYCLE_CASE expired_anchor_rejected_fresh_observation_recovers PASS");

        // An invalid frame changes the epoch while the detector owns its source
        // copy. Its late successful result must never be accepted afterward.
        reset_control();block(true);v=vg_vision_create("stub",argv[1]);require(v,"create failed");
        observe(v,1,.1,false);wait_entries(1);
        auto invalid=frame(2,.2);invalid.motion_valid=0;char json[8192];
        require(vg_vision_update(v,&invalid,json,sizeof(json),nullptr)==-2&&json[0]==0,"invalid frame must reject and clear output");
        block(false);
        await([&]{std::lock_guard<std::mutex> lock(v->mutex);return v->completed==1;});
        {std::lock_guard<std::mutex> lock(v->mutex);require(!v->ready,"old epoch revived");}
        block(true);observe(v,3,.3,false);wait_entries(2);
        block(false);ready(v);observe(v,4,.4,true);vg_vision_destroy(v);
        std::puts("ERT_LIFECYCLE_CASE invalid_epoch_discards_late_result_and_recovers PASS");
#if defined(VIGI_ERT_TEMPORAL)
        // A source-positive result that arrives after the subject has left
        // must not trigger full ERT repeatedly against the empty current image.
        reset_control();block(true);v=vg_vision_create("stub",argv[1]);require(v,"create failed");
        observe(v,1,.1,false);block(false);ready(v);
        auto original=rgb;std::fill(rgb.begin(),rgb.end(),0);
        observe(v,2,.2,false);require(!v->anchor,"unusable arriving anchor retained");
        const auto predictions=v->hybrid_inferences;
        observe(v,3,.3,false);require(v->hybrid_inferences==predictions,"invalid anchor caused repeated foreground prediction");
        rgb=std::move(original);block(true);observe(v,4,1.3,false);wait_entries(2);
        block(false);ready(v);observe(v,5,1.4,true);vg_vision_destroy(v);
        std::puts("ERT_LIFECYCLE_CASE unusable_arriving_anchor_discarded_and_recovers PASS");
        // A source-positive correction with unusable current appearance must
        // not erase a good, unexpired track. The fallback must observe pixels.
        reset_control();block(true);v=vg_vision_create("stub",argv[1]);require(v,"create failed");
        observe(v,1,.1,false);block(false);ready(v);observe(v,2,.2,true);
        const auto original_id=v->anchor->id;
        {std::lock_guard<std::mutex> lock(v->mutex);
            v->ready=std::make_unique<Anchor>(*v->anchor);v->ready->id=77;
            std::fill(v->ready->patch.values.begin(),v->ready->patch.values.end(),0.);}
        observe(v,3,.3,true);
        require(v->anchor&&v->anchor->id==original_id,"failed correction replaced usable track");
        require(v->hybrid_tracked>0,"fallback did not validate current pixels with LK");
        // Explicit detector-negative must still clear the track immediately.
        {std::lock_guard<std::mutex> lock(v->mutex);
            v->ready=std::make_unique<Anchor>(*v->anchor);v->ready->visible=false;}
        observe(v,4,.4,false);require(!v->anchor,"negative detector result ignored");
        vg_vision_destroy(v);
        std::puts("ERT_LIFECYCLE_CASE failed_correction_uses_current_track_negative_still_clears PASS");
        std::puts("ERT_LIFECYCLE_PASS cases=5 local_only=1 accuracy_verified=0");
#else
        std::puts("ERT_LIFECYCLE_PASS cases=3 local_only=1 accuracy_verified=0");
#endif
        return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr,"ERT_LIFECYCLE_FAIL %s\n",error.what());return 1;
    }
}
