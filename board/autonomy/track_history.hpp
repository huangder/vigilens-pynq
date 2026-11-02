#ifndef VG_TRACK_HISTORY_HPP
#define VG_TRACK_HISTORY_HPP
#include "point_tracker.hpp"
#include <cmath>
#include <deque>
#include <memory>

namespace vg_track {
struct Sample {
    uint64_t id;
    double ts;
    std::shared_ptr<const Pyramid> image;
};
/* Owned, bounded history. Caller synchronizes access. Replaying a correction
 * requires the exact source frame: an expired anchor cannot jump a lost span. */
class History {
    double max_age;
    size_t max_frames;
    std::deque<Sample> frames;
public:
    History(double age,size_t count):max_age(age),max_frames(count){}
    void clear(){frames.clear();}
    bool push(Sample sample) {
        if(!sample.image||sample.image->images.empty()||!std::isfinite(sample.ts)||
           !(max_age>0)||max_frames<2)return false;
        if(!frames.empty()&&(sample.id<=frames.back().id||sample.ts<=frames.back().ts||
           sample.image->source_width!=frames.back().image->source_width||
           sample.image->source_height!=frames.back().image->source_height)) {
            clear();return false;
        }
        frames.push_back(std::move(sample));
        while(frames.size()>max_frames||frames.back().ts-frames.front().ts>max_age)frames.pop_front();
        return true;
    }
    bool since(uint64_t id,double ts,std::vector<Sample>& out)const {
        out.clear();
        auto found=frames.begin();
        while(found!=frames.end()&&(found->id!=id||found->ts!=ts))++found;
        if(found==frames.end())return false;
        for(++found;found!=frames.end();++found)out.push_back(*found);
        return true;
    }
    size_t size()const{return frames.size();}
};
}
#endif
