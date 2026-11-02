#ifndef VG_LANDMARK_PREPROCESS_HPP
#define VG_LANDMARK_PREPROCESS_HPP
#include <array>
#include <cmath>
#include <cstddef>

/* Private, byte-exact replacement for copying a packed ROI then resizing it.
 * Keep the original float expressions and uint8 rounding: no reciprocal
 * substitution, FMA/fast-math, or early float normalization is permitted.
 * Caller supplies an in-bounds half-open ROI and a packed RGB source. */
namespace vg_landmark_preprocess {
constexpr int input_size=160;
using Scratch=std::array<unsigned char,input_size*input_size*3>;
inline void resize_roi_rgb(const unsigned char *rgb, int width,
                           int rx0,int ry0,int rx1,int ry1,Scratch& dst) {
    struct Horizontal { int x0,x1; float weight; };
    std::array<Horizontal,input_size> horizontal;
    const int sw=rx1-rx0,sh=ry1-ry0;
    const std::size_t stride=static_cast<std::size_t>(width)*3;
    const unsigned char *origin=rgb+static_cast<std::size_t>(ry0)*stride+static_cast<std::size_t>(rx0)*3;
    for(int x=0;x<input_size;++x) {
        const float fx=(x+0.5f)*sw/input_size-0.5f;
        int x0=static_cast<int>(std::floor(fx));
        float wx=fx-x0;
        if(x0<0){x0=0;wx=0;}
        horizontal[x]={x0,x0+1<sw ? x0+1 : x0,wx};
    }
    for(int y=0;y<input_size;++y) {
        const float fy=(y+0.5f)*sh/input_size-0.5f;
        int y0=static_cast<int>(std::floor(fy));
        float wy=fy-y0;
        if(y0<0){y0=0;wy=0;}
        const int y1=y0+1<sh ? y0+1 : y0;
        const unsigned char *row0=origin+static_cast<std::size_t>(y0)*stride;
        const unsigned char *row1=origin+static_cast<std::size_t>(y1)*stride;
        for(int x=0;x<input_size;++x) {
            const auto& h=horizontal[x];
            for(int c=0;c<3;++c) {
                const float a=row0[h.x0*3+c],b=row0[h.x1*3+c];
                const float d=row1[h.x0*3+c],e=row1[h.x1*3+c];
                const float top=a+(b-a)*h.weight;
                const float bottom=d+(e-d)*h.weight;
                const float value=top+(bottom-top)*wy;
                dst[(y*input_size+x)*3+c]=static_cast<unsigned char>(
                    value<=0 ? 0 : (value>=255 ? 255 : value+0.5f));
            }
        }
    }
}
}
#endif
