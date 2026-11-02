/* Isolated JPEG decoder benchmark. Use procedural fixtures only; writes
 * decoded synthetic RGB for cmp. Not a camera/full-pipeline benchmark. */
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <jpeglib.h>
#include <setjmp.h>
#include <time.h>

struct error {struct jpeg_error_mgr base;jmp_buf jump;};
static void fail(j_common_ptr c){longjmp(((struct error*)c->err)->jump,1);}
static double cpu_ms(void){struct timespec t;if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))exit(1);return t.tv_sec*1000.+t.tv_nsec/1e6;}
static int decode(const unsigned char *data,size_t bytes,unsigned char *rgb) {
    struct jpeg_decompress_struct c={0};struct error e={0};
    c.err=jpeg_std_error(&e.base);e.base.error_exit=fail;
    if(setjmp(e.jump)){jpeg_destroy_decompress(&c);return -1;}
    jpeg_create_decompress(&c);jpeg_mem_src(&c,data,bytes);
    if(jpeg_read_header(&c,TRUE)!=JPEG_HEADER_OK){jpeg_destroy_decompress(&c);return -1;}
    c.out_color_space=JCS_RGB;c.dct_method=JDCT_IFAST;c.do_fancy_upsampling=FALSE;
    jpeg_start_decompress(&c);
    if(c.output_width!=1280||c.output_height!=720||c.output_components!=3){jpeg_destroy_decompress(&c);return -1;}
    while(c.output_scanline<c.output_height){JSAMPROW row=rgb+(size_t)c.output_scanline*1280*3;if(jpeg_read_scanlines(&c,&row,1)!=1){jpeg_destroy_decompress(&c);return -1;}}
    jpeg_finish_decompress(&c);jpeg_destroy_decompress(&c);return 0;
}
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    FILE *input=fopen(argv[1],"rb"),*out=fopen(argv[2],"wb");
    if(!input||!out)return 2;
    unsigned char *rgb=malloc(1280u*720*3);if(!rgb)return 1;
    char line[256],name[64],extra;size_t bytes;unsigned n=0;
    while(fgets(line,sizeof(line),input)) {
        if(sscanf(line,"%63s %zu %c",name,&bytes,&extra)!=2||bytes<1||bytes>16u*1024*1024)return 2;
        unsigned char *data=malloc(bytes);if(!data)return 1;
        if(fread(data,1,bytes,input)!=bytes)return 2;
        if(decode(data,bytes,rgb))return 1;
        double total=0,maximum=0;
        for(unsigned repeat=0;repeat<6;++repeat) {
            double start=cpu_ms();if(decode(data,bytes,rgb))return 1;
            double duration=cpu_ms()-start;total+=duration;if(duration>maximum)maximum=duration;
        }
        if(fwrite(rgb,1,1280u*720*3,out)!=1280u*720*3)return 1;
        printf("{\"fixture\":\"%s\",\"bytes\":%zu,\"cpu_ms_mean\":%.6f,\"cpu_ms_max\":%.6f,\"repeats\":6,\"synthetic_only\":true}\n",name,bytes,total/6,maximum);
        free(data);++n;
    }
    if(ferror(input)||fclose(input)||fclose(out))return 1;
    free(rgb);return n?0:2;
}
