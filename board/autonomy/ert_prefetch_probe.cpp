/* Model-only order-preserving candidate benchmark. ROI is supplied, not
 * detected. Pixels remain local; output is aggregate timings and parity only. */
#include <dlib/image_processing/shape_predictor.h>
#include <dlib/array2d.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <time.h>

namespace {
double cpu_ms(){timespec t{};if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("CPU clock unavailable");return t.tv_sec*1000.+t.tv_nsec/1e6;}
constexpr std::array<int,4> modes{{-1,0,4,8}};
}
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    try {
        dlib::shape_predictor model;dlib::deserialize(argv[1])>>model;
        if(model.num_parts()!=68)return 2;
        FILE *out=std::fopen(argv[2],"w");if(!out)return 2;
        std::array<std::vector<double>,4> times;dlib::array2d<dlib::rgb_pixel> image;
        char line[512];unsigned n=0;
        while(std::fgets(line,sizeof(line),stdin)) {
            unsigned long long id;double ts;int w,h,x,y,bw,bh;char extra;
            if(std::sscanf(line,"%llu %lf %d %d %d %d %d %d %c",&id,&ts,&w,&h,&x,&y,&bw,&bh,&extra)!=8||
               !std::isfinite(ts)||w<1||h<1||w>4096||h>4096||x<0||y<0||bw<1||bh<1||bw>w||bh>h||x>w-bw||y>h-bh)return 2;
            image.set_size(h,w);const size_t bytes=size_t(w)*h*3;
            if(std::fread(image.begin(),1,bytes,stdin)!=bytes)return 2;
            const dlib::rectangle rect(x,y,x+bw-1,y+bh-1);
            const auto reference=model(image,rect);
            // Rotate order to reduce a fixed warm-cache/order advantage.
            for(unsigned k=0;k<modes.size();++k) {
                const unsigned index=(n+k)%modes.size();const int mode=modes[index];
                const double started=cpu_ms();
                const auto shape=mode<0?model(image,rect):model.predict_batched(image,rect,unsigned(mode));
                const double duration=cpu_ms()-started;times[index].push_back(duration);
                for(unsigned i=0;i<68;++i)if(shape.part(i)!=reference.part(i))throw std::runtime_error("point parity failed");
            }
            ++n;
        }
        if(std::ferror(stdin)||!n)return 2;
        std::fprintf(out,"{\"frames\":%u,\"point_parity\":\"exact\",\"supplied_roi\":true,\"full_pipeline\":false,\"accuracy_verified\":false,\"variants\":[",n);
        for(unsigned i=0;i<modes.size();++i) {
            auto v=times[i];std::sort(v.begin(),v.end());double sum=0;for(double t:v)sum+=t;
            const double rank=(v.size()-1)*.95;const size_t low=size_t(rank);
            const double p95=v[low]+(v[std::min(low+1,v.size()-1)]-v[low])*(rank-low);
            std::fprintf(out,"%s{\"prefetch_ahead\":%d,\"cpu_ms_mean\":%.6f,\"cpu_ms_p95\":%.6f,\"cpu_ms_max\":%.6f}",i?",":"",modes[i],sum/v.size(),p95,v.back());
        }
        if(std::fputs("]}\n",out)==EOF||std::fclose(out))return 1;
        std::printf("ERT_PREFETCH_PROBE_PASS frames=%u variants=4 point_parity=exact full_pipeline=0\n",n);
        return 0;
    }catch(const std::exception& e){std::fprintf(stderr,"ERT_PREFETCH_PROBE_FAIL %s\n",e.what());return 1;}
}
