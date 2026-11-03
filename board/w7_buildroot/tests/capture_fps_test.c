#define _POSIX_C_SOURCE 200809L
#include <time.h>
#define main probe_main_not_called
#include "../uvc_probe.c"
#undef main
#include <assert.h>
int main(void) {
    unsetenv("VIGI_CAMERA_FPS");assert(requested_fps()==30);
    const char *bad[]={"","0","-1","121","60fps","60 "," 60","nan","999999999999999999999999999"};
    for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i){setenv("VIGI_CAMERA_FPS",bad[i],1);assert(requested_fps()==0);}
    for(unsigned i=1;i<=120;++i){char text[16];snprintf(text,sizeof(text),"%u",i);setenv("VIGI_CAMERA_FPS",text,1);assert(requested_fps()==i);}
    puts("CAPTURE_FPS_TEST_PASS legacy=30 valid=1..120 malformed_refused=1 no_device_access=1");return 0;
}
