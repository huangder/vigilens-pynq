/* Offline driver of the EXACT board vision runtime, not a Python algorithm.
 * stdin: ASCII header "id ts width height PL_gray_mean motion_q16 valid\n"
 * followed by width*height*3 RGB bytes. PL values are supplied by the fixture;
 * this driver must not be called a hardware test. Raw pixels stay local. */
#include "vision_runtime.h"
#include <cfenv>
#include <clocale>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

int main(int argc,char **argv) {
    const bool pl_gray=argc==5&&!std::strcmp(argv[4],"--pl-gray-3of8");
    if(argc!=4&&!pl_gray) { std::fprintf(stderr,"usage: %s detector_dir peppa.mnn output.jsonl [--pl-gray-3of8]\n",argv[0]);return 2; }
    if(!std::setlocale(LC_NUMERIC,"C") || std::fesetround(FE_TONEAREST)) return 2;
    vg_vision *runtime=vg_vision_create(argv[1],argv[2]);
    if(!runtime){std::fprintf(stderr,"VISION_INIT_FAIL\n");return 1;}
    FILE *out=std::fopen(argv[3],"w");
    if(!out){vg_vision_destroy(runtime);return 1;}
    char header[512],json[8192];
    unsigned frames=0,contracts=0,rejected=0,visible=0;
    int exit_code=0;
    while(std::fgets(header,sizeof(header),stdin)) {
        unsigned long long id;unsigned motion;int width,height,valid;double ts,mean;char extra;
        if(std::sscanf(header,"%llu %lf %d %d %lf %u %d %c",&id,&ts,&width,&height,&mean,&motion,&valid,&extra)!=7 ||
           width<=0||height<=0||width>4096||height>4096) {exit_code=2;break;}
        std::vector<unsigned char> rgb(static_cast<size_t>(width)*height*3);
        if(std::fread(rgb.data(),1,rgb.size(),stdin)!=rgb.size()) {exit_code=2;break;}
        vg_vision_frame frame{static_cast<uint64_t>(id),ts,width,height,rgb.data(),mean,motion,valid,nullptr,0,0};
        std::vector<unsigned char> gray;
        if(pl_gray) {
            if(width%8||height%8){exit_code=2;break;}
            frame.gray_width=width/8*3;frame.gray_height=height/8*3;
            gray.reserve(static_cast<size_t>(frame.gray_width)*frame.gray_height);
            for(int y=0;y<height;++y)if(y%8<3)for(int x=0;x<width;++x)if(x%8<3) {
                const auto *p=rgb.data()+(static_cast<size_t>(y)*width+x)*3;
                gray.push_back(static_cast<unsigned char>((77u*p[0]+150u*p[1]+29u*p[2]+128u)>>8));
            }
            frame.pl_gray=gray.data(); // scalar reference; this remains a host test
        }
        vg_vision_info info{};
        int rc=vg_vision_update(runtime,&frame,json,sizeof(json),&info);
        ++frames;
        if(rc)++rejected;
        else {
            ++contracts;visible+=info.visible;
            if(std::fprintf(out,"%s\n",json)<0){exit_code=1;break;}
        }
        std::fprintf(stderr,"VISION_FRAME id=%llu rc=%d detections=%d visible=%d path=%d detect_ms=%.3f landmark_ms=%.3f total_ms=%.3f vital_hz=%.3f vital_reject=%s\n",
            id,rc,info.detections,info.visible,info.detector_path,info.detector_ms,info.landmark_ms,info.total_ms,
            info.vital_rate_hz, info.vital_reject ? info.vital_reject : "-");
    }
    if(std::ferror(stdin)||std::fclose(out))exit_code=1;
    std::fprintf(stderr,"VISION_SUMMARY input=%u contracts=%u visible=%u rejected=%u hardware_test=0 accuracy_verified=0\n",
        frames,contracts,visible,rejected);
    vg_vision_destroy(runtime);
    return exit_code;
}
