/* Synthetic 640x480 conversion microbenchmark; not camera performance. */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
int convert_before(const uint8_t *,size_t,unsigned,uint8_t *,unsigned,unsigned,int);
int convert_after(const uint8_t *,size_t,unsigned,uint8_t *,unsigned,unsigned,int);
static double cpu_ms(void){struct timespec t;if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))exit(1);return t.tv_sec*1000.+t.tv_nsec/1e6;}
int main(void) {
    const unsigned w=640,h=480;const size_t n=(size_t)w*h;
    uint8_t *src=malloc(n*2),*a=malloc(n*3),*b=malloc(n*3);if(!src||!a||!b)return 1;
    uint32_t rng=192061102;
    for(size_t i=0;i<n*2;++i){rng=rng*1664525u+1013904223u;src[i]=(uint8_t)(rng>>24);}
    for(int limited=0;limited<=1;++limited) {
        double before=0,after=0;
        for(int k=0;k<40;++k) {
            double t=cpu_ms();if(convert_before(src,n*2,w*2,a,w,h,limited))return 1;before+=cpu_ms()-t;
            t=cpu_ms();if(convert_after(src,n*2,w*2,b,w,h,limited))return 1;after+=cpu_ms()-t;
            if(memcmp(a,b,n*3))return 1;
        }
        printf("YUYV_CONVERT_BENCH limited=%d iterations=40 before_cpu_ms=%.6f after_cpu_ms=%.6f rgb_byte_parity=1 synthetic_only=1\n",limited,before/40,after/40);
    }
    free(src);free(a);free(b);return 0;
}
