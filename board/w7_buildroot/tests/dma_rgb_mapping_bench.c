#define _POSIX_C_SOURCE 200809L
#include "../dma_rgb.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static double seconds(void) {
    struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))exit(2);
    return t.tv_sec+t.tv_nsec/1e9;
}
int main(void) {
#if !defined(__ARM_NEON)
    return 2;
#else
    const size_t size=1280u*720u*3u;
    int fd=open("/dev/mem",O_RDWR|O_SYNC);
    if(fd<0){perror("open");return 2;}
    uint8_t *mapped=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0x12000000);
    uint8_t *source=malloc(size+64);
    if(mapped==MAP_FAILED||!source)return 2;
    unsigned rng=63895;
    for(size_t i=0;i<size+64;++i){rng=1664525u*rng+1013904223u;source[i]=rng>>24;}
    for(int trial=0;trial<6;++trial) {
        const uint8_t *input=source+trial;
        double start=seconds();
        if(trial%2)vg_dma_rgb_copy_neon(mapped,input,size);else memcpy(mapped,input,size);
        __sync_synchronize();
        const double elapsed=(seconds()-start)*1000;
        if(memcmp(mapped,input,size)){fputs("DMA_RGB_MAPPING_FAIL\n",stderr);return 1;}
        printf("DMA_RGB_MAPPING_SAMPLE mode=%s ms=%.6f bytes=%zu exact=1\n",trial%2?"neon":"memcpy",elapsed,size);
    }
    // Exercise tails and source alignments against byte guards in real DDR.
    for(size_t n=1;n<=129;++n) {
        memset(mapped,0xa5,192);
        vg_dma_rgb_copy_neon(mapped+16,source+(n%16),n);
        __sync_synchronize();
        if(memcmp(mapped+16,source+(n%16),n)||mapped[15]!=0xa5||mapped[16+n]!=0xa5)return 1;
    }
    puts("DMA_RGB_MAPPING_PASS full_frame_exact=1 tails=129 guards=1");
    munmap(mapped,size);close(fd);free(source);return 0;
#endif
}
