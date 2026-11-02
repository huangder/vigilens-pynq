// Board-side landmark probe: V4L2 MJPG -> libjpeg -> crop -> MNN Peppa -> 68 points.
//
// Why: the main program needs a proven "camera frame -> 68 landmarks" path on the
// A9 before detection is wired in. This probe uses a CENTRE crop (the detector is
// not ported yet) and prints per-frame timing plus the landmark cloud, so the
// crop/preprocess/inference path can be validated and timed on the board.
//
// Not a detector, not an accuracy claim: with no face in the centre crop the
// output is meaningless and the tool says so via the point spread check.
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <memory>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <jpeglib.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <fcntl.h>
#include <unistd.h>

#include <MNN/Interpreter.hpp>
#include <MNN/Tensor.hpp>

namespace {

constexpr int kInput = 160;
constexpr int kPoints = 68;
constexpr int kOutputs = 143;

int xioctl(int fd, unsigned long request, void* arg) {
    int r;
    do { r = ioctl(fd, request, arg); } while (r < 0 && errno == EINTR);
    return r;
}

bool decode_jpeg(const unsigned char* data, size_t size, int* width, int* height,
                 std::vector<unsigned char>* rgb) {
    jpeg_decompress_struct info{};
    jpeg_error_mgr error{};
    info.err = jpeg_std_error(&error);
    jpeg_create_decompress(&info);
    jpeg_mem_src(&info, data, static_cast<unsigned long>(size));
    if (jpeg_read_header(&info, TRUE) != JPEG_HEADER_OK) { jpeg_destroy_decompress(&info); return false; }
    info.out_color_space = JCS_RGB;
    jpeg_start_decompress(&info);
    *width = static_cast<int>(info.output_width);
    *height = static_cast<int>(info.output_height);
    rgb->assign(static_cast<size_t>(*width) * *height * 3, 0);
    while (info.output_scanline < info.output_height) {
        unsigned char* row = rgb->data() + static_cast<size_t>(info.output_scanline) * *width * 3;
        JSAMPROW rows[1] = {row};
        jpeg_read_scanlines(&info, rows, 1);
    }
    jpeg_finish_decompress(&info);
    jpeg_destroy_decompress(&info);
    return true;
}

void resize_bilinear(const unsigned char* src, int sw, int sh, int channels,
                     unsigned char* dst, int dw, int dh) {
    for (int y = 0; y < dh; ++y) {
        const float fy = (y + 0.5f) * sh / dh - 0.5f;
        int y0 = static_cast<int>(std::floor(fy));
        float wy = fy - y0;
        if (y0 < 0) { y0 = 0; wy = 0; }
        int y1 = y0 + 1 < sh ? y0 + 1 : y0;
        for (int x = 0; x < dw; ++x) {
            const float fx = (x + 0.5f) * sw / dw - 0.5f;
            int x0 = static_cast<int>(std::floor(fx));
            float wx = fx - x0;
            if (x0 < 0) { x0 = 0; wx = 0; }
            int x1 = x0 + 1 < sw ? x0 + 1 : x0;
            for (int c = 0; c < channels; ++c) {
                const float a = src[(y0 * sw + x0) * channels + c];
                const float b = src[(y0 * sw + x1) * channels + c];
                const float d = src[(y1 * sw + x0) * channels + c];
                const float e = src[(y1 * sw + x1) * channels + c];
                const float top = a + (b - a) * wx;
                const float bottom = d + (e - d) * wx;
                dst[(y * dw + x) * channels + c] =
                    static_cast<unsigned char>(std::lround(top + (bottom - top) * wy));
            }
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 6) {
        std::fprintf(stderr, "usage: vg_landmark_probe model.mnn /dev/video0 width height frames\n");
        return 2;
    }
    const char* model_path = argv[1];
    const char* device = argv[2];
    const int width = std::atoi(argv[3]);
    const int height = std::atoi(argv[4]);
    const int frames = std::atoi(argv[5]);
    if (width != 1280 || height != 720 || frames < 1) {
        std::fprintf(stderr, "only 1280x720 is supported\n");
        return 2;
    }

    std::shared_ptr<MNN::Interpreter> interpreter(MNN::Interpreter::createFromFile(model_path));
    if (!interpreter) { std::fprintf(stderr, "MNN_OPEN_FAIL\n"); return 1; }
    MNN::ScheduleConfig config;
    config.type = MNN_FORWARD_CPU;
    config.numThread = 2;
    MNN::BackendConfig backend;
    backend.precision = MNN::BackendConfig::Precision_High;
    config.backendConfig = &backend;
    MNN::Session* session = interpreter->createSession(config);
    if (!session) { std::fprintf(stderr, "MNN_SESSION_FAIL\n"); return 1; }
    MNN::Tensor* input = interpreter->getSessionInput(session, nullptr);
    const std::vector<int> shape{1, 3, kInput, kInput};
    interpreter->resizeTensor(input, shape);
    interpreter->resizeSession(session);
    input = interpreter->getSessionInput(session, nullptr);
    std::shared_ptr<MNN::Tensor> host(MNN::Tensor::create<float>(shape, nullptr, MNN::Tensor::CAFFE));

    int fd = open(device, O_RDWR | O_NONBLOCK);
    if (fd < 0) { std::perror("open"); return 1; }
    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = width; fmt.fmt.pix.height = height;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG; fmt.fmt.pix.field = V4L2_FIELD_ANY;
    if (xioctl(fd, VIDIOC_S_FMT, &fmt) < 0) { std::perror("S_FMT"); return 1; }
    v4l2_requestbuffers req{};
    req.count = 4; req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE; req.memory = V4L2_MEMORY_MMAP;
    if (xioctl(fd, VIDIOC_REQBUFS, &req) < 0) { std::perror("REQBUFS"); return 1; }
    void* maps[4] = {nullptr};
    size_t lengths[4] = {0};
    for (unsigned i = 0; i < req.count; ++i) {
        v4l2_buffer buf{};
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE; buf.memory = V4L2_MEMORY_MMAP; buf.index = i;
        if (xioctl(fd, VIDIOC_QUERYBUF, &buf) < 0) { std::perror("QUERYBUF"); return 1; }
        maps[i] = mmap(nullptr, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
        lengths[i] = buf.length;
        if (xioctl(fd, VIDIOC_QBUF, &buf) < 0) { std::perror("QBUF"); return 1; }
    }
    v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (xioctl(fd, VIDIOC_STREAMON, &type) < 0) { std::perror("STREAMON"); return 1; }

    std::vector<unsigned char> rgb(static_cast<size_t>(width) * height * 3);
    std::vector<unsigned char> crop(static_cast<size_t>(kInput) * kInput * 3);
    double total_ms = 0.0;
    int good = 0;
    const int side = std::min(width, height) / 2;
    const int cx = width / 2, cy = height / 2;
    const int x0 = cx - side / 2, y0 = cy - side / 2;
    for (int n = 0; n < frames; ++n) {
        fd_set set; FD_ZERO(&set); FD_SET(fd, &set);
        timeval tv{}; tv.tv_sec = 5;
        int ready = select(fd + 1, &set, nullptr, nullptr, &tv);
        if (ready <= 0) { std::fprintf(stderr, "CAPTURE_TIMEOUT n=%d\n", n); return 1; }
        v4l2_buffer buf{};
        buf.type = type; buf.memory = V4L2_MEMORY_MMAP;
        if (xioctl(fd, VIDIOC_DQBUF, &buf) < 0) { std::perror("DQBUF"); return 1; }
        int jw = 0, jh = 0;
        if (!decode_jpeg(static_cast<unsigned char*>(maps[buf.index]), buf.bytesused, &jw, &jh, &rgb)) {
            std::fprintf(stderr, "JPEG_FAIL n=%d\n", n);
            return 1;
        }
        // Centre crop -> 160x160, RGB->BGR for the Peppa model.
        std::vector<unsigned char> patch(static_cast<size_t>(side) * side * 3);
        for (int y = 0; y < side; ++y)
            std::memcpy(patch.data() + static_cast<size_t>(y) * side * 3,
                        rgb.data() + (static_cast<size_t>(y0 + y) * jw + x0) * 3,
                        static_cast<size_t>(side) * 3);
        resize_bilinear(patch.data(), side, side, 3, crop.data(), kInput, kInput);
        float* host_data = host->host<float>();
        for (int i = 0; i < kInput * kInput; ++i) {
            const float b = crop[i * 3 + 2], g = crop[i * 3 + 1], r = crop[i * 3 + 0];
            host_data[i] = (b - 127.0f) / 127.0f;
            host_data[kInput * kInput + i] = (g - 127.0f) / 127.0f;
            host_data[2 * kInput * kInput + i] = (r - 127.0f) / 127.0f;
        }
        input->copyFromHostTensor(host.get());
        const auto start = std::chrono::steady_clock::now();
        interpreter->runSession(session);
        const auto end = std::chrono::steady_clock::now();
        const double ms = std::chrono::duration<double, std::milli>(end - start).count();
        total_ms += ms;
        MNN::Tensor* output = interpreter->getSessionOutput(session, nullptr);
        MNN::Tensor host_out(output, output->getDimensionType());
        output->copyToHostTensor(&host_out);
        const float* values = host_out.host<float>();
        if (host_out.elementSize() < kOutputs) { std::fprintf(stderr, "OUTPUT_SHAPE_FAIL\n"); return 1; }
        double minx = 1e9, maxx = -1e9, miny = 1e9, maxy = -1e9;
        for (int i = 0; i < kPoints; ++i) {
            minx = std::fmin(minx, values[2 * i]); maxx = std::fmax(maxx, values[2 * i]);
            miny = std::fmin(miny, values[2 * i + 1]); maxy = std::fmax(maxy, values[2 * i + 1]);
        }
        const double spread = std::max(maxx - minx, maxy - miny);
        const bool plausible = spread > 0.15 && spread < 1.5 && std::isfinite(spread);
        if (plausible) ++good;
        if (n == 0 || n == frames - 1 || n % 30 == 0)
            std::printf("LANDMARK n=%d ms=%.3f spread=%.3f plausible=%d p30=(%.4f,%.4f)\n",
                        n, ms, spread, plausible ? 1 : 0, values[60], values[61]);
        xioctl(fd, VIDIOC_QBUF, &buf);
    }
    std::printf("LANDMARK_SUMMARY frames=%d plausible=%d mean_ms=%.3f\n", frames, good,
                frames ? total_ms / frames : 0.0);
    return 0;
}
