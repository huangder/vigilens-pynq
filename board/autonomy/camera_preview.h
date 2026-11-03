#ifndef VG_CAMERA_PREVIEW_H
#define VG_CAMERA_PREVIEW_H
#include <stddef.h>
/* Camera path calls only after decoding/validating these same JPEG bytes.
 * Copies encoded bytes atomically; never retains the caller's pointer. */
int vg_preview_publish_jpeg(const char *path,const unsigned char *jpeg,size_t size);
/* Display-only JPEG from the current RGB888 frame. Publishes by atomic rename.
 * The service passes a RAM filesystem path; no recording or measurement input. */
int vg_preview_publish(const char *path, const unsigned char *rgb,
                       int width, int height, int quality);
/* Two private RGB buffers: one encoding, one latest pending. submit copies
 * before returning; the caller may immediately release its camera lease. */
typedef struct vg_preview_async vg_preview_async;
vg_preview_async *vg_preview_async_open(const char *path,int quality,int nice_level);
int vg_preview_async_submit(vg_preview_async *worker,const unsigned char *rgb,int width,int height);
/* Stop the producer first. Drain the active and latest pending frame, then join. */
void vg_preview_async_close(vg_preview_async *worker);
#endif
