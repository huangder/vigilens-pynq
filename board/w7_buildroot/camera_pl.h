#ifndef VIGI_CAMERA_PL_H
#define VIGI_CAMERA_PL_H
#include <stddef.h>
#include <stdint.h>
int camera_pl_open(unsigned width, unsigned height, unsigned motion_threshold);
int camera_pl_open_format(unsigned width,unsigned height,unsigned motion_threshold,
                          int yuyv,unsigned stride,int limited);
int camera_pl_frame(const unsigned char *jpeg, size_t length, uint64_t ts_us, unsigned sequence);
void camera_pl_close(void);
int camera_pl_finish(void);
#endif
