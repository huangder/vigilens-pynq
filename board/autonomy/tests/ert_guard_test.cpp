#include "ert_guard.hpp"
#include <cassert>
#include <cstdio>
#include <limits>

int main() {
    constexpr int w=128,h=128;
    std::vector<unsigned char> rgb(w*h*3),shifted(rgb.size()),black(rgb.size()),flat(rgb.size(),90);
    for(int y=0;y<h;++y)for(int x=0;x<w;++x)for(int c=0;c<3;++c)
        rgb[(y*w+x)*3+c]=30+((x*17+y*13+(x*y)%31)%160);
    for(int y=3;y<h;++y)for(int x=4;x<w;++x)for(int c=0;c<3;++c)
        shifted[(y*w+x)*3+c]=rgb[((y-3)*w+x-4)*3+c];
    vg_ert::Patch patch;double error;
    assert(patch.make(rgb.data(),w,h,{{20,20,90,100}},32));
    assert(patch.matches(rgb.data(),w,h,{},6,18,error)&&error==0);
    assert(patch.matches(shifted.data(),w,h,{1,0,4,3},6,18,error)&&error==0);
    assert(!patch.matches(black.data(),w,h,{},6,18,error));
    assert(!patch.matches(flat.data(),w,h,{},6,18,error));
    auto changed=rgb;
    for(int y=20;y<100;++y)for(int x=20;x<90;++x)for(int c=0;c<3;++c)
        changed[(y*w+x)*3+c]=255-rgb[(y*w+x)*3+c];
    assert(!patch.matches(changed.data(),w,h,{},6,18,error));
    assert(!patch.matches(rgb.data(),w,h,{1,0,200,0},6,18,error));
    assert(!patch.matches(rgb.data(),w,h,{1,0,std::numeric_limits<double>::quiet_NaN(),0},6,18,error));
    vg_ert::Patch empty;assert(!empty.matches(rgb.data(),w,h,{},6,18,error));
    assert(!empty.make(rgb.data(),w,h,{{-20,-20,10,10}},32));
    std::array<float,136> source{},target{};
    for(int i=0;i<68;++i){source[2*i]=i%10;source[2*i+1]=i/10;target[2*i]=4+source[2*i];target[2*i+1]=3+source[2*i+1];}
    vg_ert::Transform fit;assert(vg_ert::fit(source.data(),target.data(),fit));
    assert(std::abs(fit.tx-4)<1e-9&&std::abs(fit.ty-3)<1e-9&&fit.a==1&&fit.b==0);
    // Opening the mouth cannot move the rigid seed ROI.
    target[2*57+1]+=20;assert(vg_ert::fit(source.data(),target.data(),fit));assert(fit.ty==3);
    assert(vg_ert::fit_translation(source.data(),target.data(),fit));assert(fit.a==1&&fit.b==0&&fit.tx==4&&fit.ty==3);
    // ROI translation must follow stable-point centroids without taking the
    // regressor's noisy scale back as the next input box's scale.
    for(int i=0;i<68;++i){target[2*i]=2*source[2*i]+4;target[2*i+1]=2*source[2*i+1]+3;}
    assert(vg_ert::fit_translation(source.data(),target.data(),fit));assert(fit.a==1&&fit.b==0);
    source.fill(0);assert(!vg_ert::fit(source.data(),target.data(),fit));
    assert(!vg_ert::fit_translation(source.data(),target.data(),fit));
    std::puts("ERT_GUARD_PASS black_flat_changed_outside_nan_rejected translated_pixels_matched");
}
