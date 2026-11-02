// Inspect the fixed converted model using the same official FlatBuffers schema.
#include "MNN_generated.h"
#include <cmath>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <vector>

template<typename T> static size_t count(const T* values) { return values ? values->size() : 0; }
int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::runtime_error("usage: audit_mnn_model model.mnn");
        std::ifstream file(argv[1], std::ios::binary);
        if (!file) throw std::runtime_error("cannot read model");
        std::vector<char> raw((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        flatbuffers::Verifier verifier(reinterpret_cast<const uint8_t*>(raw.data()), raw.size());
        if (!MNN::VerifyNetBuffer(verifier)) throw std::runtime_error("invalid MNN FlatBuffer");
        const auto* net = MNN::GetNet(raw.data());
        if (!net->oplists()) throw std::runtime_error("no operator list");
        size_t convs = 0, weights = 0, biases = 0, blobs = 0;
        for (const auto* op : *net->oplists()) {
            if (const auto* conv = op->main_as_Convolution2D()) {
                if (conv->quanParameter() || conv->symmetricQuan() || count(conv->external()) || !conv->weight())
                    throw std::runtime_error("quantized, FP16/compressed or external convolution weights");
                for (float value : *conv->weight()) if (!std::isfinite(value)) throw std::runtime_error("nonfinite weight");
                if (conv->bias()) for (float value : *conv->bias())
                    if (!std::isfinite(value)) throw std::runtime_error("nonfinite bias");
                ++convs;
                weights += count(conv->weight());
                biases += count(conv->bias());
            } else if (const auto* blob = op->main_as_Blob()) {
                if (blob->dataType() == MNN::DataType_DT_HALF || blob->dataType() == MNN::DataType_DT_BFLOAT16 ||
                    count(blob->external()) || count(blob->uint8s()) || count(blob->int8s()))
                    throw std::runtime_error("half, byte or external constant storage");
                if (blob->float32s()) for (float value : *blob->float32s())
                    if (!std::isfinite(value)) throw std::runtime_error("nonfinite constant");
                ++blobs;
            }
        }
        if (!convs || !weights) throw std::runtime_error("no FP32 convolution weights found");
        std::cout << "{\"kind\":\"mnn_fp32_weight_storage_audit\",\"operators\":" << net->oplists()->size()
                  << ",\"convolution_tables\":" << convs << ",\"float32_weights\":" << weights
                  << ",\"float32_biases\":" << biases << ",\"constant_blob_tables\":" << blobs
                  << ",\"quantized_convolution_tables\":0,\"compressed_or_external_convolution_tables\":0,"
                     "\"accuracy_verified\":false}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
