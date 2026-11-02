/* White-box malformed-regression regression. Build with float-cast-overflow
 * sanitizer: invalid inputs must be rejected before any network invocation or
 * float -> int conversion. No model, video, or board is needed. */
#include "../face_detector.cpp"
#include <cassert>
#include <climits>
#include <limits>

static void crop_parity() {
    const int width=281,height=179,stride=width*3+13;
    std::vector<uint8_t> pixels(static_cast<size_t>(stride)*height);
    uint32_t seed=0x461235u;
    for(auto& pixel:pixels) { seed=seed*1664525u+1013904223u;pixel=seed>>24; }
    std::vector<uint8_t> scratch;
    int tested=0;
    for(int index=0;index<512;++index) {
        auto random=[&](int n){seed=seed*1664525u+1013904223u;return int(seed%unsigned(n));};
        const int x1=index%2 ? random(width-2) : random(width*2)-width;
        const int y1=index%2 ? random(height-2) : random(height*2)-height;
        const int x2=index%2 ? x1+2+random(width-x1-1) : x1+2+random(width);
        const int y2=index%2 ? y1+2+random(height-y1-1) : y1+2+random(height);
        const int cw=x2-x1,ch=y2-y1;
        // Freeze the original allocate/copy/zero-pad implementation as reference.
        std::vector<uint8_t> packed(static_cast<size_t>(cw)*ch*3,0);
        for(int y=std::max(y1,0);y<std::min(y2,height);++y) {
            int left=std::max(x1,0),right=std::min(x2,width);
            if(right>left)std::memcpy(packed.data()+(static_cast<size_t>(y-y1)*cw+left-x1)*3,
                pixels.data()+static_cast<size_t>(y)*stride+left*3,static_cast<size_t>(right-left)*3);
        }
        for(int size:{24,48}) {
            auto old=ncnn::Mat::from_pixels_resize(packed.data(),ncnn::Mat::PIXEL_RGB,cw,ch,size,size);
            auto current=resize_candidate(pixels.data(),width,height,stride,x1,y1,x2,y2,size,scratch);
            normalize(old);normalize(current);
            for(int c=0;c<3;++c)
                assert(!std::memcmp(old.channel(c),current.channel(c),size*size*sizeof(float)));
            ++tested;
        }
    }
    std::printf("FACE_DETECTOR_CROP_INPUT_EXACT cases=%d padded_stride=1 edge_padding=1\n",tested);
}

int main() {
    crop_parity();
    assert(vg_fd_create_ex("unused",1,-1)==nullptr);
    assert(vg_fd_create_ex("unused",1,2)==nullptr);
    const float huge=std::numeric_limits<float>::max();
    const float inf=std::numeric_limits<float>::infinity();
    const float nan=std::numeric_limits<float>::quiet_NaN();
    uint8_t pixels[12*12*3]={};
    ncnn::Net unloaded; // Any accepted crop would fail network extraction.
    auto rejected=[&](Box b) {
        for(bool packing:{false,true}) {
            unloaded.opt.use_packing_layout=packing;
            std::vector<Box> out;
            assert(refine(unloaded,pixels,12,12,36,std::vector<Box>{b},out,24,0.7f,"conv5-2"));
            assert(out.empty());
        }
    };
    for(int coordinate=0;coordinate<4;++coordinate)for(float value:{huge,-huge,inf,-inf,nan}) {
        Box b={0,0,12,12,1,{}};
        // Members are deliberately assigned separately (no layout assumptions).
        if(coordinate==0)b.x1=value;if(coordinate==1)b.y1=value;
        if(coordinate==2)b.x2=value;if(coordinate==3)b.y2=value;
        rejected(b);
    }
    // Finite positive/negative coordinates with positive area also need bounds.
    rejected(Box{1e30f,0,2e30f,12,1,{}});
    rejected(Box{-2e30f,0,-1e30f,12,1,{}});
    rejected(Box{0,1e30f,12,2e30f,1,{}});
    rejected(Box{0,-2e30f,12,-1e30f,1,{}});
    for(int offset=0;offset<4;++offset)for(float value:{huge,-huge,1e30f,-1e30f,inf,nan}) {
        Box b={0,0,12,12,1,{}};b.reg[offset]=value;
        regress(b,true);rejected(b);
    }
    vg_fd ctx;
    vg_fd_box output{},candidate{0,0,12,12,1};
    vg_fd_params p={40,.709f,.6f,.7f,.7f,.5f,.7f,.7f,.7f,64};
    p.max_candidates=INT_MAX;
    assert(vg_fd_detect(&ctx,pixels,640,480,1920,&p,&output,1)==-1);
    assert(vg_fd_refine(&ctx,pixels,12,12,36,&p,&candidate,&output,1)==-1);
    p.max_candidates=64;p.scale_factor=std::nextafter(1.f,0.f);
    assert(vg_fd_detect(&ctx,pixels,640,480,1920,&p,&output,1)==-1);
    candidate.x1=INT_MIN;candidate.x2=INT_MAX;
    assert(vg_fd_refine(&ctx,pixels,12,12,36,&p,&candidate,&output,1)==-1);
    std::puts("FACE_DETECTOR_BOUNDS_PASS");
}
