/* Local autonomy build: capture/PL/inference continue without any PC socket.
 * This explicit transport variant emits metrics to the camera bridge's files.
 * It refuses stream settings rather than pretending to send preview pixels. */
#include "camera_stream.h"
#include <stdio.h>
#include <stdlib.h>
int camera_stream_open(void) {
    if (getenv("VIGI_STREAM_BIND")) {
        fputs("LOCAL_AUTONOMY_FAIL unset VIGI_STREAM_BIND (no preview transport in this build)\n",stderr);
        return -1;
    }
    return 0;
}
int camera_stream_send(const char *m,size_t n,const unsigned char *r,size_t rn,
                       const unsigned char *j,size_t jn) {
    (void)m;(void)n;(void)r;(void)rn;(void)j;(void)jn;
    return 0;
}
int camera_stream_expects_jpeg(void) { return 0; }
int camera_stream_finish(unsigned frames) {
    printf("LOCAL_CAMERA_FINISH pl_frames=%u pixel_transport=disabled\n",frames);
    return 0;
}
void camera_stream_close(void) {}
