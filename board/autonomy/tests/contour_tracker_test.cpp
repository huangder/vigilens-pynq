/* Synthetic observability tests, not face accuracy/performance evidence. */
#include "point_tracker.hpp"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <random>

static std::vector<uint8_t> frame(int local_shift,bool black=false) {
    const int w=640,h=480;
    std::vector<uint8_t> rgb(static_cast<size_t>(w)*h*3);
    std::mt19937 rng(629108);
    for(int y=0;y<h;++y)for(int x=0;x<w;++x) {
        int value=int(rng()%256);
        // Landmark 57 lies on a horizontal edge/ramp. There is deliberately
        // no horizontal texture there; 2D optical flow is unobservable.
        if(std::abs(x-232)<=35&&std::abs(y-316)<=35)
            value=std::clamp(128+6*(y-316-local_shift),0,255);
        for(int c=0;c<3;++c)rgb[(static_cast<size_t>(y)*w+x)*3+c]=black?0:uint8_t(value);
    }
    return rgb;
}
int main() {
    const vg_track::Policy policy{3,3,12,1,18,2,.7f,48,.02f};
    float points[136];
    for(int i=0;i<68;++i){points[i*2]=100+(i%9)*44;points[i*2+1]=100+(i/9)*36;}
    auto rgb=frame(0);
    const auto before=vg_track::from_rgb(rgb.data(),640,480,3);
    vg_track::Result result;
    assert(!vg_track::update(before,before,points,policy,result));
    assert(vg_track::update_contours(before,before,points,policy,result));
    assert(result.contour_count>0);
    assert(result.rejected_index==-1&&result.rejected_stage==0);
    assert(std::fabs(result.points[114]-232)<.5&&std::fabs(result.points[115]-316)<.5);
    rgb=frame(4);
    const auto opened=vg_track::from_rgb(rgb.data(),640,480,3);
    assert(vg_track::update_contours(before,opened,points,policy,result));
    assert(result.contour_count>0);
    // Independent local deformation must be measured, not simply transported
    // with the nearly stationary global face transform.
    assert(std::fabs(result.points[115]-320)<.5);
    const float global_y=result.b*232+result.a*316+result.ty;
    assert(std::fabs(result.points[115]-global_y)>2);
    rgb=frame(0,true);
    const auto black=vg_track::from_rgb(rgb.data(),640,480,3);
    assert(!vg_track::update_contours(before,black,points,policy,result));
    assert(!vg_track::update_contours(black,black,points,policy,result));
    points[56*2]=points[58*2];points[56*2+1]=points[58*2+1];
    assert(!vg_track::update_contours(before,before,points,policy,result));
    std::puts("CONTOUR_TRACKER_PASS rank_deficient_edge=1 local_deformation_measured=4 black_rejected=1 flat_rejected=1 degenerate_contour_rejected=1");
}
