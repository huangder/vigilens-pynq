/* Include the actual interpolation kernel to compare all output bits. */
#include "../point_tracker.cpp"
#include <cstring>
#include <chrono>
#include <cstdio>
#include <cstdlib>
int main() {
    vg_track::Image image;image.width=43;image.height=41;image.pixels.resize(43*41);
    uint32_t seed=7;for(auto& v:image.pixels){seed=1664525u*seed+1013904223u;v=seed>>24;}
    unsigned cases=0;
    for(int radius=1;radius<=7;++radius)for(int yy=radius+1;yy<image.height-radius-2;yy+=3)
    for(int xx=radius+1;xx<image.width-radius-2;xx+=3)for(int f=0;f<8;++f) {
        const float x=xx+f*.125f,y=yy+(7-f)*.125f;float actual[225],expected[225];
        vg_track::patch(image,x,y,radius,actual);
        int n=0;const float fx=x-int(x),fy=y-int(y);
        for(int j=-radius;j<=radius;++j)for(int i=-radius;i<=radius;++i) {
            const auto *row=image.pixels.data()+size_t(int(y)+j)*image.width+int(x)+i;
            const auto *next=row+image.width;
            const float top=row[0]+(row[1]-float(row[0]))*fx;
            const float bottom=next[0]+(next[1]-float(next[0]))*fx;
            expected[n++]=top+(bottom-top)*fy;
        }
        if(std::memcmp(actual,expected,size_t(n)*sizeof(float))) { std::fprintf(stderr,"patch mismatch radius=%d x=%g y=%g\n",radius,x,y);return 1; }
        ++cases;
    }
    float output[225];volatile float sum=0;
    auto start=std::chrono::steady_clock::now();
    for(int i=0;i<200000;++i){vg_track::patch(image,20.25f+(i%2)*.125f,20.75f,3,output);sum+=output[i%49];}
    double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
    std::printf("POINT_PATCH_PASS cases=%u radius3_calls=200000 elapsed_ms=%.3f checksum=%.0f\n",cases,ms,double(sum));
}
