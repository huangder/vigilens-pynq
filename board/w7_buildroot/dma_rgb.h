#ifndef VIGILENS_DMA_RGB_H
#define VIGILENS_DMA_RGB_H
#include <stddef.h>
#include <stdint.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
/* CPU-owned reserved DDR only, never an MMIO register bank. Caller observes
 * the existing completion fence before starting the DMA engine. */
static inline void vg_dma_rgb_copy_neon(volatile uint8_t *out,const uint8_t *in,size_t size) {
    size_t i=0;
    for(;i+64<=size;i+=64) {
        const uint8x16_t a=vld1q_u8(in+i),b=vld1q_u8(in+i+16),
                         c=vld1q_u8(in+i+32),d=vld1q_u8(in+i+48);
        vst1q_u8((uint8_t *)out+i,a);vst1q_u8((uint8_t *)out+i+16,b);
        vst1q_u8((uint8_t *)out+i+32,c);vst1q_u8((uint8_t *)out+i+48,d);
        __asm__ __volatile__("" ::: "memory");
    }
    for(;i<size;++i)out[i]=in[i];
}
#endif
#endif
