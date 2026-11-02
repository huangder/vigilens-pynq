#ifndef VIGI_CAMERA_STREAM_H
#define VIGI_CAMERA_STREAM_H
#include <stddef.h>
int camera_stream_open(void);
/* jpeg/jpeg_size may be NULL/0 unless VIGI_STREAM_CODEC=jpeg was set; then the
 * camera's own MJPEG bytes are forwarded (S1) instead of the decoded RGB. */
int camera_stream_send(const char *metadata, size_t metadata_size,
                       const unsigned char *rgb, size_t rgb_size,
                       const unsigned char *jpeg, size_t jpeg_size);
int camera_stream_expects_jpeg(void);
int camera_stream_finish(unsigned compared_frames);
void camera_stream_close(void);
#endif
