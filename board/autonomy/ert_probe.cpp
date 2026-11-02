/* Standalone model probe. Bounding boxes are supplied by the fixture, not
 * detected by this program. Never report these outputs as a full pipeline. */
#include <dlib/image_processing/shape_predictor.h>
#include <dlib/array2d.h>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <exception>
#include <stdexcept>
#include <sys/resource.h>
#include <time.h>

static double cpu_ms() {
    timespec t{};
    if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("thread CPU clock unavailable");
    return t.tv_sec*1000.+t.tv_nsec/1e6;
}
int main(int argc,char **argv) {
    if(argc!=3){std::fprintf(stderr,"usage: ert_probe model.dat output.jsonl < supplied_roi.rgbstream\n");return 2;}
    try {
        dlib::shape_predictor model;dlib::deserialize(argv[1])>>model;
        if(model.num_parts()!=68){std::fputs("ERT_MODEL_FORMAT_FAIL\n",stderr);return 2;}
        FILE *out=std::fopen(argv[2],"w");if(!out)return 2;
        dlib::array2d<dlib::rgb_pixel> image;
        static_assert(sizeof(dlib::rgb_pixel)==3,"fixture requires packed RGB888");
        char header[512];unsigned frames=0;int exit_code=0;
        while(std::fgets(header,sizeof(header),stdin)) {
            unsigned long long id;double ts;int w,h,x,y,bw,bh;char extra;
            if(std::sscanf(header,"%llu %lf %d %d %d %d %d %d %c",&id,&ts,&w,&h,&x,&y,&bw,&bh,&extra)!=8||
               !std::isfinite(ts)||w<1||h<1||w>4096||h>4096||
               x<0||y<0||bw<1||bh<1||bw>w||bh>h||x>w-bw||y>h-bh){exit_code=2;break;}
            image.set_size(h,w);
            const size_t bytes=static_cast<size_t>(w)*h*3;
            if(std::fread(image.begin(),1,bytes,stdin)!=bytes){exit_code=2;break;}
            const auto started=std::chrono::steady_clock::now();const double cpu_started=cpu_ms();
            // dlib's rectangle is inclusive; supplied bbox is [x,y,w,h].
            const auto shape=model(image,dlib::rectangle(x,y,x+bw-1,y+bh-1));
            const double cpu=cpu_ms()-cpu_started;
            const double wall=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
            std::fprintf(out,"{\"frame_id\":%llu,\"ts\":%.9f,\"bbox\":[%d,%d,%d,%d],\"wall_ms\":%.6f,\"cpu_ms\":%.6f,\"points\":[",id,ts,x,y,bw,bh,wall,cpu);
            for(unsigned i=0;i<shape.num_parts();++i)
                std::fprintf(out,"%s[%ld,%ld]",i?",":"",shape.part(i).x(),shape.part(i).y());
            if(std::fputs("]}\n",out)==EOF){exit_code=1;break;}
            ++frames;
        }
        if(std::ferror(stdin)||std::fclose(out))exit_code=1;
        rusage usage{};
        if(getrusage(RUSAGE_SELF,&usage))throw std::runtime_error("process memory usage unavailable");
#if defined(__arm__)
        const int compiled_arm=1;
#else
        const int compiled_arm=0;
#endif
        std::fprintf(stderr,"ERT_PROBE_END frames=%u compiled_arm=%d peak_rss_kib=%ld supplied_roi=1 full_pipeline=0 accuracy_verified=0 rc=%d\n",frames,compiled_arm,usage.ru_maxrss,exit_code);
        return exit_code;
    } catch(const std::exception& error){std::fprintf(stderr,"ERT_PROBE_FAIL %s\n",error.what());return 1;}
}
