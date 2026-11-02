/* Exact mean parity for full bright frames, packed patterns and random data.
 * Host test only. Does not demonstrate DMA ordering or ARM performance. */
#include "../dma_gray.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void compare(const uint32_t *words, size_t count) {
    const unsigned char *bytes = (const unsigned char *)words;
    uint64_t expected = 0;
    for (size_t i = 0; i < count * sizeof(*words); ++i) expected += bytes[i];
    assert(vg_dma_gray_sum_words(words, count) == expected);
    uint32_t *copy=malloc(count*sizeof(*copy));assert(copy);
    assert(vg_dma_gray_copy_sum_words(copy,words,count)==expected);
    assert(!memcmp(copy,words,count*sizeof(*copy)));free(copy);
#if defined(__ARM_NEON)
    copy=malloc(count*sizeof(*copy));assert(copy);
    assert(vg_dma_gray_copy_sum_neon(copy,words,count)==expected);
    assert(!memcmp(copy,words,count*sizeof(*copy)));free(copy);
#endif
}
int main(void) {
    const size_t count = 480u * 270u / 4;
    uint32_t *words = malloc(count * sizeof(*words));
    assert(words);
    assert(vg_dma_gray_sum_words(NULL, 0) == 0);
    for (unsigned value = 0; value <= 255; ++value) {
        memset(words, (int)value, count * sizeof(*words));
        compare(words, count);
    }
    uint32_t seed = 0x681247u;
    for (size_t i = 0; i < count; ++i) {
        seed = seed * 1664525u + 1013904223u;
        words[i] = seed;
    }
    compare(words, count);
    for (size_t n = 1; n < 129; ++n) compare(words, n);
    free(words);
    puts("DMA_GRAY_TEST_PASS byte_sum_exact full_frame_values=256 random=1 prefixes=128");
    return 0;
}
