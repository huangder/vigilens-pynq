// Model execution probe only: does not detect faces or emit fatigue metrics.
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
#include "tensorflow/lite/interpreter.h"
#include "tensorflow/lite/kernels/register.h"
#include "tensorflow/lite/model_builder.h"
#include "tensorflow/lite/builtin_ops.h"
#ifdef VG_ATTENTION_OPS
#include "mediapipe/util/tflite/operations/landmarks_to_transform_matrix.h"
#include "mediapipe/util/tflite/operations/transform_landmarks.h"
#include "mediapipe/util/tflite/operations/transform_tensor_bilinear.h"
#endif

static unsigned count_arg(const char* value, unsigned upper) {
  size_t end = 0;
  std::string s(value);
  if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
    throw std::runtime_error("Expected a positive integer");
  auto n = std::stoul(s, &end);
  if (end != s.size() || !n || n > upper) throw std::runtime_error("Argument out of range");
  return static_cast<unsigned>(n);
}

int main(int argc, char** argv) {
  try {
    if (argc != 6) {
      std::cerr << "usage: vg_model_probe model.tflite input.f32|--zeros output-prefix iterations threads\n"
                   "Input is exact-size native little-endian float32 in MODEL tensor order.\n"
                   "--zeros executes a synthetic tensor; it is not a face/accuracy test.\n";
      return 2;
    }
    const unsigned iterations = count_arg(argv[4], 10000), threads = count_arg(argv[5], 2);
    auto model = tflite::FlatBufferModel::BuildFromFile(argv[1]);
    if (!model) throw std::runtime_error("Cannot read/verify TFLite model");
    tflite::ops::builtin::BuiltinOpResolver resolver;
#ifdef VG_ATTENTION_OPS
    resolver.AddCustom("Landmarks2TransformMatrix",
        mediapipe::tflite_operations::RegisterLandmarksToTransformMatrixV2(), 2);
    resolver.AddCustom("TransformLandmarks",
        mediapipe::tflite_operations::RegisterTransformLandmarksV2(), 2);
    resolver.AddCustom("TransformTensorBilinear",
        mediapipe::tflite_operations::RegisterTransformTensorBilinearV2(), 2);
#endif
    std::unique_ptr<tflite::Interpreter> engine;
    tflite::InterpreterBuilder builder(*model, resolver);
    if (builder.SetNumThreads(threads) != kTfLiteOk || builder(&engine) != kTfLiteOk || !engine)
      throw std::runtime_error("Model/operator resolution failed");
    if (engine->SetNumThreads(threads) != kTfLiteOk || engine->AllocateTensors() != kTfLiteOk)
      throw std::runtime_error("Tensor allocation failed");
    if (engine->inputs().size() != 1) throw std::runtime_error("Probe requires one model input");
    auto* input = engine->tensor(engine->inputs()[0]);
    if (input->type != kTfLiteFloat32 || !input->data.f || input->bytes % sizeof(float))
      throw std::runtime_error("Probe requires a float32 input tensor");
    const bool synthetic = std::string(argv[2]) == "--zeros";
    if (synthetic) {
      std::memset(input->data.f, 0, input->bytes);
    } else {
      std::ifstream stream(argv[2], std::ios::binary | std::ios::ate);
      if (!stream || stream.tellg() != static_cast<std::streamoff>(input->bytes))
        throw std::runtime_error("Input byte length differs from model tensor");
      stream.seekg(0); stream.read(reinterpret_cast<char*>(input->data.f), input->bytes);
      if (!stream) throw std::runtime_error("Input read failed");
      for (size_t i = 0; i < input->bytes / sizeof(float); ++i)
        if (!std::isfinite(input->data.f[i])) throw std::runtime_error("Nonfinite input");
    }
    if (engine->Invoke() != kTfLiteOk) throw std::runtime_error("Warmup invoke failed");
    unsigned delegate_nodes = 0;
    for (int index : engine->execution_plan()) {
      const auto* entry = engine->node_and_registration(index);
      if (entry && entry->second.builtin_code == kTfLiteBuiltinDelegate) ++delegate_nodes;
    }
    std::vector<double> times;
    for (unsigned i = 0; i < iterations; ++i) {
      auto before = std::chrono::steady_clock::now();
      if (engine->Invoke() != kTfLiteOk) throw std::runtime_error("Invoke failed");
      times.push_back(std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - before).count());
    }
    std::sort(times.begin(), times.end());
    double sum = 0; for (double t : times) sum += t;
    struct rusage usage = {};
    if (getrusage(RUSAGE_SELF, &usage)) throw std::runtime_error("getrusage failed");
    std::string metadata_path = std::string(argv[3]) + ".json";
    std::ofstream metadata(metadata_path);
    if (!metadata) throw std::runtime_error("Cannot create metadata");
    metadata.precision(17);
    metadata << "{\"kind\":\"model_tensor_probe\",\"synthetic_input\":" << (synthetic ? "true" : "false")
             << ",\"landmark_pipeline_verified\":false,\"iterations\":" << iterations
             << ",\"threads\":" << threads << ",\"input_bytes\":" << input->bytes
             << ",\"delegate_nodes\":" << delegate_nodes
             << ",\"mean_ms\":" << sum / iterations << ",\"p50_ms\":" << times[(iterations - 1) / 2]
             << ",\"p95_ms\":" << times[static_cast<size_t>(std::ceil(iterations * .95)) - 1]
             << ",\"max_rss_kib\":" << usage.ru_maxrss << ",\"outputs\":[";
    for (size_t i = 0; i < engine->outputs().size(); ++i) {
      auto* output = engine->tensor(engine->outputs()[i]);
      if (output->type != kTfLiteFloat32 || !output->data.f || output->bytes % sizeof(float))
        throw std::runtime_error("Unexpected output type");
      for (size_t j = 0; j < output->bytes / sizeof(float); ++j)
        if (!std::isfinite(output->data.f[j])) throw std::runtime_error("Nonfinite model output");
      const std::string path = std::string(argv[3]) + ".output" + std::to_string(i) + ".f32";
      std::ofstream stream(path, std::ios::binary);
      stream.write(reinterpret_cast<const char*>(output->data.f), output->bytes);
      if (!stream) throw std::runtime_error("Output write failed");
      if (i) metadata << ',';
      metadata << "{\"slot\":" << i << ",\"elements\":" << output->bytes / sizeof(float) << ",\"shape\":[";
      for (int j = 0; j < output->dims->size; ++j) { if (j) metadata << ','; metadata << output->dims->data[j]; }
      metadata << "]}";
    }
    metadata << "]}\n"; metadata.close();
    if (!metadata) throw std::runtime_error("Metadata write failed");
    std::cout << "MODEL_PROBE_PASS outputs=" << engine->outputs().size()
              << " synthetic=" << synthetic << " delegate_nodes=" << delegate_nodes
              << " mean_ms=" << sum / iterations << '\n';
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "MODEL_PROBE_FAIL " << e.what() << '\n';
    return 1;
  }
}
