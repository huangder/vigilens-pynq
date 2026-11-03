#define _POSIX_C_SOURCE 200809L
#include "../jpeg_rows.h"
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void decode(const unsigned char *encoded,unsigned long bytes,unsigned char *output,int grouped,int fast) {
    struct jpeg_decompress_struct d={0};struct jpeg_error_mgr error;
    d.err=jpeg_std_error(&error);jpeg_create_decompress(&d);jpeg_mem_src(&d,encoded,bytes);
    assert(jpeg_read_header(&d,TRUE)==JPEG_HEADER_OK);d.out_color_space=JCS_RGB;
    if(fast){d.dct_method=JDCT_IFAST;d.do_fancy_upsampling=FALSE;}
    assert(jpeg_start_decompress(&d));
    if(grouped)assert(!vg_jpeg_read_rgb_rows(&d,output,(size_t)d.output_width*3));
    else while(d.output_scanline<d.output_height) {
        JSAMPROW row=output+(size_t)d.output_scanline*d.output_width*3;
        assert(jpeg_read_scanlines(&d,&row,1)==1);
    }
    assert(jpeg_finish_decompress(&d));jpeg_destroy_decompress(&d);
}
int main(void) {
    unsigned cases=0;
    const int sizes[][2]={{33,31},{640,480},{1280,720}};
    for(unsigned size=0;size<3;++size)for(int subsample=0;subsample<3;++subsample) {
        int width=sizes[size][0],height=sizes[size][1];size_t bytes=(size_t)width*height*3;
        unsigned char *rgb=malloc(bytes),*first=malloc(bytes),*grouped=malloc(bytes),*encoded=NULL;
        unsigned long length=0;assert(rgb&&first&&grouped);
        uint32_t seed=17;
        for(size_t i=0;i<bytes;++i){seed=1664525u*seed+1013904223u;rgb[i]=seed>>24;}
        struct jpeg_compress_struct c={0};struct jpeg_error_mgr error;
        c.err=jpeg_std_error(&error);jpeg_create_compress(&c);jpeg_mem_dest(&c,&encoded,&length);
        c.image_width=width;c.image_height=height;c.input_components=3;c.in_color_space=JCS_RGB;
        jpeg_set_defaults(&c);jpeg_set_quality(&c,80,TRUE);
        c.comp_info[0].h_samp_factor=subsample==0?1:2;c.comp_info[0].v_samp_factor=subsample==2?2:1;
        jpeg_start_compress(&c,TRUE);
        while(c.next_scanline<c.image_height){JSAMPROW row=rgb+(size_t)c.next_scanline*width*3;assert(jpeg_write_scanlines(&c,&row,1)==1);}
        jpeg_finish_compress(&c);jpeg_destroy_compress(&c);
        for(int fast=0;fast<2;++fast) {
            decode(encoded,length,first,0,fast);decode(encoded,length,grouped,1,fast);
            assert(!memcmp(first,grouped,bytes));++cases;
        }
        if(size==1&&subsample==2)for(int batch=0;batch<2;++batch) {
            double start=now();for(int n=0;n<60;++n)decode(encoded,length,grouped,batch,1);
            printf("JPEG_ROWS_BENCH grouped=%d frames=60 elapsed_ms=%.3f\n",batch,(now()-start)*1000);
        }
        free(encoded);free(rgb);free(first);free(grouped);
    }
    puts("JPEG_ROWS_TEST_PASS exact_RGB_cases=18 sampling=444,422,420 fast_and_accurate=1 odd_dimensions=1");
    assert(cases==18);return 0;
}
