/* Read-only post-run reserved-DDR probe. No register access or DMA launch. */
#define _POSIX_C_SOURCE 200809L
#include "../dma_gray.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(void) {
    const size_t bytes=480u*270u,words=bytes/4;
    int fd=open("/dev/mem",O_RDONLY|O_SYNC);if(fd<0){perror("open");return 1;}
    const volatile uint32_t *data=mmap(NULL,bytes,PROT_READ,MAP_SHARED,fd,0x12400000);
    if((const void *)data==MAP_FAILED){perror("mmap");close(fd);return 1;}
    uint32_t *word=malloc(bytes),*neon=malloc(bytes);if(!word||!neon)return 1;
    double start=now();uint64_t a=vg_dma_gray_copy_sum_words(word,data,words);double word_ms=(now()-start)*1000;
#if defined(__ARM_NEON)
    start=now();uint64_t b=vg_dma_gray_copy_sum_neon(neon,data,words);double neon_ms=(now()-start)*1000;
    if(a!=b||memcmp(word,neon,bytes)){fputs("DMA_GRAY_MAPPING_FAIL mismatch\n",stderr);return 1;}
    printf("DMA_GRAY_MAPPING_PASS sum=%llu word_ms=%.6f neon_ms=%.6f bytes=%zu\n",(unsigned long long)a,word_ms,neon_ms,bytes);
#else
    (void)a;fputs("DMA_GRAY_MAPPING_FAIL no NEON\n",stderr);return 1;
#endif
    free(word);free(neon);munmap((void *)data,bytes);close(fd);return 0;
}
