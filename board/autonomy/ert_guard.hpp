#ifndef VG_ERT_GUARD_HPP
#define VG_ERT_GUARD_HPP
#include <algorithm>
#include <array>
#include <cmath>
#include <vector>

namespace vg_ert {
struct Transform {
    double a=1,b=0,tx=0,ty=0;
    std::array<double,2> at(double x,double y)const {return {{a*x-b*y+tx,b*x+a*y+ty}};}
};
// Stable nose/eye-corner points drive the ROI. Eyelid aperture and mouth opening
// are re-inferred each frame and never used to rigidly carry an old measurement.
inline bool fit(const float *source,const float *target,Transform& t) {
    constexpr int ids[]={27,28,29,30,36,39,42,45};
    double sx=0,sy=0,dx=0,dy=0;
    for(int i:ids) {
        if(!std::isfinite(source[2*i])||!std::isfinite(source[2*i+1])||
           !std::isfinite(target[2*i])||!std::isfinite(target[2*i+1]))return false;
        sx+=source[2*i];sy+=source[2*i+1];dx+=target[2*i];dy+=target[2*i+1];
    }
    sx/=8;sy/=8;dx/=8;dy/=8;
    double denominator=0,a=0,b=0;
    for(int i:ids) {
        const double x=source[2*i]-sx,y=source[2*i+1]-sy,u=target[2*i]-dx,v=target[2*i+1]-dy;
        denominator+=x*x+y*y;a+=x*u+y*v;b+=x*v-y*u;
    }
    if(!(denominator>0))return false;
    t.a=a/denominator;t.b=b/denominator;t.tx=dx-t.a*sx+t.b*sy;t.ty=dy-t.b*sx-t.a*sy;
    return std::isfinite(t.a)&&std::isfinite(t.b)&&std::isfinite(t.tx)&&std::isfinite(t.ty)&&
        t.a*t.a+t.b*t.b>0;
}
inline bool fit_translation(const float *source,const float *target,Transform& t) {
    if(!fit(source,target,t))return false;
    constexpr int ids[]={27,28,29,30,36,39,42,45};
    double tx=0,ty=0;
    for(int i:ids){tx+=target[2*i]-source[2*i];ty+=target[2*i+1]-source[2*i+1];}
    t={1,0,tx/8,ty/8};return true;
}
inline int geometry_failure(const float *p,int w,int h) {
    for(int i=0;i<68;++i) {
        if(!std::isfinite(p[2*i])||!std::isfinite(p[2*i+1]))return 1;
        if(p[2*i]<0||p[2*i]>=w||p[2*i+1]<0||p[2*i+1]>=h)return 100+i;
    }
    if(!(p[72]<p[78]&&p[84]<p[90]&&p[96]<p[108]&&p[103]<p[115]&&
        p[0]<p[16]&&p[16]<p[32]&&p[61]>(p[73]+p[85])/2&&p[61]<p[17]))return 2;
    return 0;
}
inline bool geometry(const float *p,int w,int h){return geometry_failure(p,w,h)==0;}
inline bool gray(const unsigned char *rgb,int w,int h,double x,double y,double& value) {
    if(!std::isfinite(x)||!std::isfinite(y)||x<0||y<0||x>=w-1||y>=h-1)return false;
    const int ix=int(x),iy=int(y);const double fx=x-ix,fy=y-iy;
    auto pixel=[&](int xx,int yy){const auto *p=rgb+(static_cast<size_t>(yy)*w+xx)*3;
        return double((77u*p[0]+150u*p[1]+29u*p[2]+128u)>>8);};
    value=(1-fy)*((1-fx)*pixel(ix,iy)+fx*pixel(ix+1,iy))+
        fy*((1-fx)*pixel(ix,iy+1)+fx*pixel(ix+1,iy+1));
    return true;
}
struct Patch {
    int side=0;std::array<int,4> box{};std::vector<double> values;
    bool make(const unsigned char *rgb,int w,int h,const std::array<int,4>& b,int n) {
        side=n;box=b;values.clear();
        if(!rgb||n<2||b[2]<=b[0]||b[3]<=b[1])return false;
        for(int y=0;y<n;++y)for(int x=0;x<n;++x) {
            double value;
            if(!gray(rgb,w,h,b[0]+(x+.5)*(b[2]-b[0])/n,b[1]+(y+.5)*(b[3]-b[1])/n,value)) {
                values.clear();return false;
            }
            values.push_back(value);
        }
        return true;
    }
    // This checks actual current pixels against a detector-confirmed appearance.
    // It is a loss guard, not a face classifier or an accuracy measurement.
    bool matches(const unsigned char *rgb,int w,int h,const Transform& t,
                 double min_std,double max_error,double& error)const {
        if(!rgb||side<2||values.size()!=size_t(side)*side)return false;
        std::vector<double> current;current.reserve(values.size());
        double sm=0,cm=0;
        for(int y=0;y<side;++y)for(int x=0;x<side;++x) {
            const auto p=t.at(box[0]+(x+.5)*(box[2]-box[0])/side,
                              box[1]+(y+.5)*(box[3]-box[1])/side);
            double v;if(!gray(rgb,w,h,p[0],p[1],v))return false;
            current.push_back(v);cm+=v;sm+=values[current.size()-1];
        }
        cm/=current.size();sm/=values.size();double sv=0,cv=0;error=0;
        for(size_t i=0;i<values.size();++i) {
            const double s=values[i]-sm,c=current[i]-cm;
            sv+=s*s;cv+=c*c;error+=std::abs(s-c);
        }
        error/=values.size();
        return sv/values.size()>=min_std*min_std&&cv/values.size()>=min_std*min_std&&error<=max_error;
    }
};
}
#endif
