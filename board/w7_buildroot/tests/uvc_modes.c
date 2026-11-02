/* Read-only UVC mode inventory. No frame capture or image output. */
#include <fcntl.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <linux/videodev2.h>
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    int fd=open(argv[1],O_RDWR);if(fd<0){perror("open");return 1;}
    struct v4l2_fmtdesc f={.type=V4L2_BUF_TYPE_VIDEO_CAPTURE};
    for(;ioctl(fd,VIDIOC_ENUM_FMT,&f)==0;++f.index) {
        struct v4l2_frmsizeenum s={.pixel_format=f.pixelformat};
        for(;ioctl(fd,VIDIOC_ENUM_FRAMESIZES,&s)==0;++s.index) {
            if(s.type!=V4L2_FRMSIZE_TYPE_DISCRETE)continue;
            struct v4l2_frmivalenum v={.pixel_format=f.pixelformat,.width=s.discrete.width,.height=s.discrete.height};
            for(;ioctl(fd,VIDIOC_ENUM_FRAMEINTERVALS,&v)==0;++v.index)if(v.type==V4L2_FRMIVAL_TYPE_DISCRETE)
                printf("UVC_MODE format=0x%08x width=%u height=%u interval=%u/%u\n",f.pixelformat,v.width,v.height,v.discrete.numerator,v.discrete.denominator);
        }
    }
    close(fd);return 0;
}
