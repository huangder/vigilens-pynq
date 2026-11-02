#ifndef VG_ERT_LANDMARK_HPP
#define VG_ERT_LANDMARK_HPP
#include <dlib/image_processing/shape_predictor.h>
#include <array>
#include <cmath>
#include <stdexcept>

namespace vg_ert {
// Borrowed read-only RGB888. Only the const generic-image interface is exposed;
// the camera lease remains owned by the caller for the duration of inference.
struct RGB { const unsigned char *data=nullptr; int width=0,height=0; };
inline long num_rows(const RGB& v){return v.height;}
inline long num_columns(const RGB& v){return v.width;}
inline const void *image_data(const RGB& v){return v.data;}
inline long width_step(const RGB& v){return v.width*3;}
}
namespace dlib {
template<> struct image_traits<vg_ert::RGB> {using pixel_type=rgb_pixel;};
template<> struct image_traits<const vg_ert::RGB> {using pixel_type=rgb_pixel;};
}
namespace vg_ert {
class Model {
    dlib::shape_predictor model;
public:
    explicit Model(const char *path) {
        dlib::deserialize(path)>>model;
        if(model.num_parts()!=68)throw std::runtime_error("ERT requires 68 landmarks");
    }
    // dlib inference is const: the model is immutable and inference scratch is
    // local. The low-frequency detector worker can share it with the foreground.
    bool run(const RGB& image,const std::array<int,4>& box,std::array<float,136>& p)const {
        if(!image.data||box[0]<0||box[1]<0||box[2]>image.width||box[3]>image.height||
           box[2]<=box[0]||box[3]<=box[1])return false;
        const auto shape=model(image,dlib::rectangle(box[0],box[1],box[2]-1,box[3]-1));
        for(int i=0;i<68;++i) {p[2*i]=shape.part(i).x();p[2*i+1]=shape.part(i).y();}
        return true;
    }
};
}
#endif
