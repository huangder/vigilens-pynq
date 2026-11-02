/* Synthetic format/bounds tests. Never human quality or frame-rate evidence. */
#include "camera_geometry.h"
#include "yuyv_rgb.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"FAIL line=%d\n",__LINE__);return 1;}}while(0)
int main(void) {
    struct vg_camera_geometry g;
    const unsigned sizes[][4]={{1280,720,480,270},{640,480,240,180}};
    for(unsigned i=0;i<2;++i){REQUIRE(!vg_camera_geometry_init(&g,sizes[i][0],sizes[i][1]));REQUIRE(g.gray_width==sizes[i][2]&&g.gray_height==sizes[i][3]);}
    REQUIRE(vg_camera_geometry_init(&g,960,720));
    REQUIRE(vg_camera_geometry_init(&g,960,540));REQUIRE(vg_camera_geometry_init(&g,641,480));
    uint8_t in[18*2+7],out[18*3+2],ref[18*3];unsigned long pixels=0;
    for(int limited=0;limited<=1;++limited)for(unsigned y=0;y<256;++y)for(unsigned u=0;u<256;++u)for(unsigned v=0;v<256;v+=17) {
        for(unsigned x=0;x<18;x+=2){in[2*x]=y;in[2*x+1]=u;in[2*x+2]=255-y;in[2*x+3]=v;}
        memset(out,0x6d,sizeof(out));
        REQUIRE(!vg_yuyv_rgb(in,sizeof(in),sizeof(in),out+1,18,1,limited));
        vg_yuyv_row_scalar(in,ref,18,limited);
        REQUIRE(!memcmp(out+1,ref,sizeof(ref)));REQUIRE(out[0]==0x6d&&out[sizeof(out)-1]==0x6d);pixels+=18;
    }
    uint8_t rgb[6];vg_yuyv_pixel(16,128,128,1,rgb);REQUIRE(rgb[0]==0&&rgb[1]==0&&rgb[2]==0);
    vg_yuyv_pixel(235,128,128,1,rgb);REQUIRE(rgb[0]==255&&rgb[1]==255&&rgb[2]==255);
    vg_yuyv_pixel(82,90,240,1,rgb);REQUIRE(rgb[0]==255&&rgb[1]==1&&rgb[2]==0);
    REQUIRE(vg_yuyv_rgb(in,35,36,out,18,1,1));REQUIRE(vg_yuyv_rgb(in,36,35,out,18,1,1));
    REQUIRE(vg_yuyv_rgb(in,sizeof(in),sizeof(in),out,17,1,1));
    uint8_t padded[86],two_rows[110],two_ref[108];
    for(unsigned i=0;i<sizeof(padded);++i)padded[i]=(uint8_t)(i*29);
    memset(two_rows,0xa7,sizeof(two_rows));
    REQUIRE(!vg_yuyv_rgb(padded,sizeof(padded),43,two_rows+1,18,2,1));
    vg_yuyv_row_scalar(padded,two_ref,18,1);vg_yuyv_row_scalar(padded+43,two_ref+54,18,1);
    REQUIRE(!memcmp(two_rows+1,two_ref,sizeof(two_ref))&&two_rows[0]==0xa7&&two_rows[109]==0xa7);
    REQUIRE(vg_yuyv_rgb(padded,78,43,two_rows+1,18,2,1));
    puts("CAMERA_GEOMETRY_PASS profiles=2 invalid_rejected=1 unaligned_960_rejected=1");
    printf("YUYV_RGB_PASS pixels=%lu scalar_reference_parity=1 boundaries=1 RGB_order=1 implementation=%s\n",pixels,
#if defined(__ARM_NEON)
        "neon"
#else
        "scalar"
#endif
    );
    return 0;
}
