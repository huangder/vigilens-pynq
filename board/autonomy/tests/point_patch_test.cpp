// Test the actual internal interpolator against an independent scalar oracle.
// Include its implementation in this test executable only; production has one
// separately compiled translation unit and no test-only exported API.
#include "../point_tracker.cpp"
#include <cassert>
#include <cstdio>
#include <cstring>
#include <random>

int main() {
    vg_track::Image input;input.width=97;input.height=71;
    input.pixels.resize(static_cast<size_t>(input.width)*input.height);
    std::mt19937 rng(571646);
    for(auto& pixel:input.pixels)pixel=uint8_t(rng());
    int cases=0;
    for(int radius=1;radius<=8;++radius)for(int trial=0;trial<500;++trial) {
        const float x=radius+1+(rng()%70000)/1000.f;
        const float y=radius+1+(rng()%44000)/1000.f;
        float actual[289],expected[289];
        vg_track::patch(input,x,y,radius,actual);
        const int ix=int(x),iy=int(y);const float fx=x-ix,fy=y-iy;
        int n=0;
        for(int j=-radius;j<=radius;++j)for(int i=-radius;i<=radius;++i) {
            const auto a=input.pixels[static_cast<size_t>(iy+j)*input.width+ix+i];
            const auto b=input.pixels[static_cast<size_t>(iy+j)*input.width+ix+i+1];
            const auto c=input.pixels[static_cast<size_t>(iy+j+1)*input.width+ix+i];
            const auto d=input.pixels[static_cast<size_t>(iy+j+1)*input.width+ix+i+1];
            const float top=a+(b-float(a))*fx,bottom=c+(d-float(c))*fx;
            expected[n++]=top+(bottom-top)*fy;
        }
        assert(std::memcmp(actual,expected,static_cast<size_t>(n)*sizeof(float))==0);
        ++cases;
    }
    std::printf("POINT_PATCH_PASS scalar_bit_exact=1 cases=%d radii=1..8\n",cases);
}
