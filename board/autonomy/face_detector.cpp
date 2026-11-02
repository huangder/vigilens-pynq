/* MTCNN ncnn cascade for the cached PFLD-DEMO det1/2/3 models.
 * Internal boxes use continuous half-open coordinates; RGB normalization is
 * (pixel-127.5)/128, fixed by the pretrained model, not a decision threshold.
 */
#include "face_detector.h"
#include <net.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>

struct vg_fd { ncnn::Net nets[3]; };
namespace {
struct Box { float x1,y1,x2,y2,score,reg[4]; };
// API resource ceilings, not face classification thresholds or product defaults.
constexpr int kMaxCandidateBudget=65536;
constexpr int kMaxPyramidLevels=256;
bool geometry(const Box& b) {
    return std::isfinite(b.x1)&&std::isfinite(b.y1)&&std::isfinite(b.x2)&&std::isfinite(b.y2)&&
        b.x2>b.x1&&b.y2>b.y1;
}
float area(const Box& b) { return std::max(0.f,b.x2-b.x1)*std::max(0.f,b.y2-b.y1); }
void nms(std::vector<Box>& a,float threshold,bool use_min,int budget) {
    std::stable_sort(a.begin(),a.end(),[](const Box& x,const Box& y){return x.score>y.score;});
    std::vector<Box> kept;
    for(const Box& b:a) {
        bool keep=true;
        for(const Box& k:kept) {
            float intersection=std::max(0.f,std::min(b.x2,k.x2)-std::max(b.x1,k.x1))*
                std::max(0.f,std::min(b.y2,k.y2)-std::max(b.y1,k.y1));
            float denom=use_min?std::min(area(b),area(k)):area(b)+area(k)-intersection;
            if(denom>0 && intersection/denom>threshold) {keep=false;break;}
        }
        if(keep) kept.push_back(b);
        if(static_cast<int>(kept.size())>=budget) break;
    }
    a.swap(kept);
}
void regress(Box& b,bool square) {
    float w=b.x2-b.x1,h=b.y2-b.y1;
    b.x1+=w*b.reg[0];b.y1+=h*b.reg[1];b.x2+=w*b.reg[2];b.y2+=h*b.reg[3];
    if(square) {
        w=b.x2-b.x1;h=b.y2-b.y1;
        float s=std::max(w,h),cx=(b.x1+b.x2)/2,cy=(b.y1+b.y2)/2;
        b.x1=cx-s/2;b.y1=cy-s/2;b.x2=cx+s/2;b.y2=cy+s/2;
    }
}
void normalize(ncnn::Mat& input) {
    const float mean[]={127.5f,127.5f,127.5f},norm[]={1.f/128,1.f/128,1.f/128};
    input.substract_mean_normalize(mean,norm);
}
bool valid(const vg_fd_params& p) {
    const float thresholds[]={p.score_p,p.score_r,p.score_o,p.nms_p_scale,p.nms_p,p.nms_r,p.nms_o};
    for(float v:thresholds) if(!std::isfinite(v)||v<0||v>1)return false;
    return std::isfinite(p.min_face)&&p.min_face>=12 && std::isfinite(p.scale_factor)&&
        p.scale_factor>0&&p.scale_factor<1&&p.max_candidates>0&&p.max_candidates<=kMaxCandidateBudget;
}
ncnn::Mat resize_candidate(const uint8_t* rgb,int width,int height,int stride,
                           int x1,int y1,int x2,int y2,int size,std::vector<uint8_t>& scratch) {
    const int cw=x2-x1,ch=y2-y1;
    if(x1>=0 && y1>=0 && x2<=width && y2<=height) {
        /* The ncnn stride overload uses the SAME bilinear kernel. Avoid
           allocating/copying a full-resolution crop just to make 24/48 pixels. */
        return ncnn::Mat::from_pixels_resize(rgb+static_cast<size_t>(y1)*stride+x1*3,
            ncnn::Mat::PIXEL_RGB,cw,ch,stride,size,size);
    }
    // Preserve zero padding beyond image boundaries, including edge faces.
    scratch.assign(static_cast<size_t>(cw)*ch*3,0);
    for(int y=std::max(y1,0);y<std::min(y2,height);++y) {
        int left=std::max(x1,0),right=std::min(x2,width);
        if(right>left)std::memcpy(scratch.data()+(static_cast<size_t>(y-y1)*cw+left-x1)*3,
            rgb+static_cast<size_t>(y)*stride+left*3,static_cast<size_t>(right-left)*3);
    }
    return ncnn::Mat::from_pixels_resize(scratch.data(),ncnn::Mat::PIXEL_RGB,cw,ch,size,size);
}
bool refine(ncnn::Net& net,const uint8_t* rgb,int width,int height,int stride,
            const std::vector<Box>& input,std::vector<Box>& output,int size,float threshold,const char* regname) {
    std::vector<uint8_t> scratch;
    for(Box b:input) {
        // Check BOTH bounds of EVERY coordinate before float -> int conversion.
        // A corrupted regression may yield finite FLT_MAX in any coordinate.
        if(!geometry(b)||b.x1<-width*4.f||b.x1>width*4.f||b.x2<-width*4.f||b.x2>width*4.f||
           b.y1<-height*4.f||b.y1>height*4.f||b.y2<-height*4.f||b.y2>height*4.f)continue;
        int x1=static_cast<int>(std::floor(b.x1)),y1=static_cast<int>(std::floor(b.y1));
        int x2=static_cast<int>(std::ceil(b.x2)),y2=static_cast<int>(std::ceil(b.y2));
        int cw=x2-x1,ch=y2-y1;
        if(cw<=0||ch<=0||cw>width*4||ch>height*4)continue;
        ncnn::Mat in=resize_candidate(rgb,width,height,stride,x1,y1,x2,y2,size,scratch);
        normalize(in);
        auto ex=net.create_extractor();ncnn::Mat prob,reg;
        if(ex.input("data",in)||ex.extract("prob1",prob)||ex.extract(regname,reg)||prob.total()!=2||reg.total()!=4)return false;
        const float* scores=prob; const float* offsets=reg;
        if(!std::isfinite(scores[1])||scores[1]<threshold)continue;
        bool finite=true;for(int c=0;c<4;++c)finite=finite&&std::isfinite(offsets[c]);
        if(!finite)continue;
        b.score=scores[1];std::copy(offsets,offsets+4,b.reg);
        output.push_back(b);
    }
    return true;
}
int finish(vg_fd* ctx,const uint8_t* rgb,int width,int height,int stride,
           const vg_fd_params& p,const std::vector<Box>& candidates,vg_fd_box* boxes,int capacity) {
    std::vector<Box> r,o;
    if(!refine(ctx->nets[1],rgb,width,height,stride,candidates,r,24,p.score_r,"conv5-2"))return -2;
    nms(r,p.nms_r,false,p.max_candidates);for(Box& b:r)regress(b,true);
    if(!refine(ctx->nets[2],rgb,width,height,stride,r,o,48,p.score_o,"conv6-2"))return -2;
    for(Box& b:o)regress(b,false);
    nms(o,p.nms_o,true,p.max_candidates);
    int count=0;
    for(const Box& b:o) {
        if(!geometry(b))continue;
        vg_fd_box out={static_cast<int>(std::floor(std::max(0.f,std::min(float(width),b.x1)))),
            static_cast<int>(std::floor(std::max(0.f,std::min(float(height),b.y1)))),
            static_cast<int>(std::ceil(std::max(0.f,std::min(float(width),b.x2)))),
            static_cast<int>(std::ceil(std::max(0.f,std::min(float(height),b.y2)))),b.score};
        if(out.x2<=out.x1||out.y2<=out.y1)continue;
        boxes[count++]=out;if(count==capacity)break;
    }
    return count;
}
}
extern "C" vg_fd* vg_fd_create(const char* dir,int threads) {
    return vg_fd_create_ex(dir,threads,0);
}
extern "C" vg_fd* vg_fd_create_ex(const char* dir,int threads,int packing) {
    if(!dir||threads<1||(packing!=0&&packing!=1))return nullptr;
    vg_fd* ctx=new vg_fd;
    for(int i=0;i<3;++i) {
        ctx->nets[i].opt.num_threads=threads;
        ctx->nets[i].opt.use_vulkan_compute=false;
        ctx->nets[i].opt.use_packing_layout=packing!=0;
        std::string base=std::string(dir)+"/det"+std::to_string(i+1);
        if(ctx->nets[i].load_param((base+".param").c_str())||ctx->nets[i].load_model((base+".bin").c_str())) {
            delete ctx;return nullptr;
        }
    }
    return ctx;
}
extern "C" void vg_fd_destroy(vg_fd* ctx){delete ctx;}
extern "C" int vg_fd_detect(vg_fd* ctx,const uint8_t* rgb,int width,int height,int stride,
                             const vg_fd_params* p,vg_fd_box* boxes,int capacity) {
    if(!ctx||!rgb||!p||!boxes||width<12||height<12||width>32768||height>32768||
       stride<width*3||capacity<1||!valid(*p))return -1;
    // Reject near-one factors before they can create millions of pyramid levels.
    // Evaluate in double so a finite float parameter cannot overflow this check.
    const double ratio=double(std::min(width,height))/p->min_face;
    if(ratio>=1 && std::floor(std::log(ratio)/-std::log(double(p->scale_factor)))+1>kMaxPyramidLevels)return -1;
    std::vector<uint8_t> packed;
    if(stride!=width*3) {
        packed.resize(static_cast<size_t>(width)*height*3);
        for(int y=0;y<height;++y)std::memcpy(packed.data()+static_cast<size_t>(y)*width*3,rgb+static_cast<size_t>(y)*stride,width*3);
        rgb=packed.data();stride=width*3;
    }
    std::vector<Box> candidates;
    // Start at requested minimum face, then shrink: old probe scanned in reverse.
    for(float scale=12.f/p->min_face;std::min(width,height)*scale>=12;) {
        int sw=static_cast<int>(std::ceil(width*scale)),sh=static_cast<int>(std::ceil(height*scale));
        ncnn::Mat in=ncnn::Mat::from_pixels_resize(rgb,ncnn::Mat::PIXEL_RGB,width,height,sw,sh);
        normalize(in);
        auto ex=ctx->nets[0].create_extractor();ncnn::Mat prob,reg;
        if(ex.input("data",in)||ex.extract("prob1",prob)||ex.extract("conv4-2",reg)||prob.c!=2||reg.c!=4)return -2;
        std::vector<Box> level;
        for(int y=0;y<prob.h;++y)for(int x=0;x<prob.w;++x) {
            float score=prob.channel(1).row(y)[x];if(!std::isfinite(score)||score<p->score_p)continue;
            Box b={2*x/scale,2*y/scale,(2*x+12)/scale,(2*y+12)/scale,score,{}};
            for(int c=0;c<4;++c)b.reg[c]=reg.channel(c).row(y)[x];
            level.push_back(b);
        }
        nms(level,p->nms_p_scale,false,p->max_candidates);
        candidates.insert(candidates.end(),level.begin(),level.end());
        float next=scale*p->scale_factor;
        if(next>=scale)return -1; // A near-one factor must never stall in float32.
        scale=next;
    }
    nms(candidates,p->nms_p,false,p->max_candidates);
    for(Box& b:candidates)regress(b,true);
    return finish(ctx,rgb,width,height,stride,*p,candidates,boxes,capacity);
}
extern "C" int vg_fd_refine(vg_fd* ctx,const uint8_t* rgb,int width,int height,int stride,
                             const vg_fd_params* p,const vg_fd_box* candidate,
                             vg_fd_box* boxes,int capacity) {
    if(!ctx||!rgb||!p||!candidate||!boxes||width<12||height<12||width>32768||height>32768||
       stride<width*3||capacity<1||!valid(*p))return -1;
    if(candidate->x1<0||candidate->y1<0||candidate->x2>width||candidate->y2>height||
       candidate->x2<=candidate->x1||candidate->y2<=candidate->y1)return -1;
    Box b={float(candidate->x1),float(candidate->y1),float(candidate->x2),float(candidate->y2),0,{}};
    regress(b,true); // zero regression => square only, no expansion parameter.
    return finish(ctx,rgb,width,height,stride,*p,std::vector<Box>{b},boxes,capacity);
}
