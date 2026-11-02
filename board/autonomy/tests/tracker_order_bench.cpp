// Synthetic point-update microbenchmark; not a face or pipeline measurement.
#include "point_tracker.hpp"
#include "vg_vision_config.h"
#include <array>
#include <cstdio>
#include <random>
#include <stdexcept>
#include <time.h>
static double cpu_ms(){timespec t{};if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("CPU clock unavailable");return t.tv_sec*1000.+t.tv_nsec/1e6;}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    try {
        constexpr int w=1280,h=720;std::mt19937 rng(178326);
        std::vector<unsigned char> before(size_t(w)*h*3),after(before.size());
        for(size_t i=0;i<before.size();i+=3){const unsigned char v=rng();before[i]=before[i+1]=before[i+2]=v;}
        for(int y=2;y<h;++y)for(int x=2;x<w;++x)for(int c=0;c<3;++c)after[(size_t(y)*w+x)*3+c]=before[(size_t(y-2)*w+x-2)*3+c];
        std::array<float,136> points{};for(int i=0;i<68;++i){points[i*2]=80+(i%10)*100;points[i*2+1]=60+(i/10)*80;}
        const auto a=vg_track::from_rgb(before.data(),w,h,VG_TRACKER_LEVELS);
        const vg_track::Policy p{VG_TRACKER_LEVELS,VG_TRACKER_RADIUS,VG_TRACKER_ITERATIONS,VG_TRACKER_MIN_EIGEN,VG_TRACKER_MAX_ERROR,VG_TRACKER_FB_ERROR,VG_TRACKER_MIN_FRACTION,VG_TRACKER_MAX_MOTION,VG_TRACKER_EPSILON};
        FILE *out=std::fopen(argv[1],"wb");if(!out)return 2;
        for(int missing:{-1,36,48}) {
            auto image=after;
            if(missing>=0)for(int y=int(points[missing*2+1])-32;y<=int(points[missing*2+1])+32;++y)
                for(int x=int(points[missing*2])-32;x<=int(points[missing*2])+32;++x)for(int c=0;c<3;++c)image[(size_t(y)*w+x)*3+c]=0;
            const auto b=vg_track::from_rgb(image.data(),w,h,VG_TRACKER_LEVELS);
            double total=0;
            for(int n=0;n<100;++n) {
                vg_track::Result r;const double start=cpu_ms();const bool ok=vg_track::update(a,b,points.data(),p,r);total+=cpu_ms()-start;
                if((missing<0)!=ok||(!ok&&r.rejected_index!=missing))throw std::runtime_error("unexpected synthetic update result");
                if(missing<0&&n==0&&std::fwrite(r.points.data(),sizeof(float),r.points.size(),out)!=r.points.size())return 1;
            }
            std::printf("{\"missing_critical\":%d,\"iterations\":100,\"cpu_ms_mean\":%.6f,\"synthetic_only\":true}\n",missing,total/100);
        }
        if(std::fclose(out))return 1;
        return 0;
    }catch(const std::exception& e){std::fprintf(stderr,"TRACKER_ORDER_BENCH_FAIL %s\n",e.what());return 1;}
}
