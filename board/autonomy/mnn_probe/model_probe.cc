// Fixed FP32 CPU tensor execution probe. No image preprocessing or accuracy claim.
#include <MNN/Interpreter.hpp>
#include <MNN/Tensor.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/resource.h>

static unsigned positive(const char* text, unsigned maximum) {
    const std::string value(text);
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Expected positive integer");
    const unsigned long number = std::stoul(value);
    if (!number || number > maximum) throw std::runtime_error("Integer argument out of range");
    return static_cast<unsigned>(number);
}
static std::string quoted(const std::string& value) {
    std::string result = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += static_cast<char>(c);
    }
    return result + '"';
}
static std::vector<float> loadInput(const char* path) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559, "FP32 required");
    const size_t count = 3 * 160 * 160;
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file || file.tellg() != static_cast<std::streamoff>(count * 4))
        throw std::runtime_error("Input must be exactly 307200 bytes of little-endian NCHW FP32");
    file.seekg(0);
    std::vector<float> values(count);
    for (size_t k = 0; k < count; ++k) {
        unsigned char bytes[4];
        if (!file.read(reinterpret_cast<char*>(bytes), 4)) throw std::runtime_error("Input read failed");
        const uint32_t bits = uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8) |
                              (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
        std::memcpy(&values[k], &bits, 4);
        if (!std::isfinite(values[k])) throw std::runtime_error("Nonfinite input scalar");
    }
    return values;
}
static size_t semanticElements(const MNN::Tensor& tensor) {
    size_t count = 1;
    if (tensor.dimensions() < 1 || tensor.dimensions() > 4) throw std::runtime_error("Unsupported tensor rank");
    for (int k = 0; k < tensor.dimensions(); ++k) {
        if (tensor.length(k) <= 0 || tensor.length(k) > 1000000 || count > 1000000 / size_t(tensor.length(k)))
            throw std::runtime_error("Invalid tensor extent");
        count *= size_t(tensor.length(k));
    }
    return count;
}
static void requireFloat(const MNN::Tensor& tensor) {
    const auto type = tensor.getType();
    if (type.code != halide_type_float || type.bits != 32 || type.lanes != 1)
        throw std::runtime_error("Expected scalar float32 tensor");
}
static void writeFloat(std::ofstream& stream, float value) {
    uint32_t bits;
    std::memcpy(&bits, &value, 4);
    for (int k = 0; k < 4; ++k) stream.put(static_cast<char>((bits >> (8 * k)) & 255));
}
int main(int argc, char** argv) {
    try {
        if (argc != 6) {
            std::cerr << "usage: vg_mnn_probe model.mnn input.f32 output_prefix iterations threads\n";
            return 2;
        }
        const unsigned iterations = positive(argv[4], 10000), threads = positive(argv[5], 2);
        const auto values = loadInput(argv[2]);
        std::unique_ptr<MNN::Interpreter> model(MNN::Interpreter::createFromFile(argv[1]));
        if (!model) throw std::runtime_error("Cannot load MNN model");
        MNN::BackendConfig backend;
        backend.precision = MNN::BackendConfig::Precision_High;
        MNN::ScheduleConfig schedule;
        schedule.type = MNN_FORWARD_CPU;
        schedule.numThread = static_cast<int>(threads);
        schedule.backendConfig = &backend;
        auto* session = model->createSession(schedule);
        if (!session) throw std::runtime_error("Cannot create CPU session");
        if (model->getSessionInputAll(session).size() != 1 || model->getSessionOutputAll(session).size() != 1)
            throw std::runtime_error("Expected one model input and one model output");
        auto* input = model->getSessionInput(session, "input1");
        auto* output = model->getSessionOutput(session, "output1");
        if (!input || !output || input->shape() != std::vector<int>({1, 3, 160, 160}))
            throw std::runtime_error("Expected input1 NCHW [1,3,160,160] and output1");
        requireFloat(*input);
        MNN::Tensor hostInput(input, MNN::Tensor::CAFFE);
        requireFloat(hostInput);
        if (hostInput.getDimensionType() != MNN::Tensor::CAFFE || semanticElements(hostInput) != values.size() ||
            hostInput.elementSize() != static_cast<int>(values.size()) || !hostInput.host<float>())
            throw std::runtime_error("Input host tensor is not ordinary contiguous NCHW");
        std::copy(values.begin(), values.end(), hostInput.host<float>());
        auto run = [&]() {
            if (!input->copyFromHostTensor(&hostInput)) throw std::runtime_error("Input copy failed");
            const auto begin = std::chrono::steady_clock::now();
            const auto status = model->runSession(session); // CPU executes the graph on every call.
            const double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now()-begin).count();
            if (status != MNN::NO_ERROR || !std::isfinite(elapsed) || elapsed < 0)
                throw std::runtime_error("Session execution failed");
            return elapsed;
        };
        run(); // Exactly one unmeasured warmup.
        std::vector<double> timings;
        for (unsigned k = 0; k < iterations; ++k) timings.push_back(run());
        requireFloat(*output);
        if (semanticElements(*output) != 143) throw std::runtime_error("Output must contain 143 semantic scalars");
        MNN::Tensor hostOutput(output, MNN::Tensor::CAFFE);
        requireFloat(hostOutput);
        if (hostOutput.getDimensionType() != MNN::Tensor::CAFFE || semanticElements(hostOutput) != 143 ||
            hostOutput.elementSize() != 143 || !hostOutput.host<float>() || !output->copyToHostTensor(&hostOutput))
            throw std::runtime_error("Output CAFFE host unpacking failed");
        for (size_t k = 0; k < 143; ++k)
            if (!std::isfinite(hostOutput.host<float>()[k])) throw std::runtime_error("Nonfinite output scalar");
        struct rusage usage = {};
        if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("RSS query failed");
        const std::string prefix(argv[3]);
        std::ofstream binary(prefix+".output0.f32", std::ios::binary);
        if (!binary) throw std::runtime_error("Cannot create output tensor");
        for (size_t k = 0; k < 143; ++k) writeFloat(binary, hostOutput.host<float>()[k]);
        binary.close();
        if (!binary) throw std::runtime_error("Tensor write failed");
        std::sort(timings.begin(), timings.end());
        double sum = 0;
        for (double t : timings) sum += t;
        std::ofstream metadata(prefix+".json");
        if (!metadata) throw std::runtime_error("Cannot create metadata");
        metadata.precision(17);
        metadata << "{\"kind\":\"mnn_model_tensor_probe\",\"mnn_version\":" << quoted(MNN::getVersion())
                 << ",\"backend\":\"CPU\",\"precision\":\"Precision_High\",\"fp16\":false,\"int8\":false,"
                    "\"input_kind\":\"file\",\"input_provenance_verified\":false,\"channel_order_verified\":false,"
                    "\"landmark_pipeline_verified\":false,\"full_pipeline_realtime_verified\":false,\"accuracy_verified\":false,"
                    "\"input_layout\":\"NCHW\",\"input_shape\":[1,3,160,160],\"input_blob\":\"input1\",\"output_blob\":\"output1\","
                    "\"input_copy_each_iteration\":true,\"run_session_each_iteration\":true,\"timing_scope\":\"runSession_only\","
                    "\"warmup_iterations\":1,\"iterations\":" << iterations << ",\"threads_requested\":" << threads
                 << ",\"mean_ms\":" << sum/iterations << ",\"p50_ms\":" << timings[(iterations-1)/2]
                 << ",\"p95_ms\":" << timings[static_cast<size_t>(std::ceil(iterations*.95))-1]
                 << ",\"max_rss_kib\":" << usage.ru_maxrss
                 << ",\"outputs\":[{\"elements\":143,\"host_layout\":\"CAFFE\",\"host_element_count\":143,\"shape\":[";
        for (int k = 0; k < hostOutput.dimensions(); ++k) {
            if (k) metadata << ',';
            metadata << hostOutput.length(k);
        }
        metadata << "],\"storage\":\"little_endian_float32\"}]}\n";
        metadata.close();
        if (!metadata) throw std::runtime_error("Metadata write failed");
        std::cout << "MNN_MODEL_PROBE_PASS output_elements=143\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "MNN_MODEL_PROBE_FAIL: " << error.what() << '\n';
        return 1;
    }
}
