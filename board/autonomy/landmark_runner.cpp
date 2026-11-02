// 68-point runner: centre-crop RGB -> MNN Peppa -> 143 floats.
//
// This is the function the camera pipeline calls every N frames. It deliberately
// shares the preprocessing of landmark_probe.cpp (BGR, 160x160 bilinear,
// (px-127)/127, NCHW) so the probe and the integrated path cannot drift.
#include "landmark_runner.h"
#include "landmark_preprocess.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <limits>
#include <vector>

#include <MNN/Interpreter.hpp>
#include <MNN/Tensor.hpp>

namespace {

constexpr int kInput = 160;
constexpr int kOutputs = 143;

std::shared_ptr<MNN::Interpreter> g_interpreter;
MNN::Session* g_session = nullptr;
std::shared_ptr<MNN::Tensor> g_host_input;
double g_last_ms = 0.0;
// The runner owns one thread-confined MNN session; reuse its RGB input scratch.
vg_landmark_preprocess::Scratch g_crop;

}  // namespace

extern "C" int vg_landmark_init(const char* model_path, int threads) {
    vg_landmark_close();
    g_interpreter.reset(MNN::Interpreter::createFromFile(model_path));
    if (!g_interpreter) return -1;
    MNN::ScheduleConfig config;
    config.type = MNN_FORWARD_CPU;
    config.numThread = threads > 0 ? threads : 1;
    MNN::BackendConfig backend;
    backend.precision = MNN::BackendConfig::Precision_High;
    config.backendConfig = &backend;
    g_session = g_interpreter->createSession(config);
    if (!g_session) { g_interpreter.reset(); return -1; }
    const std::vector<int> shape{1, 3, kInput, kInput};
    MNN::Tensor* input = g_interpreter->getSessionInput(g_session, nullptr);
    g_interpreter->resizeTensor(input, shape);
    g_interpreter->resizeSession(g_session);
    g_host_input.reset(MNN::Tensor::create<float>(shape, nullptr, MNN::Tensor::CAFFE));
    return g_host_input ? 0 : -1;
}

static int run_crop(const unsigned char* rgb, int width, int height,
                    int x0, int y0, int x1, int y1, float* values) {
    if (!g_session || !rgb || !values) return -1;
    if (x1 - x0 < 16 || y1 - y0 < 16 || x0 < 0 || y0 < 0 || x1 > width || y1 > height) return -1;
    vg_landmark_preprocess::resize_roi_rgb(rgb,width,x0,y0,x1,y1,g_crop);
    float* host = g_host_input->host<float>();
    for (int i = 0; i < kInput * kInput; ++i) {
        const float b = g_crop[static_cast<size_t>(i) * 3 + 2];
        const float g = g_crop[static_cast<size_t>(i) * 3 + 1];
        const float r = g_crop[static_cast<size_t>(i) * 3 + 0];
        host[i] = (b - 127.0f) / 127.0f;
        host[kInput * kInput + i] = (g - 127.0f) / 127.0f;
        host[2 * kInput * kInput + i] = (r - 127.0f) / 127.0f;
    }
    MNN::Tensor* input = g_interpreter->getSessionInput(g_session, nullptr);
    input->copyFromHostTensor(g_host_input.get());
    const auto start = std::chrono::steady_clock::now();
    if (g_interpreter->runSession(g_session) != MNN::NO_ERROR) return -1;
    const auto end = std::chrono::steady_clock::now();
    g_last_ms = std::chrono::duration<double, std::milli>(end - start).count();
    MNN::Tensor* output = g_interpreter->getSessionOutput(g_session, nullptr);
    if (!output) return -1;
    MNN::Tensor host_out(output, output->getDimensionType());
    output->copyToHostTensor(&host_out);
    if (host_out.elementSize() < kOutputs) return -1;
    std::memcpy(values, host_out.host<float>(), sizeof(float) * kOutputs);
    for (int i = 0; i < kOutputs; ++i)
        if (!std::isfinite(values[i])) return -1;
    return 0;
}

extern "C" int vg_landmark_run_box(const unsigned char* rgb, int width, int height,
                                   const int* bbox, double crop_scale, float* points136,
                                   float* values, int capacity) {
    if (!rgb || !bbox || !points136 || !values || capacity < kOutputs ||
        width <= 0 || height <= 0 || !std::isfinite(crop_scale) || crop_scale < 1.0)
        return -1;
    const int x0 = std::max(0, bbox[0]), y0 = std::max(0, bbox[1]);
    const int x1 = std::min(width, bbox[2]), y1 = std::min(height, bbox[3]);
    if (x1 <= x0 || y1 <= y0) return -1;
    const double scaled_w = (x1 - x0) * crop_scale;
    const double scaled_h = (y1 - y0) * crop_scale;
    if (scaled_w > std::numeric_limits<int>::max() ||
        scaled_h > std::numeric_limits<int>::max()) return -1;
    const int cx = x0 + (x1 - x0) / 2, cy = y0 + (y1 - y0) / 2;
    const int half_w = static_cast<int>(scaled_w) / 2;
    const int half_h = static_cast<int>(scaled_h) / 2;
    const int rx0 = std::max(0, cx - half_w), ry0 = std::max(0, cy - half_h);
    const int rx1 = half_w > width - cx ? width : cx + half_w;
    const int ry1 = half_h > height - cy ? height : cy + half_h;
    if (run_crop(rgb, width, height, rx0, ry0, rx1, ry1, values)) return -1;
    for (int i = 0; i < 68; ++i) {
        points136[2 * i] = rx0 + values[2 * i] * (rx1 - rx0);
        points136[2 * i + 1] = ry0 + values[2 * i + 1] * (ry1 - ry0);
    }
    return 0;
}

extern "C" int vg_landmark_run(const unsigned char* rgb, int width, int height,
                               float* values, int capacity) {
    if (!values || capacity < kOutputs) return -1;
    const int side = std::min(width, height) / 2;
    return run_crop(rgb, width, height, width / 2 - side / 2, height / 2 - side / 2,
                    width / 2 + side / 2, height / 2 + side / 2, values);
}

extern "C" int vg_landmark_run_auto(const unsigned char* rgb, int width, int height,
                                    float* points136, int* bbox, float* values, int capacity) {
    if (!points136 || !bbox || !values || capacity < kOutputs) return -1;
    const int side = std::min(width, height) / 2;
    int x0 = width / 2 - side / 2, y0 = height / 2 - side / 2;
    int x1 = x0 + side, y1 = y0 + side;
    float first[kOutputs], second[kOutputs];
    if (run_crop(rgb, width, height, x0, y0, x1, y1, first)) return -1;
    float px[68], py[68];
    for (int i = 0; i < 68; ++i) {
        px[i] = x0 + first[2 * i] * (x1 - x0);
        py[i] = y0 + first[2 * i + 1] * (y1 - y0);
    }
    float minx = px[0], maxx = px[0], miny = py[0], maxy = py[0];
    for (int i = 1; i < 68; ++i) {
        minx = std::fmin(minx, px[i]); maxx = std::fmax(maxx, px[i]);
        miny = std::fmin(miny, py[i]); maxy = std::fmax(maxy, py[i]);
    }
    const float spread = std::fmax(maxx - minx, maxy - miny);
    /* The bootstrap only claims a face when the cloud is neither a dot nor the
       whole crop; otherwise the "refined" crop would be meaningless. */
    if (!(spread > 0.18f * side && spread < 0.98f * side)) return -1;
    const float pad = 0.25f * spread;
    int rx0 = std::max(0, static_cast<int>(minx - pad));
    int ry0 = std::max(0, static_cast<int>(miny - pad));
    int rx1 = std::min(width, static_cast<int>(maxx + pad));
    int ry1 = std::min(height, static_cast<int>(maxy + pad));
    if (rx1 - rx0 < 32 || ry1 - ry0 < 32) return -1;
    if (run_crop(rgb, width, height, rx0, ry0, rx1, ry1, second)) return -1;
    float cx1 = 0, cy1 = 0, cx2 = 0, cy2 = 0;
    for (int i = 0; i < 68; ++i) { cx1 += px[i]; cy1 += py[i]; }
    cx1 /= 68; cy1 /= 68;
    for (int i = 0; i < 68; ++i) {
        const float x = rx0 + second[2 * i] * (rx1 - rx0);
        const float y = ry0 + second[2 * i + 1] * (ry1 - ry0);
        points136[2 * i] = x; points136[2 * i + 1] = y;
        cx2 += x; cy2 += y;
    }
    cx2 /= 68; cy2 /= 68;
    const float shift = std::hypot(cx2 - cx1, cy2 - cy1);
    if (shift > 0.25f * spread) return -1;
    bbox[0] = rx0; bbox[1] = ry0; bbox[2] = rx1; bbox[3] = ry1;
    std::memcpy(values, second, sizeof(float) * kOutputs);
    return 0;
}

extern "C" double vg_landmark_last_ms(void) { return g_last_ms; }

extern "C" void vg_landmark_close(void) {
    if (g_interpreter && g_session) g_interpreter->releaseSession(g_session);
    g_session = nullptr;
    g_interpreter.reset();
    g_host_input.reset();
}
