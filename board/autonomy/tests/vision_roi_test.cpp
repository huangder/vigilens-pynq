#include "vision_roi.hpp"
#include <cassert>
#include <cstdio>
#include <limits>
int main() {
    float p[136]={0};
    for(int i=17;i<27;++i){p[2*i]=60+(i-17)*10;p[2*i+1]=100;}
    for(int i=36;i<48;++i){p[2*i]=80+(i-36)*4;p[2*i+1]=120;}
    int box[4]={-1,-1,-1,-1};
    assert(vg_forehead_bounds(p,640,480,0.08,0.65,0.1,8,box));
    assert(box[0]>=0&&box[2]<=640&&box[1]>=0&&box[3]<=480);
    assert(vg_forehead_bounds(p,640,480,0.08,1e300,0.1,8,box));
    assert(box[1]==0); /* finite out-of-image bounds clip before int conversion */
    p[35]=std::numeric_limits<float>::quiet_NaN();
    assert(!vg_forehead_bounds(p,640,480,0.08,0.65,0.1,8,box));
    p[35]=-std::numeric_limits<float>::max();
    assert(!vg_forehead_bounds(p,640,480,0.08,1e300,0.1,8,box));
    puts("VISION_ROI_TEST_PASS");
}
