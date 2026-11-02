#ifndef VG_VISION_ROI_HPP
#define VG_VISION_ROI_HPP
#include <algorithm>
#include <cmath>
/* Pure geometry helper. Clamp as double BEFORE integer conversion; malformed
 * points/config cannot invoke float-to-int undefined behavior. */
inline bool vg_forehead_bounds(const float *p,int w,int h,double inset_fraction,
                               double top,double bottom,int minimum,int *box) {
    if(!p||!box||w<=0||h<=0||minimum<=0||!std::isfinite(inset_fraction)||
       inset_fraction<0||inset_fraction>=0.5||!std::isfinite(top)||
       !std::isfinite(bottom)||!(top>bottom)||bottom<0) return false;
    for(int i=17;i<48;++i) if(!std::isfinite(p[2*i])||!std::isfinite(p[2*i+1]))return false;
    double brow=p[35],eye=p[73],left=p[34],right=p[34];
    for(int i=17;i<27;++i) {
        brow=std::min(brow,static_cast<double>(p[2*i+1]));
        left=std::min(left,static_cast<double>(p[2*i]));
        right=std::max(right,static_cast<double>(p[2*i]));
    }
    for(int i=36;i<48;++i)eye=std::max(eye,static_cast<double>(p[2*i+1]));
    const double gap=eye-brow,dx=(right-left)*inset_fraction;
    if(!(gap>0))return false;
    const double values[]={left+dx,brow-top*gap,right-dx,brow-bottom*gap};
    int result[4];
    for(int i=0;i<4;++i) {
        if(!std::isfinite(values[i]))return false;
        result[i]=static_cast<int>(std::clamp(std::nearbyint(values[i]),0.0,
                                             static_cast<double>((i%2)?h:w)));
    }
    if(result[2]-result[0]<minimum||result[3]-result[1]<minimum)return false;
    std::copy(result,result+4,box);
    return true;
}
#endif
