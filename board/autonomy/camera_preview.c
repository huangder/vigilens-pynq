#define _POSIX_C_SOURCE 200809L
#include "camera_preview.h"
#include <errno.h>
#include <limits.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <jpeglib.h>
#include <pthread.h>
#include <sys/resource.h>
#include <inttypes.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

typedef struct {
    struct jpeg_compress_struct codec;
    struct jpeg_error_mgr error;
    jmp_buf failure;
    unsigned char *encoded;
    unsigned long size;
} encoder;

static void jpeg_failure(j_common_ptr codec) {
    encoder *state = (encoder *)codec;
    longjmp(state->failure, 1);
}

static int encode_rgb(encoder *state, const unsigned char *rgb,
                      int width, int height, size_t stride, int quality) {
    if (setjmp(state->failure)) return -EIO;
    jpeg_create_compress(&state->codec);
    jpeg_mem_dest(&state->codec, &state->encoded, &state->size);
    state->codec.image_width = (JDIMENSION)width;
    state->codec.image_height = (JDIMENSION)height;
    state->codec.input_components = 3;
    state->codec.in_color_space = JCS_RGB;
    jpeg_set_defaults(&state->codec);
    jpeg_set_quality(&state->codec, quality, TRUE);
    jpeg_start_compress(&state->codec, TRUE);
    while (state->codec.next_scanline < state->codec.image_height) {
        JSAMPROW row = (JSAMPROW)(rgb + state->codec.next_scanline * stride);
        if (jpeg_write_scanlines(&state->codec, &row, 1) != 1) return -EIO;
    }
    jpeg_finish_compress(&state->codec);
    return 0;
}

int vg_preview_publish_jpeg(const char *path,const unsigned char *jpeg,size_t size) {
    char *temporary = NULL;
    FILE *output = NULL;
    int descriptor = -1, result = -EIO;
    if(!path||!*path||strlen(path)>PATH_MAX-12||!jpeg||size<4||jpeg[0]!=0xff||jpeg[1]!=0xd8)
        return -EINVAL;
    temporary = malloc(strlen(path) + 12);
    if (!temporary) { result = -ENOMEM; goto cleanup; }
    snprintf(temporary, strlen(path) + 12, "%s.XXXXXX", path);
    descriptor = mkstemp(temporary);
    if (descriptor < 0) goto cleanup;
    output = fdopen(descriptor, "wb");
    if (!output) goto cleanup;
    descriptor = -1;
    if (fwrite(jpeg, 1, size, output) != size) goto cleanup;
    if (fclose(output)) { output = NULL; goto cleanup; }
    output = NULL;
    if (rename(temporary, path)) goto cleanup;
    result = 0;
cleanup:
    if (output) fclose(output);
    if (descriptor >= 0) close(descriptor);
    if (temporary) { unlink(temporary); free(temporary); }
    return result;
}
int vg_preview_publish(const char *path,const unsigned char *rgb,int width,int height,int quality) {
    if(!path||!*path||!rgb||width<=0||height<=0||width>JPEG_MAX_DIMENSION||height>JPEG_MAX_DIMENSION||quality<1||quality>100||strlen(path)>PATH_MAX-12)return -EINVAL;
    const size_t stride=(size_t)width*3;
    if((size_t)height>SIZE_MAX/stride)return -EOVERFLOW;
    encoder *state=calloc(1,sizeof(*state));if(!state)return -ENOMEM;
    state->codec.err=jpeg_std_error(&state->error);state->error.error_exit=jpeg_failure;
    int result=-EIO;
    if(!encode_rgb(state,rgb,width,height,stride,quality))result=vg_preview_publish_jpeg(path,state->encoded,state->size);
    if (state->codec.mem) jpeg_destroy_compress(&state->codec);
    free(state->encoded);
    free(state);
    return result;
}

struct vg_preview_async {
    pthread_mutex_t mutex;
    pthread_cond_t ready;
    pthread_t thread;
    unsigned char *active,*queued;
    size_t active_capacity,queued_capacity;
    char *path;
    int quality,nice_level,width,height,pending,stopping;
    uint64_t submitted,encoded,replaced,failed;
};
static void *preview_run(void *argument) {
#ifdef __linux__
    (void)prctl(PR_SET_NAME,"vg_preview",0,0,0);
#endif
    vg_preview_async *w=argument;
    if(w->nice_level && setpriority(PRIO_PROCESS,0,w->nice_level))
        fputs("PREVIEW_ASYNC_NICE_FAILED\n",stderr);
    for(;;) {
        int width,height;
        pthread_mutex_lock(&w->mutex);
        while(!w->pending&&!w->stopping)pthread_cond_wait(&w->ready,&w->mutex);
        if(!w->pending&&w->stopping){pthread_mutex_unlock(&w->mutex);break;}
        unsigned char *pixels=w->active;size_t capacity=w->active_capacity;
        w->active=w->queued;w->active_capacity=w->queued_capacity;
        w->queued=pixels;w->queued_capacity=capacity;
        width=w->width;height=w->height;w->pending=0;
        pthread_mutex_unlock(&w->mutex);
        int rc=vg_preview_publish(w->path,w->active,width,height,w->quality);
        pthread_mutex_lock(&w->mutex);
        if(rc)++w->failed;else ++w->encoded;
        pthread_mutex_unlock(&w->mutex);
        if(rc)fputs("PREVIEW_PUBLISH_FAILED\n",stderr);
    }
    return NULL;
}
vg_preview_async *vg_preview_async_open(const char *path,int quality,int nice_level) {
    if(!path||!*path||strlen(path)>PATH_MAX-12||quality<1||quality>100||nice_level<0||nice_level>19)return NULL;
    vg_preview_async *w=calloc(1,sizeof(*w));
    if(!w)return NULL;
    w->path=strdup(path);w->quality=quality;w->nice_level=nice_level;
    if(!w->path){free(w);return NULL;}
    if(pthread_mutex_init(&w->mutex,NULL)){free(w->path);free(w);return NULL;}
    if(pthread_cond_init(&w->ready,NULL)){pthread_mutex_destroy(&w->mutex);free(w->path);free(w);return NULL;}
    if(pthread_create(&w->thread,NULL,preview_run,w)) {
        pthread_cond_destroy(&w->ready);pthread_mutex_destroy(&w->mutex);free(w->path);free(w);return NULL;
    }
    return w;
}
int vg_preview_async_submit(vg_preview_async *w,const unsigned char *rgb,int width,int height) {
    if(!w||!rgb||width<=0||height<=0||width>JPEG_MAX_DIMENSION||height>JPEG_MAX_DIMENSION)return -EINVAL;
    const size_t stride=(size_t)width*3;
    if((size_t)height>SIZE_MAX/stride)return -EOVERFLOW;
    const size_t bytes=stride*(size_t)height;
    pthread_mutex_lock(&w->mutex);
    if(w->stopping){pthread_mutex_unlock(&w->mutex);return -ECANCELED;}
    if(bytes>w->queued_capacity) {
        unsigned char *pixels=realloc(w->queued,bytes);
        if(!pixels){pthread_mutex_unlock(&w->mutex);return -ENOMEM;}
        w->queued=pixels;w->queued_capacity=bytes;
    }
    memcpy(w->queued,rgb,bytes);
    w->width=width;w->height=height;
    if(w->pending)++w->replaced;
    w->pending=1;++w->submitted;
    pthread_cond_signal(&w->ready);
    pthread_mutex_unlock(&w->mutex);
    return 0;
}
void vg_preview_async_close(vg_preview_async *w) {
    if(!w)return;
    pthread_mutex_lock(&w->mutex);w->stopping=1;pthread_cond_signal(&w->ready);pthread_mutex_unlock(&w->mutex);
    pthread_join(w->thread,NULL);
    printf("PREVIEW_ASYNC_END submitted=%" PRIu64 " encoded=%" PRIu64 " replaced=%" PRIu64 " failed=%" PRIu64 " buffers=2\n",w->submitted,w->encoded,w->replaced,w->failed);
    free(w->active);free(w->queued);free(w->path);
    pthread_cond_destroy(&w->ready);pthread_mutex_destroy(&w->mutex);free(w);
}
