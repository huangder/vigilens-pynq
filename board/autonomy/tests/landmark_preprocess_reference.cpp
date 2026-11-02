/* Independent frozen pre-optimization reference, no MNN dependency. */
#include "landmark_preprocess.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <random>
#include <vector>

namespace {
void old_resize(const unsigned char* src, int sw, int sh,
                unsigned char* dst, int dw, int dh) {
    for (int y = 0; y < dh; ++y) {
        const float fy = (y + 0.5f) * sh / dh - 0.5f;
        int y0 = static_cast<int>(std::floor(fy));
        float wy = fy - y0;
        if (y0 < 0) { y0 = 0; wy = 0; }
        const int y1 = y0 + 1 < sh ? y0 + 1 : y0;
        for (int x = 0; x < dw; ++x) {
            const float fx = (x + 0.5f) * sw / dw - 0.5f;
            int x0 = static_cast<int>(std::floor(fx));
            float wx = fx - x0;
            if (x0 < 0) { x0 = 0; wx = 0; }
            const int x1 = x0 + 1 < sw ? x0 + 1 : x0;
            for (int c = 0; c < 3; ++c) {
                const float a = src[(y0 * sw + x0) * 3 + c];
                const float b = src[(y0 * sw + x1) * 3 + c];
                const float d = src[(y1 * sw + x0) * 3 + c];
                const float e = src[(y1 * sw + x1) * 3 + c];
                const float top = a + (b - a) * wx;
                const float bottom = d + (e - d) * wx;
                const float value = top + (bottom - top) * wy;
                dst[(y * dw + x) * 3 + c] = static_cast<unsigned char>(
                    value <= 0 ? 0 : (value >= 255 ? 255 : value + 0.5f));
            }
        }
    }
}
void nchw(const unsigned char* crop,float *host) {
    for(int i=0;i<160*160;++i) {
        const float b=crop[static_cast<size_t>(i)*3+2];
        const float g=crop[static_cast<size_t>(i)*3+1];
        const float r=crop[static_cast<size_t>(i)*3+0];
        host[i]=(b-127.0f)/127.0f;
        host[160*160+i]=(g-127.0f)/127.0f;
        host[2*160*160+i]=(r-127.0f)/127.0f;
    }
}
}
extern "C" int vg_test_preprocess_compare(const unsigned char *rgb,int width,int height,
                                          int x0,int y0,int x1,int y1) {
    if(!rgb||x0<0||y0<0||x1>width||y1>height||x1<=x0||y1<=y0)return -1;
    const int sw=x1-x0,sh=y1-y0;
    std::vector<unsigned char> patch(static_cast<size_t>(sw)*sh*3);
    for(int y=0;y<sh;++y)
        std::memcpy(patch.data()+static_cast<size_t>(y)*sw*3,
            rgb+(static_cast<size_t>(y0+y)*width+x0)*3,static_cast<size_t>(sw)*3);
    vg_landmark_preprocess::Scratch reference,actual;
    old_resize(patch.data(),sw,sh,reference.data(),160,160);
    vg_landmark_preprocess::resize_roi_rgb(rgb,width,x0,y0,x1,y1,actual);
    if(std::memcmp(reference.data(),actual.data(),actual.size()))return 1;
    std::array<float,160*160*3> before,after;
    nchw(reference.data(),before.data());nchw(actual.data(),after.data());
    return std::memcmp(before.data(),after.data(),sizeof(before)) ? 2 : 0;
}
extern "C" int vg_test_preprocess_random(void) {
    std::mt19937 rng(20261101);
    int count=0;
    for(int image=0;image<64;++image) {
        const int width=1+static_cast<int>(rng()%1280),height=1+static_cast<int>(rng()%720);
        std::vector<unsigned char> rgb(static_cast<size_t>(width)*height*3);
        for(auto& c:rgb)c=static_cast<unsigned char>(rng());
        for(int trial=0;trial<32;++trial) {
            int x0=static_cast<int>(rng()%width),y0=static_cast<int>(rng()%height);
            int x1=x0+1+static_cast<int>(rng()%(width-x0)),y1=y0+1+static_cast<int>(rng()%(height-y0));
            if(trial==0){x0=0;y0=0;x1=width;y1=height;}
            if(trial==1){x0=0;y0=0;x1=1;y1=1;}
            if(trial==2){x0=width-1;y0=height-1;x1=width;y1=height;}
            if(trial==3){x0=0;y0=height-1;x1=width;y1=height;}
            if(trial==4){x0=width-1;y0=0;x1=width;y1=height;}
            if(vg_test_preprocess_compare(rgb.data(),width,height,x0,y0,x1,y1))return -count-1;
            ++count;
        }
    }
    return count;
}
