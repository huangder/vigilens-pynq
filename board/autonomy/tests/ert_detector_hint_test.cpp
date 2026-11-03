/* Real ERT + stub detector integration regression: landmark rejection must not
 * discard a detector-confirmed search hint. Synthetic pixels, no accuracy claim. */
#include "vision_runtime.h"
#include "face_detector.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <vector>
struct vg_fd {};
static int full_calls=0,refine_calls=0;
static bool confirm=true;
static float last_min_face=0;
extern "C" vg_fd *vg_fd_create_ex(const char*,int,int){return new vg_fd;}
extern "C" void vg_fd_destroy(vg_fd *fd){delete fd;}
extern "C" int vg_fd_detect(vg_fd*,const uint8_t*,int w,int h,int,const vg_fd_params *params,vg_fd_box *b,int) {
    ++full_calls;
    last_min_face=params->min_face;
    if(!confirm)return 0;
    // A valid thin half-open ROI at the bottom edge forces invalid landmark
    // topology/visibility, even though this stub confirms the candidate box.
    *b={4,h-1,w-4,h,1.f};return 1;
}
extern "C" int vg_fd_refine(vg_fd*,const uint8_t*,int,int,int,const vg_fd_params*,
                             const vg_fd_box *hint,vg_fd_box *b,int) {
    ++refine_calls;
    if(!confirm)return 0;
    *b=*hint;return 1;
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    assert(setenv("VIGI_ERT_SYNC","1",1)==0);
    auto *v=vg_vision_create("stub",argv[1]);assert(v);
    std::vector<unsigned char> rgb(64*64*3,128);char json[8192];
    auto observe=[&](unsigned id) {
        vg_vision_frame frame{ id,1.1*id,64,64,rgb.data(),.5,0,1,nullptr,0,0 };
        vg_vision_info info{};assert(vg_vision_update(v,&frame,json,sizeof(json),&info)==0);
        assert(!info.visible); // A retained hint cannot turn rejection into a measurement.
    };
    observe(1);assert(full_calls==1&&refine_calls==0);
    observe(2);assert(full_calls==1&&refine_calls==1);
    confirm=false;observe(3);assert(full_calls>1&&refine_calls==2);
    const int previous_refines=refine_calls;observe(4);assert(refine_calls==previous_refines);
    vg_vision_destroy(v);
    // Cold search is wide once. Without any retained hint, retry the wider
    // search at the configured loss-refresh interval rather than waiting 30s.
    v=vg_vision_create("stub",argv[1]);assert(v);
    full_calls=0;refine_calls=0;
    observe(1);assert(full_calls==1&&last_min_face==40);
    observe(2);assert(full_calls==2&&last_min_face==80);
    observe(6);assert(full_calls==4&&last_min_face==40);
    observe(7);assert(full_calls==5&&last_min_face==80);
    observe(11);assert(full_calls==7&&last_min_face==40);
    observe(12);assert(full_calls==8&&last_min_face==80);
    vg_vision_destroy(v);
    std::puts("ERT_DETECTOR_HINT_PASS retained_after_landmark_rejection cleared_after_detector_loss no_false_visible=1");
}
