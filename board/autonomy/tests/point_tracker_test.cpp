#include "point_tracker.hpp"
#include "track_history.hpp"
#include "track_box.hpp"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <random>

static std::vector<uint8_t> image(int width,int height,int dx,int dy) {
    std::vector<uint8_t> rgb(static_cast<size_t>(width)*height*3);
    std::mt19937 rng(178326);
    std::vector<uint8_t> texture(static_cast<size_t>(width)*height);
    for(auto& value:texture)value=static_cast<uint8_t>(rng());
    for(int y=0;y<height;++y)for(int x=0;x<width;++x)for(int c=0;c<3;++c) {
        const int sx=x-dx,sy=y-dy;
        rgb[(static_cast<size_t>(y)*width+x)*3+c]=(sx>=0&&sy>=0&&sx<width&&sy<height)?
            texture[static_cast<size_t>(sy)*width+sx]:0;
    }
    return rgb;
}
int main() {
    {
        const auto original=vg_track::Quad::rectangle(100,80,300,340);
        auto q=original;vg_track::Result motion;
        for(int i=0;i<1000;++i) {
            motion.tx=.25f;motion.ty=-.25f;q=q.moved(motion);
            motion.tx=-.25f;motion.ty=.25f;q=q.moved(motion);
        }
        assert(q.bounds(640,480)==original.bounds(640,480));
        motion.a=0;motion.b=1;motion.tx=560;motion.ty=-80;
        for(int i=0;i<4;++i)q=q.moved(motion);
        assert(q.bounds(640,480)==original.bounds(640,480));
        q.p[0]=NAN;assert((q.bounds(640,480)==std::array<int,4>{}));
        std::puts("POINT_TRACKER_BOX_PASS reversible_motion=1000 rotation_cycle=1 invalid_rejected=1");
    }
    {
        // All rounding/tail cases use the scalar expression as the oracle;
        // the same executable exercises ARM NEON after cross-compilation.
        std::mt19937 random(92466);
        for(int w:{32,33,46,48,49,127,128,129})for(int h:{32,33,48,49}) {
            std::vector<uint8_t> data(static_cast<size_t>(w)*h*3);
            for(auto& b:data)b=uint8_t(random());
            auto p=vg_track::from_rgb(data.data(),w,h,3);
            for(int y=0;y<h/2;++y)for(int x=0;x<w/2;++x) {
                unsigned sum=512;
                for(int yy=0;yy<2;++yy)for(int xx=0;xx<2;++xx) {
                    const auto *v=data.data()+(static_cast<size_t>(y*2+yy)*w+x*2+xx)*3;
                    sum+=77u*v[0]+150u*v[1]+29u*v[2];
                }
                assert(p.images[0].pixels[static_cast<size_t>(y)*(w/2)+x]==sum>>10);
            }
            for(size_t level=1;level<p.images.size();++level) {
                const auto& src=p.images[level-1];const auto& dst=p.images[level];
                for(int y=0;y<dst.height;++y)for(int x=0;x<dst.width;++x) {
                    unsigned sum=8;
                    for(int j=-1;j<=1;++j)for(int i=-1;i<=1;++i) {
                        const int yy=std::max(0,std::min(src.height-1,y*2+j));
                        const int xx=std::max(0,std::min(src.width-1,x*2+i));
                        sum+=src.pixels[static_cast<size_t>(yy)*src.width+xx]*(i==0?2:1)*(j==0?2:1);
                    }
                    assert(dst.pixels[static_cast<size_t>(y)*dst.width+x]==sum>>4);
                }
            }
        }
        std::puts("POINT_TRACKER_RGB_GRAY_PASS cases=32 scalar_neon_exact=1");
    }
    {
        const int sw=128,sh=64,gw=sw/8*3,gh=sh/8*3;
        std::vector<uint8_t> gray(static_cast<size_t>(gw)*gh);
        for(int y=0;y<gh;++y)for(int x=0;x<gw;++x)
            gray[static_cast<size_t>(y)*gw+x]=uint8_t((x/3*8+x%3)+2*(y/3*8+y%3));
        auto p=vg_track::from_pl_gray(gray.data(),gw,gh,sw,sh,1);
        assert(p.scale==4&&p.images.size()==1);
        const auto& a=p.images[0];
        for(int y=0;y<a.height-1;++y)for(int x=0;x<a.width-1;++x)
            assert(a.pixels[static_cast<size_t>(y)*a.width+x]==x*4+y*8);
        assert(vg_track::from_pl_gray(gray.data(),gw-1,gh,sw,sh,1).images.empty());
        std::puts("POINT_TRACKER_PL_GRID_PASS actual_3of8_coordinates=1 invalid_profile=1");
    }
    const int width=640,height=480;
    const vg_track::Policy policy{3,3,12,1,18,2,0.7f,48,.02f};
    float points[136];
    for(int i=0;i<68;++i){points[2*i]=100+(i%9)*44;points[2*i+1]=100+(i/9)*36;}
    auto rgb=image(width,height,0,0);
    const auto first=vg_track::from_rgb(rgb.data(),width,height,3);
    {
        vg_track::History history(1.0,4);
        const auto p=std::make_shared<vg_track::Pyramid>(first);
        std::vector<vg_track::Sample> path;
        for(int i=0;i<6;++i)assert(history.push({uint64_t(i),i/30.,p}));
        assert(history.size()==4&&!history.since(1,1/30.,path));
        assert(history.since(2,2/30.,path)&&path.size()==3&&path.back().id==5);
        assert(!history.push({5,5/30.,p})&&history.size()==0);
        assert(history.push({6,6/30.,p})&&history.push({7,2.,p}));
        assert(!history.since(6,6/30.,path));
        assert(history.since(7,2.,path)&&path.empty());
        std::puts("POINT_TRACKER_HISTORY_PASS expired_source=1 bounded=1 nonmonotonic_clear=1");
    }
    vg_track::Result out;
    assert(vg_track::update(first,first,points,policy,out));
    for(int i=0;i<136;++i)assert(std::fabs(out.points[i]-points[i])<1e-4);
    int cases=0;
    for(int dx:{-8,-4,0,4,8})for(int dy:{-4,0,4}) {
        rgb=image(width,height,dx,dy);
        auto next=vg_track::from_rgb(rgb.data(),width,height,3);
        if(!vg_track::update(first,next,points,policy,out)){
            std::fprintf(stderr,"translation refused dx=%d dy=%d observations=%d\n",dx,dy,out.count);return 1;
        }
        for(int i=0;i<68;++i) {
            assert(std::fabs(out.points[i*2]-points[i*2]-dx)<0.5);
            assert(std::fabs(out.points[i*2+1]-points[i*2+1]-dy)<0.5);
        }
        ++cases;
    }
    std::vector<uint8_t> black(static_cast<size_t>(width)*height*3,0);
    const auto blank=vg_track::from_rgb(black.data(),width,height,3);
    assert(!vg_track::update(first,blank,points,policy,out));
    assert(!vg_track::update(blank,blank,points,policy,out));
    {
        // A long-delayed correction travels through actual intermediate
        // observations. Crossing a black frame must fail even if it recovers.
        auto previous=first;std::array<float,136> current{};
        std::copy(points,points+136,current.begin());
        for(int step=1;step<=16;++step) {
            auto data=image(width,height,step*4,0);
            auto next=vg_track::from_rgb(data.data(),width,height,3);
            assert(vg_track::update(previous,next,current.data(),policy,out));
            current=out.points;previous=std::move(next);
        }
        for(int i=0;i<68;++i)assert(std::fabs(current[i*2]-points[i*2]-64)<0.5);
        assert(!vg_track::update(previous,blank,current.data(),policy,out));
        std::puts("POINT_TRACKER_BRIDGE_PASS intermediate_steps=16 total_motion=64 black_rejected=1");
    }
    {
        const int box0[]={60,60,540,420},box1[]={72,52,552,412};
        auto a_rgb=image(width,height,0,0),b_rgb=image(width,height,4,-4);
        auto a=vg_track::from_rgb_region(a_rgb.data(),width,height,box0,1,3);
        auto b=vg_track::from_rgb_region(b_rgb.data(),width,height,box1,1,3);
        assert(vg_track::update(a,b,points,policy,out));
        for(int i=0;i<68;++i) {
            assert(std::fabs(out.points[i*2]-points[i*2]-4)<0.5);
            assert(std::fabs(out.points[i*2+1]-points[i*2+1]+4)<0.5);
        }
        std::puts("POINT_TRACKER_ROI_ORIGIN_PASS different_origins=1 full_resolution=1");
    }
    points[36*2]=NAN;
    assert(!vg_track::update(first,first,points,policy,out));
    std::printf("POINT_TRACKER_TEST_PASS translation_cases=%d black_loss=1 flat_rejected=1 malformed=1\n",cases);
}
