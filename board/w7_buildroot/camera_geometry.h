#ifndef VG_CAMERA_GEOMETRY_H
#define VG_CAMERA_GEOMETRY_H
#include <stddef.h>
/* Software subsets of the existing runtime-programmable 3/8 PL build.
 * Never use the old 3/5 bitstream for these dimensions. */
struct vg_camera_geometry {
    unsigned width,height,gray_width,gray_height,gray_bytes;
    size_t rgb_bytes;
};
static inline int vg_camera_geometry_init(struct vg_camera_geometry *g,unsigned w,unsigned h) {
    if(!g||!((w==1280&&h==720)||(w==960&&h==720)||(w==640&&h==480)))return -1;
    g->width=w;g->height=h;g->gray_width=w/8*3;g->gray_height=h/8*3;
    g->gray_bytes=g->gray_width*g->gray_height;g->rgb_bytes=(size_t)w*h*3;
    /* The current TLAST-suppressed DMA sink has only been audited with a
     * complete 64-byte write block. 960x720 produces 97200 gray bytes and
     * timed out waiting for its last byte on the board: fail before MMIO. */
    return g->gray_bytes%64?-1:0;
}
#endif
