/* Synthetic RGB transport fixture. No camera or physiological claims. */
#define _POSIX_C_SOURCE 200809L
#include "camera_preview.h"
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jpeglib.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line=%d %s\n",__LINE__,#x); abort(); } } while (0)
enum { WIDTH=32, HEIGHT=24 };
static unsigned char red[WIDTH*HEIGHT*3], blue[WIDTH*HEIGHT*3];
static const char *target;
static atomic_int finished;
static int expect_blue;
static void *publish_frames(void *unused) {
    (void)unused;
    for (int i=0;i<60;++i)
        CHECK(vg_preview_publish(target,i%2 ? red : blue,WIDTH,HEIGHT,80)==0);
    atomic_store(&finished,1);
    return NULL;
}
static void read_frame(void) {
    struct jpeg_decompress_struct decoder;
    struct jpeg_error_mgr error;
    unsigned char row[WIDTH*3];
    JSAMPROW rows[]={row};
    FILE *input=fopen(target,"rb");
    CHECK(input);
    decoder.err=jpeg_std_error(&error);
    jpeg_create_decompress(&decoder);
    jpeg_stdio_src(&decoder,input);
    CHECK(jpeg_read_header(&decoder,TRUE)==JPEG_HEADER_OK);
    decoder.out_color_space=JCS_RGB;
    jpeg_start_decompress(&decoder);
    CHECK(decoder.output_width==WIDTH && decoder.output_height==HEIGHT && decoder.output_components==3);
    while (decoder.output_scanline<decoder.output_height) {
        CHECK(jpeg_read_scanlines(&decoder,rows,1)==1);
        CHECK((row[0]>200 && row[2]<50) || (row[2]>200 && row[0]<50));
        if(expect_blue)CHECK(row[2]>200&&row[0]<50);
    }
    CHECK(jpeg_finish_decompress(&decoder));
    jpeg_destroy_decompress(&decoder);
    fclose(input);
}
int main(int argc,char **argv) {
    pthread_t producer;
    unsigned reads=0;
    CHECK(argc==2);target=argv[1];
    for (int i=0;i<WIDTH*HEIGHT;++i) { red[3*i]=255;blue[3*i+2]=255; }
    CHECK(vg_preview_publish(target,red,WIDTH,HEIGHT,80)==0);
    CHECK(vg_preview_publish(target,red,0,HEIGHT,80)==-EINVAL);
    CHECK(vg_preview_publish(target,red,WIDTH,HEIGHT,0)==-EINVAL);
    CHECK(vg_preview_publish("/missing-preview-directory/frame.jpg",red,WIDTH,HEIGHT,80)!=0);
    read_frame();
    {
        FILE *source=fopen(target,"rb");CHECK(source);CHECK(!fseek(source,0,SEEK_END));
        long size=ftell(source);CHECK(size>4);rewind(source);
        unsigned char *bytes=malloc((size_t)size);CHECK(bytes);
        CHECK(fread(bytes,1,(size_t)size,source)==(size_t)size);CHECK(!fclose(source));
        CHECK(!vg_preview_publish_jpeg(target,bytes,(size_t)size));read_frame();
        source=fopen(target,"rb");CHECK(source);
        for(long i=0;i<size;++i){CHECK(fgetc(source)==bytes[i]);}
        CHECK(fgetc(source)==EOF);fclose(source);
        CHECK(vg_preview_publish_jpeg(target,bytes,0)==-EINVAL);
        bytes[0]=0;CHECK(vg_preview_publish_jpeg(target,bytes,(size_t)size)==-EINVAL);free(bytes);
        puts("PREVIEW_PASSTHROUGH_PASS exact_encoded_bytes=1 original_rgb_encoder_retained=1");
    }
    CHECK(!pthread_create(&producer,NULL,publish_frames,NULL));
    while (!atomic_load(&finished) || reads<60) { read_frame();++reads; }
    CHECK(!pthread_join(producer,NULL));
    read_frame();
    {
        unsigned char changing[WIDTH*HEIGHT*3];
        vg_preview_async *worker=vg_preview_async_open(target,80,0);
        CHECK(worker);
        CHECK(vg_preview_async_submit(worker,red,0,HEIGHT)==-EINVAL);
        for(int i=0;i<1000;++i) {
            memcpy(changing,i%2?red:blue,sizeof(changing));
            CHECK(vg_preview_async_submit(worker,changing,WIDTH,HEIGHT)==0);
            memset(changing,0,sizeof(changing));
        }
        memcpy(changing,blue,sizeof(changing));
        CHECK(vg_preview_async_submit(worker,changing,WIDTH,HEIGHT)==0);
        memset(changing,0,sizeof(changing));
        vg_preview_async_close(worker);
        expect_blue=1;read_frame();
        CHECK(!vg_preview_async_open(target,0,0));
        CHECK(!vg_preview_async_open(target,80,20));
        vg_preview_async_close(NULL);
        puts("PREVIEW_ASYNC_PASS latest_pending_drained=1 caller_rgb_reused_immediately=1 private_buffers=2");
    }
    puts("PREVIEW_JPEG_PASS synthetic_RGB_color_dimensions_atomic_readers_and_errors");
    return 0;
}
