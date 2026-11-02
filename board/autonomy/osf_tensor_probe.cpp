/* OpenSeeFace U/V model-only FP32 CPU probe. Synthetic caller input; no image
 * detection, point mapping, confidence gate or full-pipeline performance claim. */
#include <MNN/Interpreter.hpp>
#include <MNN/Tensor.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <vector>
#include <time.h>
static double cpu_ms(){timespec t{};if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&t))throw std::runtime_error("CPU clock failed");return t.tv_sec*1000.+t.tv_nsec/1e6;}
int main(int argc,char **argv) {
    if(argc!=6)return 2;
    try {
        char *end;unsigned long iterations=std::strtoul(argv[5],&end,10);
        if(!*argv[5]||*end||!iterations||iterations>1000)return 2;
        std::unique_ptr<MNN::Interpreter> model(MNN::Interpreter::createFromFile(argv[1]));if(!model)throw std::runtime_error("model load failed");
        MNN::BackendConfig backend;backend.precision=MNN::BackendConfig::Precision_High;
        MNN::ScheduleConfig config;config.type=MNN_FORWARD_CPU;config.numThread=1;config.backendConfig=&backend;
        auto *session=model->createSession(config);if(!session)throw std::runtime_error("session failed");
        if(model->getSessionInputAll(session).size()!=1||model->getSessionOutputAll(session).size()!=1)throw std::runtime_error("expected one input/output");
        auto *input=model->getSessionInput(session,nullptr);auto *output=model->getSessionOutput(session,nullptr);
        if(!input||!output||input->shape()!=std::vector<int>({1,3,112,112})||output->shape()!=std::vector<int>({1,198,14,14}))throw std::runtime_error("unexpected input/output shape");
        MNN::Tensor host(input,MNN::Tensor::CAFFE),result(output,MNN::Tensor::CAFFE);
        if(host.elementSize()!=3*112*112||result.elementSize()!=198*14*14||input->getType().bits!=32||output->getType().bits!=32)throw std::runtime_error("not expected float storage");
        FILE *f=std::fopen(argv[2],"rb");if(!f)throw std::runtime_error("input unavailable");
        const size_t count=3*112*112;
        if(std::fread(host.host<float>(),sizeof(float),count,f)!=count||std::fgetc(f)!=EOF)throw std::runtime_error("input size mismatch");
        std::fclose(f);
        for(size_t i=0;i<count;++i)if(!std::isfinite(host.host<float>()[i]))throw std::runtime_error("nonfinite input");
        std::vector<double> wall,cpu;
        for(unsigned long n=0;n<=iterations;++n) {
            if(!input->copyFromHostTensor(&host))throw std::runtime_error("input copy failed");
            const auto start=std::chrono::steady_clock::now();const double cpu_start=cpu_ms();
            if(model->runSession(session)!=MNN::NO_ERROR)throw std::runtime_error("inference failed");
            const double c=cpu_ms()-cpu_start,w=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
            if(n){wall.push_back(w);cpu.push_back(c);}
        }
        if(!output->copyToHostTensor(&result))throw std::runtime_error("output copy failed");
        for(int i=0;i<result.elementSize();++i)if(!std::isfinite(result.host<float>()[i]))throw std::runtime_error("nonfinite output");
        FILE *blob=std::fopen(argv[3],"wb");if(!blob)throw std::runtime_error("output unavailable");
        if(std::fwrite(result.host<float>(),sizeof(float),result.elementSize(),blob)!=size_t(result.elementSize())||std::fclose(blob))throw std::runtime_error("output write failed");
        double sum=0,csum=0;for(double v:wall)sum+=v;for(double v:cpu)csum+=v;std::sort(wall.begin(),wall.end());
        FILE *report=std::fopen(argv[4],"w");if(!report)throw std::runtime_error("report unavailable");
        std::fprintf(report,"{\"iterations\":%lu,\"threads\":1,\"input_shape\":[1,3,112,112],\"output_shape\":[1,198,14,14],\"wall_ms_mean\":%.6f,\"wall_ms_max\":%.6f,\"caller_cpu_ms_mean\":%.6f,\"synthetic_only\":true,\"accuracy_verified\":false,\"full_pipeline\":false}\n",iterations,sum/iterations,wall.back(),csum/iterations);
        if(std::fclose(report))throw std::runtime_error("report close failed");
        std::puts("OSF_TENSOR_PROBE_PASS full_pipeline=0 accuracy_verified=0");return 0;
    } catch(const std::exception& e){std::fprintf(stderr,"OSF_TENSOR_PROBE_FAIL %s\n",e.what());return 1;}
}
