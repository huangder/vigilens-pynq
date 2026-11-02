/* V4L2 verification; VIGI_CAMERA_PL can opt into an in-memory wired stream. */
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <stdio.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <unistd.h>
#include "sample_gate.h"
#include "capture_budget.h"
#ifdef VIGI_CAMERA_PL
#include "camera_pl.h"
#endif
static int call(int fd, unsigned long op, void *p) {
    int r; do { r = ioctl(fd, op, p); } while (r < 0 && errno == EINTR); return r;
}
#ifdef VIGI_AUTONOMY
static volatile sig_atomic_t capture_stop;
static void stop_capture(int sig) { (void)sig;capture_stop=1; }
#endif
int main(int argc, char **argv) {
    /* Configure buffering before the first stdout write, also for log files. */
    if (setvbuf(stdout, NULL, _IOLBF, 0)) return 1;
    struct v4l2_capability cap = {0};
    struct v4l2_format fmt = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    struct v4l2_streamparm rate = {.type = V4L2_BUF_TYPE_VIDEO_CAPTURE};
    struct v4l2_requestbuffers req = {.count = 4, .type = V4L2_BUF_TYPE_VIDEO_CAPTURE, .memory = V4L2_MEMORY_MMAP};
    void *maps[4] = {0}; size_t lengths[4] = {0};
    unsigned w, h, want, n = 0, i, caps, previous_seq = 0, driver_gaps = 0;
    int fd = -1, active = 0, rc = 1, matched = 0;
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    struct sample_gate gate = {0};
    uint64_t first_us = 0, last_us = 0, kept_first = 0, kept_last = 0;
    const char *input_format=getenv("VIGI_CAMERA_FORMAT");
    if(input_format&&strcmp(input_format,"mjpg")&&strcmp(input_format,"yuyv"))return 2;
    int yuyv=input_format&&!strcmp(input_format,"yuyv");
    unsigned fourcc=yuyv?V4L2_PIX_FMT_YUYV:V4L2_PIX_FMT_MJPEG;
    if (argc != 5 && argc != 6
#ifdef VIGI_CAMERA_PL
        && argc != 7
#endif
    ) {
#ifdef VIGI_CAMERA_PL
        fprintf(stderr, "usage: camera_pl device width height frames analysis-fps motion-threshold\n");
        fprintf(stderr, "  (the PL build needs all six arguments; missing ones fail with EINVAL)\n");
#else
        fprintf(stderr, "usage: uvc_probe device width height frames [analysis-fps]\n");
#endif
        return 2;
    }
    w = (unsigned)atoi(argv[2]); h = (unsigned)atoi(argv[3]);
    unsigned max_frames = 300;
    int continuous_allowed=0;
#ifdef VIGI_AUTONOMY
    max_frames = 36000; /* Same bounded long-run budget as the network build. */
    continuous_allowed=1;
    struct sigaction action={0};action.sa_handler=stop_capture;
    sigemptyset(&action.sa_mask);
    if(sigaction(SIGINT,&action,NULL)||sigaction(SIGTERM,&action,NULL))return 1;
#endif
#ifdef VIGI_CAMERA_PL
    if (getenv("VIGI_STREAM_BIND")) max_frames = 36000;
#endif
    if (!w || !h || vigi_capture_budget(argv[4],max_frames,continuous_allowed,&want)) return 2;
    if(!want)puts("CAPTURE_CONTINUOUS enabled=1 stop=SIGINT_or_SIGTERM");
    if (argc >= 6) {
        gate.fps = (unsigned)atoi(argv[5]);
        if (!gate.fps || gate.fps > 120) return 2;
        printf("SAMPLE_REQUEST target_fps=%u time_base=v4l2_buffer no_padding=1\n", gate.fps);
    }
    printf("CAPTURE_REQUEST %s %ux%u %s 30fps frames=%u\n", argv[1], w, h, yuyv?"YUYV":"MJPG",want);
    fd = open(argv[1], O_RDWR | O_NONBLOCK);
    if (fd < 0 || call(fd, VIDIOC_QUERYCAP, &cap) < 0) goto fail;
    caps = (cap.capabilities & V4L2_CAP_DEVICE_CAPS) ? cap.device_caps : cap.capabilities;
    if (!(caps & V4L2_CAP_VIDEO_CAPTURE) || !(caps & V4L2_CAP_STREAMING)) {
        puts("CAPTURE_SKIP not a streaming capture node"); rc = 2; goto done;
    }
    printf("CAPTURE_DEVICE driver=%s card=%s\n", cap.driver, cap.card);
    for (i = 0; ; ++i) {
        struct v4l2_frmivalenum interval = {.index = i, .pixel_format = fourcc,
                                          .width = w, .height = h};
        if (call(fd, VIDIOC_ENUM_FRAMEINTERVALS, &interval) < 0) break;
        if (interval.type == V4L2_FRMIVAL_TYPE_DISCRETE)
            printf("CAPTURE_SUPPORTED_INTERVAL %ux%u %u/%u\n", w, h,
                   interval.discrete.numerator, interval.discrete.denominator);
    }
    fmt.fmt.pix.width = w; fmt.fmt.pix.height = h;
    fmt.fmt.pix.pixelformat = fourcc; fmt.fmt.pix.field = V4L2_FIELD_ANY;
    if (call(fd, VIDIOC_S_FMT, &fmt) < 0) goto fail;
    rate.parm.capture.timeperframe.numerator = 1;
    rate.parm.capture.timeperframe.denominator = 30;
    if (call(fd, VIDIOC_S_PARM, &rate) < 0) goto fail;
    printf("CAPTURE_MODE %ux%u fourcc=0x%08x interval=%u/%u\n", fmt.fmt.pix.width,
           fmt.fmt.pix.height, fmt.fmt.pix.pixelformat, rate.parm.capture.timeperframe.numerator,
           rate.parm.capture.timeperframe.denominator);
    matched = fmt.fmt.pix.width == w && fmt.fmt.pix.height == h &&
        fmt.fmt.pix.pixelformat == fourcc && rate.parm.capture.timeperframe.numerator &&
        rate.parm.capture.timeperframe.denominator == 30 * rate.parm.capture.timeperframe.numerator;
    if (!matched) puts("CAPTURE_MODE_MISMATCH: verify frames, but do not certify requested 30fps mode");
    if(gate.fps) {
        if(sample_gate_set_interval(&gate,rate.parm.capture.timeperframe.numerator,
            rate.parm.capture.timeperframe.denominator)){errno=EINVAL;goto fail;}
        printf("SAMPLE_POLICY mode=%s original_timestamps=1\n",gate.native_cadence?"native_passthrough":"timestamp_slots");
    }
#ifdef VIGI_CAMERA_PL
    if (argc != 7 || !gate.fps || fmt.fmt.pix.width != w || fmt.fmt.pix.height != h ||
        fmt.fmt.pix.pixelformat != fourcc) {
        errno = EINVAL; goto fail;
    }
    unsigned enc=fmt.fmt.pix.ycbcr_enc,quant=fmt.fmt.pix.quantization;
    if(enc==V4L2_YCBCR_ENC_DEFAULT)enc=V4L2_MAP_YCBCR_ENC_DEFAULT(fmt.fmt.pix.colorspace);
    if(quant==V4L2_QUANTIZATION_DEFAULT)quant=V4L2_MAP_QUANTIZATION_DEFAULT(0,fmt.fmt.pix.colorspace,enc);
    printf("CAPTURE_COLOR colorspace=%u ycbcr=%u quantization=%u stride=%u\n",fmt.fmt.pix.colorspace,enc,quant,fmt.fmt.pix.bytesperline);
    if(yuyv&&(enc!=V4L2_YCBCR_ENC_601||(quant!=V4L2_QUANTIZATION_LIM_RANGE&&quant!=V4L2_QUANTIZATION_FULL_RANGE))) {
        fputs("CAPTURE_FAIL unsupported YUYV colorimetry\n",stderr);errno=EINVAL;goto fail;
    }
    if (camera_pl_open_format(w,h,(unsigned)atoi(argv[6]),yuyv,fmt.fmt.pix.bytesperline,quant!=V4L2_QUANTIZATION_FULL_RANGE)) goto fail;
#endif
    if (call(fd, VIDIOC_REQBUFS, &req) < 0) goto fail;
    if (!req.count || req.count > 4) { errno = ENOBUFS; goto fail; }
    for (i = 0; i < req.count; ++i) {
        struct v4l2_buffer b = {.type = type, .memory = V4L2_MEMORY_MMAP, .index = i};
        if (call(fd, VIDIOC_QUERYBUF, &b) < 0) goto fail;
        maps[i] = mmap(NULL, b.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, b.m.offset);
        if (maps[i] == MAP_FAILED) { maps[i] = NULL; goto fail; }
        lengths[i] = b.length;
        if (call(fd, VIDIOC_QBUF, &b) < 0) goto fail;
    }
    if (call(fd, VIDIOC_STREAMON, &type) < 0) goto fail;
    active = 1;
    while ((!want || n < want)
#ifdef VIGI_AUTONOMY
           && !capture_stop
#endif
    ) {
        fd_set set; struct timeval timeout = {.tv_sec = 5};
        struct v4l2_buffer b = {.type = type, .memory = V4L2_MEMORY_MMAP};
        FD_ZERO(&set); FD_SET(fd, &set);
        int ready = select(fd + 1, &set, NULL, NULL, &timeout);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) { if (!ready) errno = ETIMEDOUT; goto fail; }
        if (call(fd, VIDIOC_DQBUF, &b) < 0) { if (errno == EAGAIN) continue; goto fail; }
        if (b.index >= req.count || !b.bytesused || b.bytesused > lengths[b.index] ||
            (b.flags & V4L2_BUF_FLAG_ERROR)) { errno = EIO; goto fail; }
        if (n && b.sequence - previous_seq > 1) driver_gaps += b.sequence - previous_seq - 1;
        previous_seq = b.sequence;
        printf("CAPTURE_FRAME n=%u sequence=%u bytes=%u ts=%ld.%06ld\n", ++n, b.sequence,
               b.bytesused, (long)b.timestamp.tv_sec, (long)b.timestamp.tv_usec);
        last_us = (uint64_t)b.timestamp.tv_sec * 1000000 + b.timestamp.tv_usec;
        if (n == 1) first_us = last_us;
        if (gate.fps) {
            int keep = sample_gate_push(&gate, last_us);
            if (keep < 0) { errno = EINVAL; goto fail; }
            if (keep) {
                if (gate.kept == 1) kept_first = last_us;
                kept_last = last_us;
                printf("SAMPLE_FRAME n=%llu source_sequence=%u ts_us=%llu\n",
                       (unsigned long long)gate.kept, b.sequence, (unsigned long long)last_us);
#ifdef VIGI_CAMERA_PL
                if (camera_pl_frame(maps[b.index], b.bytesused, last_us, b.sequence)) {
                    errno = EIO; goto fail;
                }
#endif
            }
        }
        if (call(fd, VIDIOC_QBUF, &b) < 0) goto fail;
    }
    if (gate.fps) {
        double input_fps = last_us > first_us ? (n - 1) * 1e6 / (last_us - first_us) : 0;
        double output_fps = kept_last > kept_first ? (gate.kept - 1) * 1e6 / (kept_last - kept_first) : 0;
        printf("SAMPLE_RESULT input=%u kept=%llu dropped=%llu driver_sequence_gaps=%u empty_slots=%llu input_fps=%.6f output_fps=%.6f native_30fps=%s\n",
               n, (unsigned long long)gate.kept, (unsigned long long)gate.dropped,
               driver_gaps, (unsigned long long)gate.empty, input_fps, output_fps, matched ? "yes" : "no");
    }
#ifdef VIGI_CAMERA_PL
    if (camera_pl_finish()) { errno = EIO; goto fail; }
    printf("CAMERA_PL_PASS compared_frames=%llu native_camera_mode_verified=%d\n",
           (unsigned long long)gate.kept, matched);
#endif
    if (matched) {
        printf("CAPTURE_PASS %s %ux%u frames=%u\n", argv[1], w, h, n); rc = 0;
    } else {
        printf("CAPTURE_FRAMES_OK %s negotiated=%ux%u frames=%u requested_mode=unmet\n",
               argv[1], fmt.fmt.pix.width, fmt.fmt.pix.height, n); rc = 2;
    }
    goto done;
fail:
    fprintf(stderr, "CAPTURE_FAIL %s frames=%u errno=%d (%s)\n", argv[1], n, errno, strerror(errno));
done:
#ifdef VIGI_CAMERA_PL
    camera_pl_close();
#endif
    if (active) call(fd, VIDIOC_STREAMOFF, &type);
    for (i = 0; i < 4; ++i) if (maps[i]) munmap(maps[i], lengths[i]);
    if (fd >= 0) close(fd);
    return rc;
}
