#ifndef VG_YUYV_RGB_H
#define VG_YUYV_RGB_H
#include <stddef.h>
#include <stdint.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif
/* V4L2 YUYV = Y0,Cb,Y1,Cr. Caller checks BT.601 and negotiated range.
 * RGB888 order matches the existing PL/landmark contract. */
static inline uint8_t vg_clip_q8(int v) {return v<=0?0:v>=255*256?255:(uint8_t)(v/256);}
static inline void vg_yuyv_pixel(int y,int u,int v,int limited,uint8_t *out) {
    int c=limited?(y>16?y-16:0):y,d=u-128,e=v-128;
    int k=limited?298:256;
    out[0]=vg_clip_q8(k*c+(limited?409:359)*e+128);
    out[1]=vg_clip_q8(k*c-(limited?100:88)*d-(limited?208:183)*e+128);
    out[2]=vg_clip_q8(k*c+(limited?516:454)*d+128);
}
static inline void vg_yuyv_row_scalar(const uint8_t *in,uint8_t *out,unsigned w,int limited) {
    for(unsigned x=0;x<w;x+=2) {
        vg_yuyv_pixel(in[0],in[1],in[3],limited,out);
        vg_yuyv_pixel(in[2],in[1],in[3],limited,out+3);
        in+=4;out+=6;
    }
}
#if defined(__ARM_NEON)
static inline uint8x8_t vg_yuyv_channel(int16x8_t y,int16x8_t d,int16x8_t e,int ky,int kd,int ke) {
    int32x4_t lo=vmull_n_s16(vget_low_s16(y),ky),hi=vmull_n_s16(vget_high_s16(y),ky);
    lo=vmlal_n_s16(lo,vget_low_s16(d),kd);hi=vmlal_n_s16(hi,vget_high_s16(d),kd);
    lo=vmlal_n_s16(lo,vget_low_s16(e),ke);hi=vmlal_n_s16(hi,vget_high_s16(e),ke);
    lo=vaddq_s32(lo,vdupq_n_s32(128));hi=vaddq_s32(hi,vdupq_n_s32(128));
    return vqmovn_u16(vcombine_u16(vqshrun_n_s32(lo,8),vqshrun_n_s32(hi,8)));
}
#endif
static inline void vg_yuyv_row(const uint8_t *in,uint8_t *out,unsigned w,int limited) {
    unsigned x=0;
#if defined(__ARM_NEON)
    for(;x+16<=w;x+=16,in+=32,out+=48) {
        const uint8x8x4_t a=vld4_u8(in);
        int16x8_t y0=vreinterpretq_s16_u16(vmovl_u8(a.val[0])),y1=vreinterpretq_s16_u16(vmovl_u8(a.val[2]));
        const int16x8_t d=vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(a.val[1])),vdupq_n_s16(128));
        const int16x8_t e=vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(a.val[3])),vdupq_n_s16(128));
        if(limited) {
            y0=vmaxq_s16(vsubq_s16(y0,vdupq_n_s16(16)),vdupq_n_s16(0));
            y1=vmaxq_s16(vsubq_s16(y1,vdupq_n_s16(16)),vdupq_n_s16(0));
        }
        const int ky=limited?298:256;
        uint8x16x3_t rgb;
        const uint8x8x2_t r=vzip_u8(vg_yuyv_channel(y0,d,e,ky,0,limited?409:359),vg_yuyv_channel(y1,d,e,ky,0,limited?409:359));
        const uint8x8x2_t g=vzip_u8(vg_yuyv_channel(y0,d,e,ky,limited?-100:-88,limited?-208:-183),vg_yuyv_channel(y1,d,e,ky,limited?-100:-88,limited?-208:-183));
        const uint8x8x2_t b=vzip_u8(vg_yuyv_channel(y0,d,e,ky,limited?516:454,0),vg_yuyv_channel(y1,d,e,ky,limited?516:454,0));
        rgb.val[0]=vcombine_u8(r.val[0],r.val[1]);rgb.val[1]=vcombine_u8(g.val[0],g.val[1]);rgb.val[2]=vcombine_u8(b.val[0],b.val[1]);
        vst3q_u8(out,rgb);
    }
#endif
    vg_yuyv_row_scalar(in,out,w-x,limited);
}
static inline int vg_yuyv_rgb(const uint8_t *in,size_t bytes,unsigned stride,uint8_t *out,unsigned w,unsigned h,int limited) {
    if(!in||!out||!w||!h||w%2||stride<(size_t)w*2||bytes<(size_t)(h-1)*stride+(size_t)w*2)return -1;
    for(unsigned y=0;y<h;++y)vg_yuyv_row(in+(size_t)y*stride,out+(size_t)y*w*3,w,limited);
    return 0;
}
#endif
