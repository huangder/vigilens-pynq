/* Board-only synthetic PL audit, including the real local-autonomy handoff.
 * Does not open a camera. Source timestamps are procedural, NOT FPS evidence. */
#include "camera_pl.h"
#include "camera_geometry.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc,char **argv) {
    if(argc!=4)return 2;
    unsigned w=(unsigned)atoi(argv[1]),h=(unsigned)atoi(argv[2]);struct vg_camera_geometry g;
    if(vg_camera_geometry_init(&g,w,h))return 2;
    unsigned char *data=malloc((size_t)w*h*2);if(!data)return 1;
    puts("SYNTHETIC_PL_INPUT source=procedural_yuyv camera_opened=0 performance_evidence=0");
    int rc=camera_pl_open_format(w,h,(unsigned)atoi(argv[3]),1,w*2,1);
    if(!rc)for(unsigned n=0;n<12;++n) {
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;x+=2) {
            size_t i=((size_t)y*w+x)*2;
            data[i]=16+(x+y+n*13)%220;data[i+1]=90+(x/2+n)%76;
            data[i+2]=16+(x+y+n*13+1)%220;data[i+3]=90+(y+n)%76;
        }
        if(camera_pl_frame(data,(size_t)w*h*2,1000000+(uint64_t)n*1000000/30,n)){rc=1;break;}
    }
    if(!rc)rc=camera_pl_finish();
    camera_pl_close();free(data);
    if(!rc)printf("SYNTHETIC_PL_INPUT_PASS width=%u height=%u frames=12\n",w,h);
    return rc?1:0;
}
