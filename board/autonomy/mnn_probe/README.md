# Peppa FP32 / MNN CPU张量探针

2026-10-30。本目录是独立引擎预算评估，不改后端、阈值或点位映射，不控制板卡。
输入必须由调用者预处理。本轮只使用固定seed合成输入，不上传/分发人脸素材。

## 固定来源与实际产物

【已验证】官方[3.6.1 release](https://github.com/alibaba/MNN/releases/tag/3.6.1)，
提交`d407447ed56c4121a11ccbd266dc184ca1ead0c2`，MNN使用Apache-2.0。
归档URL为`https://codeload.github.com/alibaba/MNN/tar.gz/refs/tags/3.6.1`，
94212337字节，SHA256 `4b6065c4e2674318f5bf1dc75836ce4d30c17bfe598c4a1b11b7d0b2092b06e6`。
已读该源码根`AGENTS.md`，未读取/修改其禁止访问的内部代码，`MNN_INTERNAL=OFF`。
原Peppa的来源/许可/前处理仍以`../lightweight_candidates.md`及固定作者源码为准。

本地证据根：`D:/Desktop/AMD/.tmp/autonomy/mnn/`；WSL持久目录：
`/root/vigilens/autonomy-20261030/mnn/`，包含`source/MNN-3.6.1`、`host`、`arm`、`artifacts`。
以下两件产物已交主代理审核并冻结，后续重建应使用新目录，不能覆盖本轮证据：

| Windows证据根下文件 | 字节 | SHA256 |
|---|---:|---|
| `vg_mnn_probe` | 11211040 | `3d42f95f68c13c0599e1547a919bd404a62983454ec5f4d735aa27f221790ad9` |
| `peppa.mnn` | 936144 | `2a22689a611d4d4b17da3f5e6824508d2e9ea0d08b197ce1fdc30836b8a683fc` |

`source-manifest.json`、`artifact-manifest.json`记录下载/构建/转换/输出hash。
板端执行及性能结论由主代理另行取证；不能引用下面的host耗时为Cortex-A9耗时。

## 构建与转换复现

在WSL Ubuntu执行，独立目录避免与其他引擎构建竞争；现有protobuf为3.20.3。
`build.sh`不下载依赖，不修改官方源码，使用已解压官方源。可用`VG_CMAKE`、`VG_PROTOBUF`、
`VG_ARM_TOOLCHAIN`、`VG_JOBS`覆盖工具路径/并行数；ARM工具链复用`../armv7.cmake`。

```bash
bash /mnt/d/Desktop/AMD/board/autonomy/mnn_probe/build.sh host \
  /root/vigilens/autonomy-20261030/mnn/source/MNN-3.6.1 \
  /root/vigilens/autonomy-20261030/mnn/host
bash /mnt/d/Desktop/AMD/board/autonomy/mnn_probe/build.sh arm \
  /root/vigilens/autonomy-20261030/mnn/source/MNN-3.6.1 \
  /root/vigilens/autonomy-20261030/mnn/arm
```

【已验证】GNU8.3 ARM构建为静态MNN、静态libstdc++/libgcc，CPU ThreadPool开启。
GPU/OpenCL/OpenGL/Vulkan/CUDA/NNAPI/ARM82/ARMv7-FP16/BF16等关闭。
实际`arm-cpu-flags.txt`为`-O3`，无`-Ofast`或`-ffast-math`。
MNN上游Linux armv7配置先添加`-mfpu=neon-vfpv4`，随后最终`-mfpu=neon`覆盖；
实际ELF标记VFPv3、NEONv1、VFP硬浮点传参，见`arm-elf.txt`。
`-mcpu=cortex-a9`与上游`-march=armv7-a`产生的同级ISA警告已保留在构建日志。
没有声称全运行时已禁用flush-to-zero；转换器明确记录原权重subnormal警告，数值差需真实对拍。

ELF动态依赖仅`libpthread.so.0`、`libdl.so.2`、`libm.so.6`、`libc.so.6`、`ld-linux-armhf.so.3`，
没有动态libstdc++或libgcc；最高所需GLIBC为2.27。此为静态审计，不能代替目标板实际运行。

转换命令实际执行如下，未加FP16、weightQuant或量化参数：

```bash
/root/vigilens/autonomy-20261030/mnn/host/mnn-runtime/MNNConvert -f ONNX \
  --modelFile /mnt/d/Desktop/AMD/.tmp/autonomy/candidates/peppa/slim_160_latest.onnx \
  --MNNModel /root/vigilens/autonomy-20261030/mnn/artifacts/peppa.mnn \
  --bizCode VigiLensPeppaFP32
/root/vigilens/autonomy-20261030/mnn/host/audit_mnn_model \
  /root/vigilens/autonomy-20261030/mnn/artifacts/peppa.mnn
```

【已验证】`audit_model.cc`用同版官方FlatBuffers schema读取模型表：54个op，26个Convolution2D表，
229728个FP32 weight、2415个FP32 bias，5个常量Blob表；无quantization参数、压缩/外置卷积权重。
这是权重存储检查；`Precision_High`是另一个运行时配置，二者不能互相替代。

## 探针接口和计时范围

```text
vg_mnn_probe model.mnn input.f32 output_prefix iterations threads
```

`iterations`范围1..10000，`threads`仅1/2。固定输入`input1`为NCHW `[1,3,160,160]`，
little-endian FP32恰307200字节，拒绝非有限值。输出`output1`必须恰143个语义标量。
输入/输出通过`Tensor::CAFFE`主机张量拷贝，不拿内部NC4HW4 padding计数充作元素数；
输出检查全部有限，保存为572字节little-endian FP32及JSON元数据。
本探针不解释末7个辅助量，也不计算EAR/MAR、头姿或额头ROI。

CPU、`Precision_High`。一次不计时预热后，每轮重新拷入输入并调用`runSession`；
计时只覆盖`runSession`，排除模型加载、session创建、输入拷贝、文件读写和输出解包。
输出记录mean/p50/p95、Linux `ru_maxrss`、请求线程数和配置，线程请求不冒充实际并行效率证明。
`input_provenance_verified`、`channel_order_verified`、`landmark_pipeline_verified`、
`accuracy_verified`、`full_pipeline_realtime_verified`始终为false。

## 本机真实执行与对拍

【已验证】host probe使用`peppa_input_synthetic.f32`：seed=20261030、uniform[-1,1]，
输入SHA256 `1eebed2a407647faa48624fb9b0756b6afd1867bacbf9499643dc763fa7a47dd`。
host输出143值全部有限，SHA256 `651e37f6baff23f66e828641bd291ebc6e0c5fe669f2cdabe624fbf2da07d20d`。
完整差异、参考文件路径和hash见`host-comparison.json`：

| 参考引擎/已有输出 | 全143最大绝对差 | 前136坐标最大绝对差 |
|---|---:|---:|
| ONNXRuntime | 1.1920928955078125e-5 | 9.5367431640625e-7 |
| OpenCV DNN | 8.940696716308594e-7 | 3.5762786865234375e-7 |
| 原NCNN板端FP32 | 3.814697265625e-6 | 1.7881393432617188e-7 |

未设新容差，未据此声明准确性。原视频99帧验证及MNN目标板耗时不属于本子任务已完成结论。

## 板端原始输出回读（离线，不重跑板卡）

`compare_board_outputs.py` 从**已记录的串口转录**里解出 `VIGI_FILE_BEGIN ... VIGI_FILE_END`
的 gzip/base64 张量，并与主机参考比较。白名单只有
`peppa_mnn_t2.output0.f32` 与 `peppa_mnn_pruned_t2.output0.f32`；严格 base64、
1 MiB 解压上限、572 字节长度、有限值检查，且日志必须出现冻结的输入/模型/探针 SHA256。

```powershell
. .\env.ps1
python board/autonomy/mnn_probe/compare_board_outputs.py `
  --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_original.log `
  --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_pruned.log `
  --out metrics/evidence/2026-10-31_mnn_board_output_comparison.json
python -m pytest board/autonomy/mnn_probe/tests/test_compare_board_outputs.py -q
```

2026-10-31 实际结果：原图/压缩图 ARM 输出都是
`cde263832e4b17ea1f477df2d32b7799d1b047309a8382205a4979881a4996f7`（两者逐字节相同），
相对 MNN host 的 143 值最大差 1.37e-6 / 1.43e-6，前 136 坐标最大差 3.58e-7 / 3.87e-7。
单测 17 passed。这不是新上板运行，详见
[证据说明](../../../metrics/evidence/2026-10-31_mnn_board_output_comparison.md)。

## 独立集成测试

测试执行真实MNN host二进制，不使用假引擎。检查CLI边界、精确文件长度、NaN/Inf拒绝、
普通布局143元素输出、CPU/Precision_High配置，以及更换输入后输出随之变化。
缺少显式二进制/模型路径时跳过，不能把跳过当成推理验证。项目Python实际运行：

```powershell
. .\env.ps1
$env:VG_MNN_PROBE='/root/vigilens/autonomy-20261030/mnn/host/vg_mnn_probe'
$env:VG_MNN_MODEL='/root/vigilens/autonomy-20261030/mnn/artifacts/peppa.mnn'
$env:VG_MNN_RUNNER='wsl.exe -d Ubuntu --'
python -m pytest board/autonomy/mnn_probe/tests/test_mnn_probe.py -q
```

结果：`4 passed, 7 subtests passed in 3.71s`。本目录没有执行硬件控制命令。

## 压缩NCNN融合FP32图的标准ONNX导出

`export_ncnn_fp32_onnx.py`复用`../prune_peppa_fp32.py`的完整二进制、属性与拓扑验证器。
仅接受固定Peppa输入/输出、已确认ReLU的普通/逐通道卷积、Split、空间mean、向量Concat及143输出FC。
不重新裁剪、不改权重、不量化、不使用FP16。来源是已融合的NCNN图，不能声称原始ONNX subnormal逐位等价。

官方NCNN 20240410 `convolution.cpp::load_param`定义4=left、15=right(default left)、
14=top(default left)、16=bottom(default top)，导出ONNX pads顺序为[top,left,bottom,right]。
Conv/DW按OIHW读取现有权重，DW group等于实际通道数，后接显式Relu；Split逐分支Identity。
空间mean先按当前NCNN rank确认没有C轴，再加1补回batch轴；向量Concat axis0映射到ONNX axis1；
FC导出Gemm、transB=1，权重形状`[143,inputC]`。未知属性、自动padding及不支持的轴/步长等直接拒绝。
生成ONNX IR8/opset13，实际执行checker full_check与严格shape inference，全部中间形状必须具体且正数。

使用本地已固定的ONNX1.17.0/ORT1.20.1，不改venv全局依赖：

```powershell
. .\env.ps1
$env:PYTHONPATH=(Resolve-Path .tmp/autonomy/onnx_py).Path
python board/autonomy/mnn_probe/export_ncnn_fp32_onnx.py `
  --param .tmp/autonomy/candidates/peppa_ncnn_pruned_fp32/peppa.param `
  --weights .tmp/autonomy/candidates/peppa_ncnn_pruned_fp32/peppa.bin `
  --output-dir .tmp/autonomy/mnn/pruned
python -m pytest board/autonomy/mnn_probe/tests/test_export_ncnn_fp32_onnx.py -q
```

输出目录必须为空/不存在。本轮实际生成的目录已含证据，重跑请换新目录。
测试实际结果：`5 passed, 7 subtests passed`，包括ORT执行合成整数网络的精确输出检查，无新增测量容差。

【已验证】新资产在`.tmp/autonomy/mnn/pruned/`，原冻结MNN及ARM探针hash未变：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| `peppa_pruned.onnx` | 485503 | `81e56cbcbeeacaddadf8d90a1d822bc117fc4d2b20d6e4d63f3fcac912d8b3ff` |
| `peppa_pruned.mnn` | 483636 | `be908e37a2dba5fb1b43fcf242f54db5290800f999ff18c2406432726f19b138` |

新ONNX与压缩NCNN host重新执行同一固定合成输入：143值均有限，最大绝对差`3.0994415283203125e-6`、
前136坐标最大差`1.7881393432617188e-7`。NCNN host程序可执行INT8，但本轮所载图没有量化层；
不能把该程序的INT8许可标志冒充本模型量化。

相同MNNConvert命令仅改输入/输出路径及bizCode为`VigiLensPeppaPrunedFP32`，无FP16/量化参数。
新模型表仍54个op/26个Convolution2D表，116952个FP32 weight、1903个FP32 bias，无量化、压缩或外置卷积权重。
MNN host相对新ONNX(ORT)全部143最大差`2.1457672119140625e-6`、前136最大差`2.086162567138672e-7`；
相对压缩NCNN全部最大差`9.5367431640625e-7`、前136最大差`2.682209014892578e-7`。
差异与输出hash保存于`onnx-ncnn-comparison.json`、`mnn-host-comparison.json`和`artifact-manifest.json`，未设新容差。
新模型已交主代理做板端300次预算取证；本子任务没有执行硬件控制。

## 主代理追加取证：原版MNN

【已验证：读取主代理日志】`metrics/logs/autonomy_board_peppa_mnn.log`中，原版MNN FP32、
CPU Precision_High、一次预热后300次，Mizar-Z7020单线程均值57.41756855 ms，
双线程均值31.21068677 ms、p95 31.313899 ms、峰值RSS 7740 KiB。
双线程进入`1000/30`单项必要预算，但只计runSession；没有证明完整链路30 fps。

`metrics/logs/autonomy_peppa_mnn_original_development.json`记录同视频99帧、6732点，
原MNN相对NCNN FP32的原裁剪像素位移p95为`5.125999450683594e-5 px`。
这是同视频开发验证集的数值一致性；没有人工真值，不能称独立准确率或阈值标定。
