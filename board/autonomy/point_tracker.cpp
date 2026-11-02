#include "point_tracker.hpp"
#include <algorithm>
#include <cmath>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

namespace vg_track {
static void finish_pyramid(Pyramid& p,int levels) {
    for(int level=1;level<levels;++level) {
        const Image& src=p.images.back();
        Image dst;dst.width=src.width/2;dst.height=src.height/2;
        if(dst.width<8||dst.height<8)break;
        dst.pixels.resize(static_cast<size_t>(dst.width)*dst.height);
        for(int y=0;y<dst.height;++y) {
            int x=0;
            for(;x<dst.width;++x) {
#if defined(__ARM_NEON)
                if(x>=1&&x+16<dst.width) {
                    // Interior binomial samples use the identical integer sum;
                    // the first/last columns retain scalar clamped borders.
                    const int cy=y*2;
                    auto horizontal=[&](int yy) {
                        const uint8_t *row=src.pixels.data()+static_cast<size_t>(yy)*src.width+x*2-1;
                        const uint8x16x2_t a=vld2q_u8(row),b=vld2q_u8(row+2);
                        uint16x8x2_t out;
                        out.val[0]=vaddq_u16(vaddl_u8(vget_low_u8(a.val[0]),vget_low_u8(b.val[0])),
                                            vshll_n_u8(vget_low_u8(a.val[1]),1));
                        out.val[1]=vaddq_u16(vaddl_u8(vget_high_u8(a.val[0]),vget_high_u8(b.val[0])),
                                            vshll_n_u8(vget_high_u8(a.val[1]),1));
                        return out;
                    };
                    const auto top=horizontal(std::max(0,cy-1)),middle=horizontal(cy),
                               bottom=horizontal(std::min(src.height-1,cy+1));
                    const uint16x8_t lo=vaddq_u16(vaddq_u16(top.val[0],bottom.val[0]),
                                                vaddq_u16(vshlq_n_u16(middle.val[0],1),vdupq_n_u16(8)));
                    const uint16x8_t hi=vaddq_u16(vaddq_u16(top.val[1],bottom.val[1]),
                                                vaddq_u16(vshlq_n_u16(middle.val[1],1),vdupq_n_u16(8)));
                    vst1q_u8(dst.pixels.data()+static_cast<size_t>(y)*dst.width+x,
                             vcombine_u8(vshrn_n_u16(lo,4),vshrn_n_u16(hi,4)));
                    x+=15;continue;
                }
#endif
            const int cx=x*2,cy=y*2;
            const int left=std::max(0,cx-1),right=std::min(src.width-1,cx+1);
            const int top=std::max(0,cy-1),bottom=std::min(src.height-1,cy+1);
            const auto at=[&](int xx,int yy){return unsigned(src.pixels[static_cast<size_t>(yy)*src.width+xx]);};
            dst.pixels[static_cast<size_t>(y)*dst.width+x]=
                static_cast<uint8_t>((at(left,top)+at(right,top)+at(left,bottom)+at(right,bottom)+
                    2*(at(cx,top)+at(left,cy)+at(right,cy)+at(cx,bottom))+4*at(cx,cy)+8)>>4);
            }
        }
        p.images.push_back(std::move(dst));
    }
}
Pyramid from_rgb(const uint8_t *rgb,int width,int height,int levels) {
    Pyramid p;
    if(!rgb||width<32||height<32||levels<1||levels>8)return p;
    p.source_width=width;p.source_height=height;
    Image base;base.width=width/2;base.height=height/2;
    base.pixels.resize(static_cast<size_t>(base.width)*base.height);
    for(int y=0;y<base.height;++y) {
        int x=0;
#if defined(__ARM_NEON)
        const uint8_t *row=rgb+static_cast<size_t>(y*2)*width*3;
        const uint8_t *next=row+static_cast<size_t>(width)*3;
        for(;x+8<=base.width;x+=8) {
            const uint8x16x3_t a=vld3q_u8(row+x*6),b=vld3q_u8(next+x*6);
            const uint16x8_t r=vaddq_u16(vpaddlq_u8(a.val[0]),vpaddlq_u8(b.val[0]));
            const uint16x8_t g=vaddq_u16(vpaddlq_u8(a.val[1]),vpaddlq_u8(b.val[1]));
            const uint16x8_t c=vaddq_u16(vpaddlq_u8(a.val[2]),vpaddlq_u8(b.val[2]));
            uint32x4_t lo=vmull_n_u16(vget_low_u16(r),77),hi=vmull_n_u16(vget_high_u16(r),77);
            lo=vmlal_n_u16(lo,vget_low_u16(g),150);hi=vmlal_n_u16(hi,vget_high_u16(g),150);
            lo=vmlal_n_u16(lo,vget_low_u16(c),29);hi=vmlal_n_u16(hi,vget_high_u16(c),29);
            const uint16x8_t value=vcombine_u16(vshrn_n_u32(vaddq_u32(lo,vdupq_n_u32(512)),10),
                                               vshrn_n_u32(vaddq_u32(hi,vdupq_n_u32(512)),10));
            vst1_u8(base.pixels.data()+static_cast<size_t>(y)*base.width+x,vmovn_u16(value));
        }
#endif
        for(;x<base.width;++x) {
        const uint8_t *pixel=rgb+(static_cast<size_t>(y*2)*width+x*2)*3;
        const uint8_t *next=pixel+static_cast<size_t>(width)*3;
        const unsigned r=unsigned(pixel[0])+pixel[3]+next[0]+next[3];
        const unsigned g=unsigned(pixel[1])+pixel[4]+next[1]+next[4];
        const unsigned b=unsigned(pixel[2])+pixel[5]+next[2]+next[5];
        base.pixels[static_cast<size_t>(y)*base.width+x]=
            static_cast<uint8_t>((77u*r+150u*g+29u*b+512u)>>10);
        }
    }
    p.images.push_back(std::move(base));finish_pyramid(p,levels);return p;
}
Pyramid from_pl_gray(const uint8_t *gray,int width,int height,int sw,int sh,int levels) {
    Pyramid p;
    if(!gray||sw<32||sh<32||sw%8||sh%8||width!=sw/8*3||height!=sh/8*3||levels<1||levels>8)return p;
    p.source_width=sw;p.source_height=sh;p.scale=4;
    Image base;base.width=sw/4;base.height=sh/4;
    base.pixels.resize(static_cast<size_t>(base.width)*base.height);
    // PL samples [8k,8k+1,8k+2], then skips to 8k+8. Re-grid at source
    // [4j] with the actual nonuniform coordinates; never pretend 3of8 is a
    // geometrically uniform resize. Only this tracking image is interpolated.
    for(int y=0;y<base.height;++y) {
        int y0=y/2*3+(y%2?2:0),y1=std::min(y0+1,height-1),wy=y%2;
        for(int x=0;x<base.width;++x) {
            int x0=x/2*3+(x%2?2:0),x1=std::min(x0+1,width-1),wx=x%2;
            const unsigned a=gray[static_cast<size_t>(y0)*width+x0]*(3-wx)+gray[static_cast<size_t>(y0)*width+x1]*wx;
            const unsigned b=gray[static_cast<size_t>(y1)*width+x0]*(3-wx)+gray[static_cast<size_t>(y1)*width+x1]*wx;
            base.pixels[static_cast<size_t>(y)*base.width+x]=(a*(3-wy)+b*wy+4)/9;
        }
    }
    p.images.push_back(std::move(base));finish_pyramid(p,levels);return p;
}
Pyramid from_rgb_region(const uint8_t *rgb,int width,int height,const int *box,double expansion,int levels) {
    Pyramid p;
    if(!rgb||!box||width<32||height<32||levels<1||levels>8||
       !(expansion>=1)||!std::isfinite(expansion)||box[0]<0||box[1]<0||
       box[2]>width||box[3]>height||box[2]<=box[0]||box[3]<=box[1])return p;
    const double cx=(double(box[0])+box[2])/2,cy=(double(box[1])+box[3])/2;
    const int x0=int(std::max(0.0,std::floor(cx-(box[2]-box[0])*expansion/2)));
    const int x1=int(std::min(double(width),std::ceil(cx+(box[2]-box[0])*expansion/2)));
    const int y0=int(std::max(0.0,std::floor(cy-(box[3]-box[1])*expansion/2)));
    const int y1=int(std::min(double(height),std::ceil(cy+(box[3]-box[1])*expansion/2)));
    p.source_width=width;p.source_height=height;p.scale=1;p.origin_x=float(x0);p.origin_y=float(y0);
    Image base;base.width=x1-x0;base.height=y1-y0;
    base.pixels.resize(static_cast<size_t>(base.width)*base.height);
    for(int y=0;y<base.height;++y) {
        int x=0;
        const uint8_t *row=rgb+(static_cast<size_t>(y+y0)*width+x0)*3;
        uint8_t *dst=base.pixels.data()+static_cast<size_t>(y)*base.width;
#if defined(__ARM_NEON)
        for(;x+8<=base.width;x+=8) {
            const uint8x8x3_t v=vld3_u8(row+x*3);
            uint16x8_t luma=vmull_u8(v.val[0],vdup_n_u8(77));
            luma=vmlal_u8(luma,v.val[1],vdup_n_u8(150));
            luma=vmlal_u8(luma,v.val[2],vdup_n_u8(29));
            vst1_u8(dst+x,vshrn_n_u16(vaddq_u16(luma,vdupq_n_u16(128)),8));
        }
#endif
        for(;x<base.width;++x) {
            const uint8_t *v=row+x*3;
            dst[x]=uint8_t((77u*v[0]+150u*v[1]+29u*v[2]+128u)>>8);
        }
    }
    p.images.push_back(std::move(base));finish_pyramid(p,levels);return p;
}
namespace {
bool critical(int i) { return (i>=36&&i<48)||i==48||i==51||i==54||i==57; }
bool inside(const Image& img,float x,float y,int radius) {
    return std::isfinite(x)&&std::isfinite(y)&&x>=radius+1&&y>=radius+1&&
        x<img.width-radius-2&&y<img.height-radius-2;
}
void patch(const Image& img,float x,float y,int radius,float *output) {
    const int ix=static_cast<int>(x),iy=static_cast<int>(y);
    const float fx=x-ix,fy=y-iy;
    int n=0;
    for(int j=-radius;j<=radius;++j) {
        const uint8_t *row=img.pixels.data()+static_cast<size_t>(iy+j)*img.width+ix-radius;
        const uint8_t *next=row+img.width;
        int i=0;
#if defined(__ARM_NEON)
        // Each lane keeps the scalar interpolation's operation order. No
        // reduction reassociation or approximate reciprocal is introduced.
        auto as_float=[](uint16x4_t v){return vcvtq_f32_u32(vmovl_u16(v));};
        for(;i+8<=radius*2+1;i+=8) {
            const uint16x8_t a=vmovl_u8(vld1_u8(row+i)),b=vmovl_u8(vld1_u8(row+i+1)),
                             c=vmovl_u8(vld1_u8(next+i)),d=vmovl_u8(vld1_u8(next+i+1));
            const float32x4_t a0=as_float(vget_low_u16(a)),a1=as_float(vget_high_u16(a)),
                             c0=as_float(vget_low_u16(c)),c1=as_float(vget_high_u16(c));
            const float32x4_t top0=vaddq_f32(a0,vmulq_n_f32(vsubq_f32(as_float(vget_low_u16(b)),a0),fx)),
                             top1=vaddq_f32(a1,vmulq_n_f32(vsubq_f32(as_float(vget_high_u16(b)),a1),fx));
            const float32x4_t bottom0=vaddq_f32(c0,vmulq_n_f32(vsubq_f32(as_float(vget_low_u16(d)),c0),fx)),
                             bottom1=vaddq_f32(c1,vmulq_n_f32(vsubq_f32(as_float(vget_high_u16(d)),c1),fx));
            vst1q_f32(output+n,vaddq_f32(top0,vmulq_n_f32(vsubq_f32(bottom0,top0),fy)));
            vst1q_f32(output+n+4,vaddq_f32(top1,vmulq_n_f32(vsubq_f32(bottom1,top1),fy)));n+=8;
        }
#endif
        for(;i<radius*2+1;++i) {
            const float top=row[i]+(row[i+1]-float(row[i]))*fx;
            const float bottom=next[i]+(next[i+1]-float(next[i]))*fx;
            output[n++]=top+(bottom-top)*fy;
        }
    }
}
bool one(const Pyramid& before,const Pyramid& after,float x,float y,
         const Policy& p,float& out_x,float& out_y) {
    float dx=0,dy=0;
    bool final_ok=false;
    const int levels=static_cast<int>(std::min(before.images.size(),after.images.size()));
    // Radius is bounded by config validation; arrays have an API safety ceiling.
    // Every consumed element is filled by patch/gradient loops below. Avoid
    // zeroing unused maximum-radius scratch for every landmark and LK pass.
    std::array<float,225> templ,gx,gy,target;
    std::array<float,289> reference;
    for(int level=levels-1;level>=0;--level) {
        const float scale=before.scale*static_cast<float>(1u<<level);
        if(level!=levels-1){dx*=2;dy*=2;}
        else {dx=(before.origin_x-after.origin_x)/scale;dy=(before.origin_y-after.origin_y)/scale;}
        const float px=(x-before.origin_x)/scale,py=(y-before.origin_y)/scale;
        const Image& a=before.images[level];const Image& b=after.images[level];
        if(!inside(a,px,py,p.radius))continue;
        patch(a,px,py,p.radius+1,reference.data());
        const int ref_stride=p.radius*2+3;
        int n=0;float gxx=0,gxy=0,gyy=0;
        for(int j=-p.radius;j<=p.radius;++j)for(int i=-p.radius;i<=p.radius;++i) {
            const int k=(j+p.radius+1)*ref_stride+i+p.radius+1;
            templ[n]=reference[k];
            gx[n]=(reference[k+1]-reference[k-1])*0.5f;
            gy[n]=(reference[k+ref_stride]-reference[k-ref_stride])*0.5f;
            gxx+=gx[n]*gx[n];gxy+=gx[n]*gy[n];gyy+=gy[n]*gy[n];++n;
        }
        const float det=gxx*gyy-gxy*gxy;
        const float eigen=(gxx+gyy-std::sqrt((gxx-gyy)*(gxx-gyy)+4*gxy*gxy))/(2*n);
        if(!(det>0)||!std::isfinite(det)||eigen<p.min_eigen)continue;
        bool ok=true;
        for(int iteration=0;iteration<p.iterations;++iteration) {
            if(!inside(b,px+dx,py+dy,p.radius)){ok=false;break;}
            patch(b,px+dx,py+dy,p.radius,target.data());
            float ex=0,ey=0;int k=0;
            for(k=0;k<n;++k) {
                const float err=target[k]-templ[k];
                ex+=gx[k]*err;ey+=gy[k]*err;
            }
            const float step_x=(gyy*ex-gxy*ey)/det,step_y=(gxx*ey-gxy*ex)/det;
            dx-=step_x;dy-=step_y;
            const float wx=dx*scale+after.origin_x-before.origin_x;
            const float wy=dy*scale+after.origin_y-before.origin_y;
            if(!std::isfinite(dx)||!std::isfinite(dy)||wx*wx+wy*wy>p.max_motion*p.max_motion){ok=false;break;}
            if(step_x*step_x+step_y*step_y<=p.epsilon*p.epsilon)break;
        }
        if(level==0&&ok&&inside(b,px+dx,py+dy,p.radius)) {
            patch(b,px+dx,py+dy,p.radius,target.data());
            float error=0;
            for(int k=0;k<n;++k)error+=std::fabs(target[k]-templ[k]);
            final_ok=error/n<=p.max_error;
        }
    }
    out_x=x+dx*before.scale+after.origin_x-before.origin_x;
    out_y=y+dy*before.scale+after.origin_y-before.origin_y;
    return final_ok&&std::isfinite(out_x)&&std::isfinite(out_y);
}
bool fit(const float *old,Result& out) {
    double ax=0,ay=0,bx=0,by=0;
    for(int i=0;i<68;++i)if(out.observed[i]) {
        ax+=old[i*2];ay+=old[i*2+1];bx+=out.points[i*2];by+=out.points[i*2+1];
    }
    if(out.count<2)return false;
    ax/=out.count;ay/=out.count;bx/=out.count;by/=out.count;
    double denominator=0,dot=0,cross=0;
    for(int i=0;i<68;++i)if(out.observed[i]) {
        const double x=old[i*2]-ax,y=old[i*2+1]-ay;
        const double u=out.points[i*2]-bx,v=out.points[i*2+1]-by;
        denominator+=x*x+y*y;dot+=x*u+y*v;cross+=x*v-y*u;
    }
    if(!(denominator>0))return false;
    out.a=static_cast<float>(dot/denominator);out.b=static_cast<float>(cross/denominator);
    out.tx=static_cast<float>(bx-out.a*ax+out.b*ay);
    out.ty=static_cast<float>(by-out.b*ax-out.a*ay);
    return std::isfinite(out.a)&&std::isfinite(out.b)&&std::isfinite(out.tx)&&std::isfinite(out.ty);
}

bool normal(const float *points,int i,float& nx,float& ny) {
    int left,right;
    if(i>=36&&i<42){left=36+(i-36+5)%6;right=36+(i-36+1)%6;}
    else if(i>=42&&i<48){left=42+(i-42+5)%6;right=42+(i-42+1)%6;}
    else if(i>=48&&i<60){left=48+(i-48+11)%12;right=48+(i-48+1)%12;}
    else return false;
    const float dx=points[right*2]-points[left*2],dy=points[right*2+1]-points[left*2+1];
    const float length=std::hypot(dx,dy);
    if(!(length>0)||!std::isfinite(length))return false;
    nx=-dy/length;ny=dx/length;return true;
}

/* The global image-observed transform supplies tangential motion; only the
 * normal offset is solved from this frame's pixels. A straight edge has an
 * observable normal even when its 2x2 LK matrix is rank deficient. */
bool one_contour(const Pyramid& before,const Pyramid& after,float x,float y,
                 float seed_x,float seed_y,float nx,float ny,const Policy& p,
                 float& out_x,float& out_y) {
    std::array<float,225> templ,gradient,target;
    std::array<float,289> reference;
    float offset=0;bool final_ok=false;
    const int levels=int(std::min(before.images.size(),after.images.size()));
    for(int level=levels-1;level>=0;--level) {
        const float scale=before.scale*float(1u<<level);
        const float px=(x-before.origin_x)/scale,py=(y-before.origin_y)/scale;
        const auto& a=before.images[level];const auto& b=after.images[level];
        if(!inside(a,px,py,p.radius))continue;
        patch(a,px,py,p.radius+1,reference.data());
        const int stride=p.radius*2+3;
        int n=0;float energy=0;
        for(int j=-p.radius;j<=p.radius;++j)for(int i=-p.radius;i<=p.radius;++i) {
            const int k=(j+p.radius+1)*stride+i+p.radius+1;
            templ[n]=reference[k];
            gradient[n]=((reference[k+1]-reference[k-1])*nx+
                         (reference[k+stride]-reference[k-stride])*ny)*.5f;
            energy+=gradient[n]*gradient[n];++n;
        }
        if(!std::isfinite(energy)||!(energy>0)||energy/n<p.min_eigen)continue;
        bool ok=true;
        for(int iteration=0;iteration<p.iterations;++iteration) {
            const float qx=(seed_x+offset*nx-after.origin_x)/scale,
                        qy=(seed_y+offset*ny-after.origin_y)/scale;
            if(!inside(b,qx,qy,p.radius)){ok=false;break;}
            patch(b,qx,qy,p.radius,target.data());
            float residual=0;
            for(int k=0;k<n;++k)residual+=gradient[k]*(target[k]-templ[k]);
            const float step=residual/energy;
            offset-=step*scale;
            const float dx=seed_x+offset*nx-x,dy=seed_y+offset*ny-y;
            if(!std::isfinite(offset)||dx*dx+dy*dy>p.max_motion*p.max_motion){ok=false;break;}
            if(step*step<=p.epsilon*p.epsilon)break;
        }
        const float qx=(seed_x+offset*nx-after.origin_x)/scale,
                    qy=(seed_y+offset*ny-after.origin_y)/scale;
        if(level==0&&ok&&inside(b,qx,qy,p.radius)) {
            patch(b,qx,qy,p.radius,target.data());
            float error=0;
            for(int k=0;k<n;++k)error+=std::fabs(target[k]-templ[k]);
            final_ok=error/n<=p.max_error;
        }
    }
    out_x=seed_x+offset*nx;out_y=seed_y+offset*ny;
    return final_ok&&std::isfinite(out_x)&&std::isfinite(out_y);
}
}
static bool update_impl(const Pyramid& before,const Pyramid& after,const float *points,
                        const Policy& p,Result& out,bool contours) {
    out=Result{};
    if(!points||before.images.empty()||after.images.empty()||
       before.source_width!=after.source_width||before.source_height!=after.source_height||
       before.scale!=after.scale||
       p.radius<1||p.radius>7||p.iterations<1||p.iterations>32||
       !(p.min_eigen>0)||!(p.max_error>0)||!(p.fb_error>0)||
       !(p.min_fraction>0&&p.min_fraction<=1)||!(p.max_motion>0)||!(p.epsilon>0))return false;
    // Any failed critical eye/MAR point rejects the ordinary update. Evaluate
    // those independent observations first so ERT recovery does not also pay
    // for 36 noncritical points on an already unusable frame. Fit/successful
    // accumulation below retain their original landmark-index order.
    static constexpr std::array<int,68> critical_first{{
        36,37,38,39,40,41,42,43,44,45,46,47,48,51,54,57,
        0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,
        49,50,52,53,55,56,58,59,60,61,62,63,64,65,66,67}};
    static_assert([]{std::array<bool,68> seen{};for(int i:critical_first){if(i<0||i>=68||seen[i])return false;seen[i]=true;}return true;}(),"invalid LK evaluation order");
    for(int order=0;order<68;++order) {
        const int i=contours?order:critical_first[order];
        float x,y;
        bool valid=one(before,after,points[i*2],points[i*2+1],p,x,y);
        int stage=valid?0:1;
        if(valid&&critical(i)) {
            float bx,by;
            valid=one(after,before,x,y,p,bx,by)&&
                (bx-points[i*2])*(bx-points[i*2])+(by-points[i*2+1])*(by-points[i*2+1])<=p.fb_error*p.fb_error;
            if(!valid)stage=2;
        }
        out.observed[i]=valid;
        if(valid){out.points[i*2]=x;out.points[i*2+1]=y;++out.count;}
        else if(critical(i)){
            out.rejected_index=i;out.rejected_stage=stage;
            if(!contours)return false;
        }
    }
    // The original configured majority must be observed by full 2D LK, even
    // in the contour candidate. Normal-only observations cannot invent a pose.
    if(out.count<68*p.min_fraction||!fit(points,out)){out.rejected_stage=3;return false;}
    if(contours)for(int i=0;i<68;++i)if(critical(i)&&!out.observed[i]) {
        const float x=points[i*2],y=points[i*2+1];
        float nx,ny;
        if(!normal(points,i,nx,ny)){out.rejected_index=i;out.rejected_stage=4;return false;}
        const float length=std::hypot(out.a,out.b);
        if(!(length>0)||!std::isfinite(length))return false;
        const float mx=(out.a*nx-out.b*ny)/length,my=(out.b*nx+out.a*ny)/length;
        float qx,qy,bx,by;
        const bool valid=one_contour(before,after,x,y,out.a*x-out.b*y+out.tx,
                out.b*x+out.a*y+out.ty,mx,my,p,qx,qy)&&
            one_contour(after,before,qx,qy,x,y,nx,ny,p,bx,by)&&
            (bx-x)*(bx-x)+(by-y)*(by-y)<=p.fb_error*p.fb_error;
        if(!valid){out.rejected_index=i;out.rejected_stage=4;return false;}
        out.points[i*2]=qx;out.points[i*2+1]=qy;out.observed[i]=true;
        ++out.count;++out.contour_count;
    }
    for(int i=0;i<68;++i)if(!out.observed[i]) {
        out.points[i*2]=out.a*points[i*2]-out.b*points[i*2+1]+out.tx;
        out.points[i*2+1]=out.b*points[i*2]+out.a*points[i*2+1]+out.ty;
    }
    out.rejected_index=-1;out.rejected_stage=0;
    return true;
}
bool update(const Pyramid& before,const Pyramid& after,const float *points,
            const Policy& p,Result& out) {
    return update_impl(before,after,points,p,out,false);
}
bool update_contours(const Pyramid& before,const Pyramid& after,const float *points,
                     const Policy& p,Result& out) {
    return update_impl(before,after,points,p,out,true);
}
}
