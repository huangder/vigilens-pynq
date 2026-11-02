#ifndef VG_TRACK_BOX_HPP
#define VG_TRACK_BOX_HPP
#include "point_tracker.hpp"
#include <algorithm>
#include <cmath>

namespace vg_track {
/* Keep the four observed box corners in floating point. Repeatedly rotating
 * an integer axis-aligned bound expands it even when motion later reverses.
 * Only conversion to the detector/contract's half-open box rounds/clips. */
struct Quad {
    std::array<float,8> p{};
    static Quad rectangle(int x0,int y0,int x1,int y1) {
        return {{{float(x0),float(y0),float(x1),float(y0),
                  float(x0),float(y1),float(x1),float(y1)}}};
    }
    Quad moved(const Result& r)const {
        Quad out;
        for(int i=0;i<4;++i) {
            out.p[i*2]=r.a*p[i*2]-r.b*p[i*2+1]+r.tx;
            out.p[i*2+1]=r.b*p[i*2]+r.a*p[i*2+1]+r.ty;
        }
        return out;
    }
    std::array<int,4> bounds(int width,int height)const {
        if(width<=0||height<=0)return {};
        for(float value:p)if(!std::isfinite(value))return {};
        float left=p[0],right=p[0],top=p[1],bottom=p[1];
        for(int i=1;i<4;++i) {
            left=std::min(left,p[i*2]);right=std::max(right,p[i*2]);
            top=std::min(top,p[i*2+1]);bottom=std::max(bottom,p[i*2+1]);
        }
        return {{int(std::floor(std::clamp(left,0.f,float(width)))),
                 int(std::floor(std::clamp(top,0.f,float(height)))),
                 int(std::ceil(std::clamp(right,0.f,float(width)))),
                 int(std::ceil(std::clamp(bottom,0.f,float(height))))}};
    }
};
}
#endif
