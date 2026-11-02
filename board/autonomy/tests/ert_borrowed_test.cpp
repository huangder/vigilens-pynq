// Model-dependent parity test. Reads the supplied-ROI local fixture used by
// ert_probe; no images or face-recognition features are emitted.
#include "ert_landmark.hpp"
#include <dlib/array2d.h>
#include <cassert>
#include <cstdio>
#include <thread>

int main(int argc,char **argv) {
    if(argc!=2)return 2;
    vg_ert::Model borrowed(argv[1]);dlib::shape_predictor reference;dlib::deserialize(argv[1])>>reference;
    dlib::array2d<dlib::rgb_pixel> image;char line[512];unsigned frames=0;
    while(std::fgets(line,sizeof(line),stdin)) {
        unsigned long long id;double ts;int w,h,x,y,bw,bh;
        if(std::sscanf(line,"%llu %lf %d %d %d %d %d %d",&id,&ts,&w,&h,&x,&y,&bw,&bh)!=8||
           w<1||h<1||w>4096||h>4096)return 2;
        image.set_size(h,w);const size_t bytes=size_t(w)*h*3;
        if(std::fread(image.begin(),1,bytes,stdin)!=bytes)return 2;
        const auto shape=reference(image,dlib::rectangle(x,y,x+bw-1,y+bh-1));
        const auto *rgb=reinterpret_cast<const unsigned char*>(image.begin());
        const std::vector<unsigned char> before(rgb,rgb+bytes);
        auto check=[&] {
            std::array<float,136> p{};
            assert(borrowed.run({rgb,w,h},{{x,y,x+bw,y+bh}},p));
            for(int i=0;i<68;++i){assert(p[2*i]==shape.part(i).x());assert(p[2*i+1]==shape.part(i).y());}
            assert(!borrowed.run({rgb,w,h},{{-1,0,w,h}},p));
        };
        std::thread concurrent(check);check();concurrent.join();
        assert(std::equal(before.begin(),before.end(),rgb));++frames;
    }
    if(std::ferror(stdin)||!frames)return 2;
    std::printf("ERT_BORROWED_PASS frames=%u concurrent_calls=2 point_parity=exact rgb_unchanged=1\n",frames);
}
