#ifndef VIGILENS_DMA_GRAY_H
#define VIGILENS_DMA_GRAY_H
#include <stddef.h>
#include <stdint.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif

/* Completed DMA DDR, aligned to 32 bits. Read each word exactly once instead
 * of issuing four uncached byte reads. Endianness does not affect the sum.
 * The caller must first observe the existing DMA/IP completion barriers. */
static inline uint64_t vg_dma_gray_sum_words(const volatile uint32_t *data, size_t words) {
    uint64_t sum = 0;
    for (size_t i = 0; i < words; ++i) {
        uint32_t word = data[i];
        sum += (word & 255u) + ((word >> 8) & 255u) +
               ((word >> 16) & 255u) + (word >> 24);
    }
    return sum;
}
/* Preserve a cached gray snapshot with the same single pass over uncached DDR.
 * Destination belongs to the same leased pool slot as its RGB and PL metadata. */
static inline uint64_t vg_dma_gray_copy_sum_words(uint32_t *out,const volatile uint32_t *data,size_t words) {
    uint64_t sum=0;
    for(size_t i=0;i<words;++i) {
        const uint32_t word=data[i];out[i]=word;
        sum+=(word&255u)+((word>>8)&255u)+((word>>16)&255u)+(word>>24);
    }
    return sum;
}
#if defined(__ARM_NEON)
/* Real reserved-DDR byte parity is covered by dma_gray_mapping_bench.c.
 * The caller still supplies the DMA completion fence before reading. */
static inline uint64_t vg_dma_gray_copy_sum_neon(uint32_t *out,const volatile uint32_t *data,size_t words) {
    uint64x2_t sum=vdupq_n_u64(0);size_t i=0;
    for(;i+4<=words;i+=4) {
        __asm__ __volatile__("" ::: "memory");
        const uint8x16_t v=vld1q_u8((const uint8_t *)(data+i));
        vst1q_u8((uint8_t *)(out+i),v);
        sum=vpadalq_u32(sum,vpaddlq_u16(vpaddlq_u8(v)));
    }
    uint64_t result=vgetq_lane_u64(sum,0)+vgetq_lane_u64(sum,1);
    for(;i<words;++i) {
        uint32_t word=data[i];out[i]=word;
        result+=(word&255u)+((word>>8)&255u)+((word>>16)&255u)+(word>>24);
    }
    return result;
}
#endif
#endif
