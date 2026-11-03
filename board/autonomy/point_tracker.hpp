#ifndef VG_POINT_TRACKER_HPP
#define VG_POINT_TRACKER_HPP
#include <array>
#include <cstdint>
#include <vector>

namespace vg_track {
struct Image { int width=0,height=0;std::vector<uint8_t> pixels; };
struct Pyramid { int source_width=0,source_height=0;float scale=2,origin_x=0,origin_y=0;std::vector<Image> images; };
struct Policy {
    int levels,radius,iterations;
    float min_eigen,max_error,fb_error,min_fraction,max_motion,epsilon;
};
struct Result {
    std::array<float,136> points{};
    std::array<bool,68> observed{};
    int count=0;
    int contour_count=0; // experimental 1D observations; not 2D corner matches
    int rejected_index=-1,rejected_stage=0; // diagnostic only: LK / FB / coverage
    float a=1,b=0,tx=0,ty=0; // image-observed similarity, original-image coordinates
};
Pyramid from_rgb(const uint8_t *rgb,int width,int height,int levels);
Pyramid from_rgb_half_region(const uint8_t *rgb,int width,int height,const int *box,double expansion,int levels);
Pyramid from_pl_gray(const uint8_t *gray,int width,int height,int source_width,int source_height,int levels);
Pyramid from_rgb_region(const uint8_t *rgb,int width,int height,const int *box,double expansion,int levels);
/* Current-image LK observations drive eyes/mouth. Noncritical missing points
 * may follow the similarity measured from the surviving observations. Eyes
 * and the four MAR points must pass forward/backward checks, or fail closed.
 * This is tracking, not a replacement accuracy/calibration claim. */
bool update(const Pyramid& before,const Pyramid& after,const float *points,
            const Policy& policy,Result& result);
/* Experimental: a valid 2D majority first measures the global motion. Missing
 * critical contour points may then be observed along their contour normals.
 * No missing point is accepted solely from the global prediction. */
bool update_contours(const Pyramid& before,const Pyramid& after,const float *points,
                     const Policy& policy,Result& result);
}
#endif
