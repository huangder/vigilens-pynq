/* PNet-only face detector probe (MTCNN stage 1) using ncnn.
 *
 * Diagnostic only; deployed detection uses face_detector.cpp (all three stages).
 * Input is packed RGB888, no JPEG dependency.
 *
 * Usage: vg_pnet_probe det1.param det1.bin frame.rgb width height threshold nms min_face factor
 * Prints: PNET boxes=<n> ms=<t> then one line per box (x1 y1 x2 y2 score).
 */
#include <cmath>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <algorithm>

#include <net.h>

namespace {

struct Box {
    float x1, y1, x2, y2, score;
};

constexpr int kMinSize = 12;
constexpr float kStride = 2.0f;
constexpr float kCell = 12.0f;

float iou(const Box& a, const Box& b) {
    const float x1 = std::fmax(a.x1, b.x1), y1 = std::fmax(a.y1, b.y1);
    const float x2 = std::fmin(a.x2, b.x2), y2 = std::fmin(a.y2, b.y2);
    const float inter = std::fmax(0.0f, x2 - x1) * std::fmax(0.0f, y2 - y1);
    const float aa = std::fmax(0.0f, a.x2 - a.x1) * std::fmax(0.0f, a.y2 - a.y1);
    const float bb = std::fmax(0.0f, b.x2 - b.x1) * std::fmax(0.0f, b.y2 - b.y1);
    const float denom = aa + bb - inter;
    return denom > 0 ? inter / denom : 0.0f;
}

void nms(std::vector<Box>* boxes, float threshold) {
    std::stable_sort(boxes->begin(), boxes->end(), [](const Box& a, const Box& b) {
        return a.score > b.score;
    });
    for (size_t i = 0; i < boxes->size(); ++i)
        for (size_t j = i + 1; j < boxes->size();) {
            if (iou((*boxes)[i], (*boxes)[j]) > threshold) {
                boxes->erase(boxes->begin() + static_cast<long>(j));
            } else {
                ++j;
            }
        }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 10) {
        std::fprintf(stderr, "usage: %s det1.param det1.bin frame.rgb width height threshold nms min_face factor\n", argv[0]);
        return 2;
    }
    const int width = std::atoi(argv[4]), height = std::atoi(argv[5]);
    const float threshold = std::atof(argv[6]), nms_threshold = std::atof(argv[7]);
    const float min_face = std::atof(argv[8]), factor = std::atof(argv[9]);
    if (width < 12 || height < 12 || width > 32768 || height > 32768 ||
        !std::isfinite(threshold) || threshold < 0 || threshold > 1 ||
        !std::isfinite(nms_threshold) || nms_threshold < 0 || nms_threshold > 1 ||
        !std::isfinite(min_face) || min_face < 12 || !std::isfinite(factor) || factor <= 0 || factor >= 1) return 2;
    std::vector<unsigned char> image(static_cast<size_t>(width) * height * 3);
    FILE* handle = std::fopen(argv[3], "rb");
    if (!handle) { std::perror("frame"); return 2; }
    if (std::fread(image.data(), 1, image.size(), handle) != image.size()) {
        std::fprintf(stderr, "FRAME_SIZE_FAIL\n"); return 2;
    }
    std::fclose(handle);
    ncnn::Net net;
    if (net.load_param(argv[1]) || net.load_model(argv[2])) {
        std::fprintf(stderr, "MODEL_LOAD_FAIL\n"); return 2;
    }
    const float mean[3] = {127.5f, 127.5f, 127.5f};
    const float norm[3] = {0.0078125f, 0.0078125f, 0.0078125f};
    std::vector<Box> candidates;
    const float min_side = static_cast<float>(std::min(width, height));
    const auto started = std::chrono::steady_clock::now();
    for (float scale = kMinSize / min_face; scale * min_side >= kMinSize; scale *= factor) {
        const int sw = static_cast<int>(std::round(width * scale));
        const int sh = static_cast<int>(std::round(height * scale));
        if (sw < kMinSize || sh < kMinSize) break;
        ncnn::Mat input = ncnn::Mat::from_pixels_resize(image.data(), ncnn::Mat::PIXEL_RGB,
                                                        width, height, sw, sh);
        input.substract_mean_normalize(mean, norm);
        ncnn::Extractor ex = net.create_extractor();
        ex.input("data", input);
        ncnn::Mat prob, reg;
        if (ex.extract("prob1", prob) || ex.extract("conv4-2", reg)) {
            std::fprintf(stderr, "EXTRACT_FAIL\n"); return 2;
        }
        for (int y = 0; y < prob.h; ++y)
            for (int x = 0; x < prob.w; ++x) {
                const float score = prob.channel(1).row(y)[x];
                if (score < threshold) continue;
                const float dx1 = reg.channel(0).row(y)[x];
                const float dy1 = reg.channel(1).row(y)[x];
                const float dx2 = reg.channel(2).row(y)[x];
                const float dy2 = reg.channel(3).row(y)[x];
                Box box;
                box.x1 = (x * kStride + dx1 * kCell) / scale;
                box.y1 = (y * kStride + dy1 * kCell) / scale;
                box.x2 = (x * kStride + kCell + dx2 * kCell) / scale;
                box.y2 = (y * kStride + kCell + dy2 * kCell) / scale;
                const float w = box.x2 - box.x1, h = box.y2 - box.y1;
                const float side = std::fmax(w, h);
                const float cx = box.x1 + w / 2, cy = box.y1 + h / 2;
                box.x1 = cx - side / 2; box.y1 = cy - side / 2;
                box.x2 = cx + side / 2; box.y2 = cy + side / 2;
                if (box.x2 <= box.x1 || box.y2 <= box.y1) continue;
                box.score = score;
                candidates.push_back(box);
            }
    }
    nms(&candidates, nms_threshold);
    const double elapsed_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - started).count();
    std::printf("PNET boxes=%zu threshold=%.2f min_face=%.1f ms=%.3f\n",
                candidates.size(), threshold, min_face, elapsed_ms);
    {
        size_t printed = 0;
        for (size_t i = 0; i < candidates.size() && printed < 10; ++i) {
            const float w = candidates[i].x2 - candidates[i].x1;
            const float h = candidates[i].y2 - candidates[i].y1;
            if (w < 8.0f || h < 8.0f) continue;
            std::printf("BOX %.2f %.2f %.2f %.2f %.4f\n", candidates[i].x1, candidates[i].y1,
                        candidates[i].x2, candidates[i].y2, candidates[i].score);
            ++printed;
        }
    }
    return 0;
}
