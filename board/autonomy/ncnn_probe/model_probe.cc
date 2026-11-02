// Raw float32 tensor probe; input provenance is supplied by the caller.
// The optional INT8 runtime still accepts/emits float32 tensors. No accuracy claim.
#include "net.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/resource.h>
#ifndef VG_PROBE_INT8_ENABLED
#define VG_PROBE_INT8_ENABLED 0
#endif
#ifndef VG_PROBE_OPENMP_ENABLED
#define VG_PROBE_OPENMP_ENABLED 0
#endif

static unsigned positive(const char* text, unsigned maximum) {
    const std::string value(text);
    if (value.empty() || value.find_first_not_of("0123456789")!=std::string::npos)
        throw std::runtime_error("Expected positive integer argument");
    size_t end=0;
    const unsigned long number=std::stoul(value,&end);
    if (end!=value.size() || !number || number>maximum) throw std::runtime_error("Integer argument out of range");
    return static_cast<unsigned>(number);
}
static std::string quoted(const std::string& s) {
    std::string out="\"";
    const char* hex="0123456789abcdef";
    for (unsigned char c : s) {
        if (c=='"' || c=='\\') {out+='\\';out+=static_cast<char>(c);}
        else if (c<32) {out+="\\u00";out+=hex[c>>4];out+=hex[c&15];}
        else out+=static_cast<char>(c);
    }
    return out+'"';
}
static void little_endian_f32(std::ofstream& stream, float value) {
    static_assert(sizeof(float)==4 && std::numeric_limits<float>::is_iec559,"IEEE754 float32 required");
    const unsigned char* bytes=reinterpret_cast<const unsigned char*>(&value);
    const uint32_t one=1;
    const bool little=*reinterpret_cast<const unsigned char*>(&one)==1;
    for (int k=0;k<4;++k) stream.put(static_cast<char>(bytes[little?k:3-k]));
}
static void load_input(const char* path, ncnn::Mat& input, unsigned width, unsigned height) {
    const size_t plane=static_cast<size_t>(width)*height;
    const size_t bytes=plane*3*sizeof(float);
    std::ifstream stream(path,std::ios::binary|std::ios::ate);
    if (!stream || stream.tellg()!=static_cast<std::streamoff>(bytes))
        throw std::runtime_error("Input must contain exactly W*H*3 little-endian float32 scalars");
    stream.seekg(0);
    // Each channel may contain NCNN alignment padding: never memcpy a packed
    // file directly across Mat.cstep. Read semantic planar elements per channel.
    for (unsigned channel=0;channel<3;++channel) {
        float* destination=input.channel(static_cast<int>(channel));
        for (size_t k=0;k<plane;++k) {
            unsigned char b[4];
            if (!stream.read(reinterpret_cast<char*>(b),4)) throw std::runtime_error("Input read failed");
            const uint32_t bits=static_cast<uint32_t>(b[0])|(static_cast<uint32_t>(b[1])<<8)|
                (static_cast<uint32_t>(b[2])<<16)|(static_cast<uint32_t>(b[3])<<24);
            float value;
            std::memcpy(&value,&bits,4);
            if (!std::isfinite(value)) throw std::runtime_error("Nonfinite input scalar");
            destination[k]=value;
        }
    }
}
int main(int argc,char** argv) {
    try {
        if (argc!=11 && argc!=12) {
            std::cerr<<"usage: vg_ncnn_probe model.param model.bin width height input_blob output_blob output_prefix iterations threads expected_elements [raw_input.f32|--zeros]\n"
                        "Default synthetic planar float32 zeros. File input must already be preprocessed; no face/accuracy claim.\n";
            return 2;
        }
        const unsigned width=positive(argv[3],4096),height=positive(argv[4],4096);
        const unsigned iterations=positive(argv[8],10000),threads=positive(argv[9],2);
        const unsigned expected=positive(argv[10],10000000);
        ncnn::Net net;
        net.opt.num_threads=static_cast<int>(threads);
        net.opt.use_vulkan_compute=false;
        net.opt.use_fp16_packed=false;
        net.opt.use_fp16_storage=false;
        net.opt.use_fp16_arithmetic=false;
        net.opt.use_bf16_storage=false;
        net.opt.use_int8_inference=VG_PROBE_INT8_ENABLED;
        net.opt.use_int8_packed=VG_PROBE_INT8_ENABLED;
        net.opt.use_int8_storage=VG_PROBE_INT8_ENABLED;
        net.opt.use_int8_arithmetic=VG_PROBE_INT8_ENABLED;
        const char* kernel_env=std::getenv("VG_NCNN_KERNELS");
        const std::string kernels=kernel_env?kernel_env:"default";
        if (kernels!="default" && kernels!="winograd_only" && kernels!="sgemm_only" && kernels!="direct")
            throw std::runtime_error("Invalid VG_NCNN_KERNELS");
        net.opt.use_winograd_convolution=kernels=="default" || kernels=="winograd_only";
        net.opt.use_sgemm_convolution=kernels=="default" || kernels=="sgemm_only";
        const char* packing_env=std::getenv("VG_NCNN_PACKING");
        const std::string packing=packing_env?packing_env:"1";
        if (packing!="0" && packing!="1") throw std::runtime_error("Invalid VG_NCNN_PACKING");
        net.opt.use_packing_layout=packing=="1";
        if (net.load_param(argv[1]) || net.load_model(argv[2])) throw std::runtime_error("NCNN parameter/model loading failed");
        ncnn::Mat input(static_cast<int>(width),static_cast<int>(height),3);
        if (input.empty()) throw std::runtime_error("Input allocation failed");
        input.fill(0.f);
        const bool synthetic=argc==11 || std::string(argv[11])=="--zeros";
        if (!synthetic) load_input(argv[11],input,width,height);
        ncnn::Mat output;
        auto invoke=[&]() {
            // A fresh Extractor prevents repeated extract() returning cached blobs.
            ncnn::Extractor extractor=net.create_extractor();
            extractor.set_light_mode(true);
            if (extractor.input(argv[5],input) || extractor.extract(argv[6],output,1))
                throw std::runtime_error("NCNN input/extract failed");
            if (output.empty()) throw std::runtime_error("Empty model output");
        };
        invoke(); // One unmeasured warmup; measured iterations include extractor/input/extract.
        std::vector<double> times;
        for (unsigned k=0;k<iterations;++k) {
            output=ncnn::Mat();
            const auto before=std::chrono::steady_clock::now();
            invoke();
            times.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-before).count());
        }
        if (output.dims!=1 || output.elembits()!=32 || (output.elempack!=1 && output.elempack!=4))
            throw std::runtime_error("Expected FP32 vector output with elempack 1 or 4");
        if (output.w<=0 || static_cast<size_t>(output.w)*output.elempack!=expected)
            throw std::runtime_error("Output scalar element count differs from expected_elements");
        ncnn::Mat unpacked;
        ncnn::convert_packing(output,unpacked,1,net.opt);
        if (unpacked.empty() || unpacked.dims!=1 || unpacked.elempack!=1 || unpacked.elembits()!=32 ||
            static_cast<unsigned>(unpacked.w)!=expected) throw std::runtime_error("Output unpacking failed");
        const float* values=unpacked;
        for (unsigned k=0;k<expected;++k) if (!std::isfinite(values[k])) throw std::runtime_error("Nonfinite output scalar");
        struct rusage usage={};
        if (getrusage(RUSAGE_SELF,&usage)) throw std::runtime_error("getrusage failed");
        const std::string prefix(argv[7]);
        std::ofstream binary(prefix+".output0.f32",std::ios::binary);
        if (!binary) throw std::runtime_error("Cannot create output tensor");
        for (unsigned k=0;k<expected;++k) little_endian_f32(binary,values[k]);
        binary.close();
        if (!binary) throw std::runtime_error("Output tensor write failed");
        std::sort(times.begin(),times.end());
        double sum=0;
        for (double t:times) sum+=t;
        std::ofstream metadata(prefix+".json");
        if (!metadata) throw std::runtime_error("Cannot create metadata");
        metadata.precision(17);
        metadata<<"{\"kind\":\"ncnn_model_tensor_probe\",\"synthetic_input\":"<<(synthetic?"true":"false")
                <<",\"input_kind\":"<<quoted(synthetic?"zeros":"file")
                <<",\"input_provenance_verified\":false,\"landmark_pipeline_verified\":false,"
                  "\"input_layout\":\"planar_float32\",\"channel_order_verified\":false,\"fp16\":false,\"bf16\":false,"
                <<"\"int8\":"<<(VG_PROBE_INT8_ENABLED?"true":"false")
                <<",\"quantized_model_verified\":false,\"threads_requested\":"<<threads
                <<",\"ncnn_openmp_build_requested\":"<<(VG_PROBE_OPENMP_ENABLED?"true":"false")<<','
                <<"\"packing\":"<<(net.opt.use_packing_layout?"true":"false")
                <<",\"kernel_policy\":"<<quoted(kernels)
                <<",\"winograd\":"<<(net.opt.use_winograd_convolution?"true":"false")
                <<",\"sgemm\":"<<(net.opt.use_sgemm_convolution?"true":"false")
                <<",\"fresh_extractor_each_iteration\":true,\"warmup_iterations\":1,"
                <<"\"input_blob\":"<<quoted(argv[5])<<",\"output_blob\":"<<quoted(argv[6])
                <<",\"input_shape_whc\":["<<width<<','<<height<<",3],\"iterations\":"<<iterations
                <<",\"threads\":"<<threads<<",\"mean_ms\":"<<sum/iterations
                <<",\"p50_ms\":"<<times[(iterations-1)/2]
                <<",\"p95_ms\":"<<times[static_cast<size_t>(std::ceil(iterations*.95))-1]
                <<",\"max_rss_kib\":"<<usage.ru_maxrss
                <<",\"outputs\":[{\"slot\":0,\"elements\":"<<expected<<",\"shape\":["<<expected
                <<"],\"source_dims\":"<<output.dims<<",\"source_w\":"<<output.w
                <<",\"source_elempack\":"<<output.elempack<<",\"storage\":\"little_endian_float32\"}]}\n";
        metadata.close();
        if (!metadata) throw std::runtime_error("Metadata write failed");
        std::cout<<"NCNN_MODEL_PROBE_PASS synthetic_input="<<(synthetic?"true":"false")<<" output_elements="<<expected<<'\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr<<"NCNN_MODEL_PROBE_FAIL: "<<error.what()<<'\n';
        return 1;
    }
}
