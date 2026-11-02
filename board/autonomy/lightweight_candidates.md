# 轻量关键点候选：资产与迁移边界

2026-10-30。此页记录来源核验、模型元数据、合成张量对拍和真实板端推理预算；不是准确率或完整链路30 fps验收。
模型、代码快照与审计JSON位于本机 `.tmp/autonomy/candidates/`，没有下载训练集或真人测试素材。

> 后续同日追加：Peppa KL INT8/首末层FP32混合图双线程约22.94/24.60ms进入单项预算，
> 但主机99帧开发集相对FP32点位偏移p95约9.60/8.78px，且PC/A9量化输出有差异，未接受为正式替换。
> FP32精确零通道压缩保持接近参考的输出，真板均值33.51ms仍略超预算。
> [原始证据与完整限定条件](../../metrics/evidence/2026-10-30_board_autonomy.md)。人工阈值标定仍缺当前素材真值。

## 已完成板端预算探针的候选

**PFLD-GhostOne 98点、112×112 ONNX**已经下载，且在现有OpenCV DNN上完成一次纯合成输入推理。
随后已完成NCNN FP32转换、同输入对拍及Mizar-Z7020真实板端推理。**双线程114.70774815 ms，未满足33.33 ms单项预算**。
它来自该派生模型创作者的仓库，并非PFLD论文原作者的权重；两者不能混称。

来源：[模型作者仓库](https://github.com/AnthonyF333/PFLD_GhostOne)，固定提交
`7061b5649ffdb6e7fbb4add2372346264a160343`。仓库采用Apache-2.0，已保存其LICENSE；
该结论描述仓库授权，不替代WFLW数据集及模型使用场景的许可核查。

| 本机资产（`candidates/`下） | 实读字节 | SHA256 |
|---|---:|---|
| `ghostone/onnx_models/PFLD_GhostOne_112_1_opt_sim.onnx` | 2838549 | `74d1a83932af79c2689d9371b21f32d014552f089985f076e832e89cb18f41e8` |
| `ghostone/onnx_models/PFLD_GhostNet_Slim_112_1_opt_sim.onnx` | 2970407 | `ff82819d3169b8e5b5c721fb7704a364dddcd3e9c4ae5e55ad161adcedb6265b` |
| `ghostone/onnx_models/PFLD_112_1_opt_sim.onnx` | 4890278 | `f5ae7e87fec4f47c1e0a41e4e00c81ec0c03302172b26828c42f47db032b0736` |
| `2d106det.onnx` | 5030888 | `f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf` |

逐文件固定下载URL、许可证及源码hash见 `ghostone_sources.json`。
GhostOne的[固定模型URL](https://raw.githubusercontent.com/AnthonyF333/PFLD_GhostOne/7061b5649ffdb6e7fbb4add2372346264a160343/onnx_models/PFLD_GhostOne_112_1_opt_sim.onnx)
直接提供ONNX，不需要执行PyTorch checkpoint反序列化。

【已验证：元数据】三个112模型均为ONNX IR4/opset9，单输入`input`，FLOAT32 NCHW `[1,3,112,112]`，
单输出`output` `[1,196]`，即98组XY；没有自定义domain算子。完整审计见 `model_audit.json`。

| 模型 | 实读算子数量 | initializer元素数（含常量，不等同训练参数） |
|---|---|---:|
| GhostOne | Conv51、Relu26、AveragePool4、Reshape5、Concat23、Gemm1 | 702866 |
| GhostNet Slim | Conv59、Relu26、AveragePool4、Reshape5、Concat23、Slice22、Add11、Gemm1 | 735970 |
| PFLD基线 | Conv43、Relu30、Add9、AveragePool2、Reshape3、Concat1、Gemm1 | 1218550 |

【已验证：来源树】作者该提交没有`.param`/`.bin`/`.tflite`成套文件。本轮已从该ONNX转换，
不能凭其他仓库的同名文件宣称与这组ONNX相同。可用官方NCNN的ONNX转换工具；
[NCNN当前官方指南](https://github.com/Tencent/ncnn/wiki/use-ncnn-with-pytorch-or-onnx)
更推荐pnnx，并明确传统onnx2ncnn也可能遭遇算子与shape兼容问题。
转换后的实际blob名、层类型、权重hash必须从新产物读取，不能预先假定保留ONNX命名。

本轮使用的转换路线如下；实际NCNN文件及板端下载校验值以`metrics/logs/autonomy_board_ghostone.log`为准：

```text
onnx2ncnn PFLD_GhostOne_112_1_opt_sim.onnx ghostone.param ghostone.bin
```

不得忽略不支持算子警告；先保存FP32未量化产物并对拍，再讨论packing、FP16存储或INT8。
Cortex-A9没有FP16算术能力，不能把其他ARM设备的FP16优化选项直接照搬。

## 输入、输出和同输入参考

【已验证：作者代码】`ghostone/onnx_inference.py`先使用RGB图像，将检测框居中补成正方形，
以PIL BICUBIC缩放至112，然后`ToTensor()`加`Normalize([0.5]*3,[0.5]*3)`。
因此模型输入是 **RGB/127.5−1**，不是MediaPipe的RGB/255。Tensor布局为NCHW。
边界补黑及crop偏移必须与同一版本前处理一起对拍；不能只匹配归一化就认完整输入一致。
输出按98组XY读取，乘crop宽/高并加crop偏移回原图。

【已验证：本机执行】OpenCV 4.11.0 DNN加载上述固定GhostOne ONNX，并执行seed=20261030、
uniform[-1,1]的合成FP32输入，返回 `[1,196]`且全部有限。资产：

| 文件 | 格式/用途 | SHA256 |
|---|---|---|
| `ghostone_input_synthetic.f32` | little-endian FP32，150528字节，`[1,3,112,112]` | `4496218ecb68f2c690cef27a4d19a9ba5960045f93e30f353c9a48e1dea7e682` |
| `ghostone_output_opencv.f32` | little-endian FP32，784字节，`[1,196]` | `93617735512cf6fad593b8abdcb1cb5f96e1c89d9181794bd1fe9622e68e8df7` |

运行元数据见 `ghostone_synthetic_reference.json`。这是转换对拍输入，不是人脸、EAR/MAR准确性或ARM性能证据。

## 不能按文件大小决定“更轻”

【已验证：形状计算】按Conv输出元素数×每输出卷积核元素数，另加Gemm权重元素数，
得到GhostOne约74913944 MAC、Slim约81922120 MAC、PFLD基线约272791232 MAC。
本地MediaPipe468的Conv/Depthwise Conv约34979040 MAC，attention约43476672 MAC，
后者另有自定义几何运算未计入。该统计没有计入激活、访存、布局转换和线程调度，**不是实测性能**。
GhostOne虽然输入较小，其静态卷积运算量仍更高；本轮NCNN实跑也未达到单项预算。

【已验证：本地模型】`mediapipe_audit.json`再次实读两模型：

| 模型 | 字节 | SHA256 | 算子实例 | 卷积权重/偏置/PReLU斜率元素 |
|---|---:|---|---:|---:|
| `face_landmark.tflite` | 1242398 | `1055cb9d4a9ca8b8c688902a3a5194311138ba256bcc94e336d8373a5f30c814` | 210 | 604685 |
| `face_landmark_with_attention.tflite` | 2495106 | `e06a804e0144f9929eda782122916b35d60c697c3c9344013ca2bbe76a6ce2b4` | 712 | 1189277 |

两者该类权重均以FLOAT16存储，经DEQUANTIZE进入FLOAT32计算；文件较小不等于全程FP16算术。
468是192×192 RGB FLOAT32输入，输出1404坐标值和一个faceflag，无custom；attention输出七组，
三个custom算子均version2。468保留现用眼/嘴/姿态及额头点号的范围，但不保留attention眼嘴精修，
EAR/MAR分布仍需重新对拍和标定。它不能因索引兼容就被认定满足30 fps。

## 点位与测量迁移边界

98点模型训练目标是WFLW拓扑，眼轮廓含足够点数选取六点EAR，嘴轮廓可用于MAR；
这些点号与MediaPipe完全不同。**本页没有冻结任何98→MediaPipe映射或阈值**。
必须先核对作者/数据集拓扑，挑选成对眼睑点及唇缘点，再验证睁闭眼、哈欠、侧脸与遮挡。

68/98/106点二维模型没有MediaPipe额顶10、眉间151等同义点，也没有478点的虹膜Z。
额头ROI只能根据眉线及脸框另做几何构建与有效性验证，不能把眉毛/眼睛区域默认为额头。
现用`backend/face_landmark.py`的`MP_FOREHEAD`不能直接复用。rPPG额头可见性与质量门控也需重新验证。
姿态若依赖原3D语义同样要重审；二维坐标不能随意补零冒充真实3D输出。

## 其他候选与明确阻塞

**InsightFace 2d106det**来自[官方模型页](https://github.com/deepinsight/insightface/tree/master/alignment/coordinate_reg)
链接的[Google Drive原文件](https://drive.google.com/file/d/13Pz8mH-a1s7RXpq_jFUXxaqCpDUE0oSr/view)。
它是MobileNet-0.5、192输入、106点二维模型，不是五点检测器。
本机实读ONNX IR7/opset12，FLOAT32 `data` `[batch,3,192,192]`→`fc1` `[1,212]`，
Conv28、BatchNormalization28、PRelu28、Flatten2、Sub1、Mul1、Gemm1；initializer共1252566元素。
代码采用MIT，[官方模型许可说明](https://github.com/deepinsight/insightface#license)限制公开预训练模型为非商业研究。
没有当前TFLite直接加载路线；需NCNN转换或另建ONNX运行时，且192/5MB不构成比468更快的证据。
官方`landmark.py`使用1.5倍检测框crop，并按模型图是否内含归一化决定外部输入缩放，不能照抄GhostOne预处理。

**原PFLD论文作者0.25X演示**由[论文](https://arxiv.org/abs/1902.10859)指向
[作者项目页](https://sites.google.com/view/xjguo/fld)。已下载`PFLD-DEMO.zip`：11890055字节，
SHA256 `49ac8c97c27edda3ab8567cb25575e165cfa17457c8a649619d3b20b80571e39`。
归档包含Android armeabi-v7a JNI库、`tracking.bin`及`tracking.proto.bin`，没有TFLite/ONNX。
【已验证：公开符号与字符串】JNI库含`ncnn::Mat`、`MTCNN::detect`符号，以及
`ParamDict load_param failed`、`param is too old, please regenerate`等NCNN加载错误字符串。
因此它包含NCNN相关实现，**不能说它与NCNN无关**。

然而`tracking.proto.bin`实际是17427字节文本，开头hex `69 66 79 66`、首行
`ifyf Htsa1 0=8 1=3 2=1 3=2 4=-233 5=0 6=216`，没有标准NCNN `7767517`标识及层/blob计数。
同包检测模型`det1.param`、`det2.param`、`det3.param`均有该标准文本头，可直接对照。
没有发现可证明关键点模型采用标准`ncnn::Net::load_param_bin`的公开符号；由于库已strip，
不能把“未找到符号”当成“确定没有实现”。【不确定】它可能采用历史或定制的关键点图加载方式；
本轮未逆向还原编码、未绕过许可证、未尝试将其改写成标准图。

文件配对与hash：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| `pfld_author/tracking.bin` | 2235896 | `c42cbd57b5a4d43a4f7b80a02103a8ade9df62fe1e8de9c3806c6ef3688f0462` |
| `pfld_author/tracking.proto.bin` | 17427 | `e271c5d1768d096b15fa2ef9dc2120918bdd412d0a5cdbc2a76670511192e24f` |
| `pfld_author/libTracking-lib.so` | 3309620 | `c67e3641d538ffc2f14728eb549a3728b51efa9a54bdb3eb01e98100fb55a3b2` |

Java的`Face.java`按`106*2`分配landmarks，图末参数出现`0=212`，支持“演示按106点工作”的判断，
但尚未实际加载核对输出shape。输入尺寸与归一化没有公开Java定义，不能据论文示意图填值。
JNI库公开`FaceTracking::doingLandmark`等接口，Android依赖含`libjnigraphics.so`、`liblog.so`、`libc.so`，
Linux Buildroot不能直接使用这一Bionic/JNI库。作者声明演示仅研究使用。
当前阻塞是缺少可核对的关键点加载器/标准导出及前处理契约；不是仅因后缀不叫ONNX就排除。

**PFLD_Ultralight 0.25宽度、96/112输入**来自同一派生模型作者的
[前序仓库](https://github.com/AnthonyF333/FaceLandmark_PFLD_UltraLight)，固定提交
`40fc6c3e6612f3ff18df78907d73cde8c38f8666`。其官方源码/README提供架构、训练与导出流程，
但该提交递归树与README未发现公开预训练权重/ONNX/NCNN文件；不能用随机权重证明测量可用性。
这一候选理论上更值得压缩预算，但目前缺可直接交付的公开权重，需要作者提供或重新训练。

## Peppa Slim160：更低MAC，仍未达到单项预算

【已验证：固定来源】[Peppa模型作者仓库](https://github.com/ainrichman/Peppa-Facial-Landmark-PyTorch/tree/238063317fd31c4c21c5c43692e6a5d769970370)，
提交`238063317fd31c4c21c5c43692e6a5d769970370`。这是作者使用300W-LP训练的Slim派生实现，
借鉴610265158/face_landmark与PFLD，不能称为PFLD论文作者原权重。仓库LICENSE为Apache-2.0；
该许可不替代训练数据许可审查。未下载训练集或真人素材。

资产位于本机`D:/Desktop/AMD/.tmp/autonomy/candidates/peppa/`，固定下载URL及其余源码hash见
`small_candidates_sources.json`。本轮用PowerShell `Get-FileHash -Algorithm SHA256`复核：

| 文件 | 字节 | SHA256 |
|---|---:|---|
| `slim_160_latest.onnx` | 973972 | `2121aacea9169ac27227508238237376fe64b96e2f2cc57082f26e88d650fbd2` |
| `onnx_detector.py` | 2007 | `58101f430e74fde13a120b81e8a84d357d0f5f806eeeb9995f582b23fe8b607a` |
| `LICENSE` | 11357 | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

[固定ONNX文件](https://raw.githubusercontent.com/ainrichman/Peppa-Facial-Landmark-PyTorch/238063317fd31c4c21c5c43692e6a5d769970370/pretrained_weights/slim_160_latest.onnx)
为IR4/opset9，实际数据输入`input1` FLOAT32 NCHW `[1,3,160,160]`，输出`output1` `[1,143]`。
旧导出将initializer也列入graph.input；它们不是额外运行时图像输入。
算子为Conv25、BatchNormalization25、Relu25、ReduceMean6、Concat1、Gemm1，无自定义算子；
initializer共238984元素。此前按OpenCV实际推导的每层输出形状与分组卷积核计算，Conv+Gemm为24694464 MAC；
不计激活、归一化、归约及访存，不能拿这个数替代板端时延。作者README的26.5M FLOPs是作者口径，不能与MAC直接等同。

【已验证：作者ONNX前后处理】`onnx_detector.py`将检测框宽、高各扩为1.4倍（每侧0.2），
以框中心裁剪并夹到图像边界，OpenCV默认双线性resize到160×160；函数不交换输入通道，
应保留其OpenCV BGR调用约定。归一化为`(pixel-127.0)/127.0`，转NCHW FLOAT32。
前136个输出按68组XY读取，X乘实际裁剪宽再加左偏移，Y乘实际裁剪高再加上偏移。
**其余7个输出的确切语义尚未核实，不能命名为已验证的姿态或置信度**。
同仓库PyTorch `detector.py`使用1.5倍框，与ONNX示例不同；本轮固定采用ONNX示例契约，不能混用。
作者示例还接LK光流平滑及头姿估计，当前张量探针未包含这些步骤。
68点拓扑含眼睑与唇缘多点，可进一步核对EAR/MAR映射；没有MediaPipe额顶同义点，额头ROI、姿态和阈值均须重新验证。

【已验证：合成参考】seed=20261030、uniform[-1,1]，OpenCV 4.11.0输出143个有限FP32值。
`peppa_input_synthetic.f32`为307200字节，SHA256 `1eebed2a407647faa48624fb9b0756b6afd1867bacbf9499643dc763fa7a47dd`；
`peppa_output_opencv.f32`为572字节，SHA256 `6ab5363ba956f9e0d39f4ae3314ca18f25e4547ac7aa9dd3fe6ce9a1571a1295`。
元数据见`peppa_synthetic_reference.json`。这是合成张量参考，不证明真人裁剪、点位或准确性。

## 本轮真实板端证据与30 fps边界

【已验证：读取实际日志】以下为Mizar-Z7020 ARMv7、NCNN FP32、packing开启、每次新建Extractor、
预热1次后60次运行；耗时只覆盖模型推理，不含检测、裁剪、关键点解码、EAR/MAR、PL或发布：

| 模型 | 单线程均值ms | 双线程均值ms | 双线程p95 ms | 原始日志 |
|---|---:|---:|---:|---|
| GhostOne112 | 194.61428723 | 114.70774815 | 114.964333 | `metrics/logs/autonomy_board_ghostone.log` |
| Peppa160 | 65.14181065 | 36.29815087 | 36.464263 | `metrics/logs/autonomy_board_peppa.log` |

GhostOne的196元素NCNN板端输出与ONNXRuntime同输入参考最大绝对差为`8.642673492431641e-7`，
见`ghostone_ncnn_board_comparison.json`；这验证本次合成输入的转换一致性，不是人脸准确率。
Peppa的NCNN blob为`input1`/`output1`，输出143元素；板端param SHA256为
`6735001ab4f53ff72fd315522e491f9a598701a63fd518a559b4a318f378f523`，weights SHA256为
`2c336fa2f1dc2b4ae339ddbba139408f2c590eabe64f8c580ccfa9946c5158c3`，均有下载OK行。

`metrics/logs/autonomy_board_peppa_sweep.log`又测试了default、winograd_only、sgemm_only、direct
分别搭配packing开/关，共8组、双线程各30次。最快为winograd_only+packing，均值36.15213793 ms、p95 36.2064 ms，
仍未满足`1000/30=33.333… ms`单项必要预算。板端探针对file输入标记`input_provenance_verified=false`、
`landmark_pipeline_verified=false`；文件hash与合成参考可关联，但该执行没有验证图像前处理。

**30 fps目标保持不变。本轮已测候选均未达到该单项必要预算，更不能声明完整pipeline达到30 fps。**
尚未完成真人点位验证、EAR/MAR与额头ROI标定、疲劳趋势准确性或独立运行闭环验收。

## PFLD106-lite新线索：文件小，计算量不低

【已验证：工具库维护者的一手资产】[xlite-dev/lite.ai.toolkit模型对应表](https://github.com/xlite-dev/lite.ai.toolkit/blob/22ebd42daaafd627affddf7593474a22e6055f52/docs/hub/lite.ai.toolkit.hub.onnx.md)
将`pfld-106-lite.onnx`、`pfld-106-v2.onnx`、`pfld-106-v3.onnx`分别列为约1、5、5.5 MB，
它们与同表的PFLD68/PFLD98不同。库将106模型归因于`Hsintao/pfld_106_face_landmarks`，
该原作者仓库本轮访问返回404，**未能核实其训练/导出脚本与权重许可**，不能称其为原论文作者权重。
工具库当前为GPL-3.0；这不能自动证明上游预训练权重可按同一许可再发布。

固定工具库提交为`22ebd42daaafd627affddf7593474a22e6055f52`，可取得的
[固定模型URL](https://raw.githubusercontent.com/xlite-dev/lite.ai.toolkit/22ebd42daaafd627affddf7593474a22e6055f52/examples/hub/onnx/cv/pfld-106-lite.onnx)
已通过Python urllib在内存读取：1063021字节，SHA256
`ce073227c4afee80e46abd98c8b99f5a55c3ad47f0f9cb2d2d333aeb510288f4`。
本轮未保存新的模型或源码文件；已有本地`2d106det.onnx`是上文InsightFace的另一资产，不能混为同模型。

【已验证：模型图】IR4/opset9，标准域Conv30、Relu27、GlobalAveragePool3、Reshape3、Concat1、Gemm1，
initializer共263338元素。输入`input` FLOAT32 NCHW `[1,3,112,112]`；输出共两组：
`output1` `[1,64,28,28]`为特征图，**关键点必须读取`output` `[1,212]`（106组XY）**。
[官方ORT wrapper](https://github.com/xlite-dev/lite.ai.toolkit/blob/22ebd42daaafd627affddf7593474a22e6055f52/lite/ort/cv/pfld.cpp)
取第二输出；[NCNN wrapper](https://github.com/xlite-dev/lite.ai.toolkit/blob/22ebd42daaafd627affddf7593474a22e6055f52/lite/ncnn/cv/ncnn_pfld.cpp)
使用`input`/`output`，OpenCV resize到112、保留BGR、逐通道除255，未内置额外检测框扩边。
它将XY截到[0,1]后乘传入裁剪图宽/高；回原图的crop偏移需调用者处理。
不能照抄Peppa的减127归一化，也不能把特征图误当106点输出。

【已验证：内存形状与合成执行】OpenCV 4.11.0推导出全部30层Conv输出形状，按分组核计算并加Gemm，
总计77355904 MAC（未缺层）。seed=20261030、uniform[0,1]的合成NCHW112输入，两个输出shape与图声明一致且全为有限值。
这组MAC高于GhostOne的74913944，更高于Peppa的24694464；**1 MB不意味着更低延迟，不作为本轮更低MAC首选**。
本轮没有NCNN转换对拍、ARM时延或真人点位结论。即使继续预算探针，也必须先保留实际转换文件hash，
并补齐上游权重许可、106点拓扑与EAR/MAR/额头ROI的核验。

## OpenSeeFace最小文件：保留资产，尚无ARM结论

【已验证：作者资产】[OpenSeeFace固定提交](https://github.com/emilianavt/OpenSeeFace/tree/50c09741897010faad721a218b2bbe14ecad6251)
的`models/lm_model0_opt.onnx`已保存在`candidates/openseeface/`，1902521字节，SHA256
`43c9c58f03701cc15b8423c49b78af348a6397334d348887e0fda59013275c29`。
作者明确代码与模型采用BSD-2-Clause；训练含LS3D-W、改标WFLW及不公开的自有数据，不能宣称可完全复训复现。
它是该模型树实际最小文件，**没有据文件大小证明其最低MAC或满足预算**。

输入`input` `[batch,3,224,224]`，输出`output` `[batch,198,28,28]`，每张155232元素，
不是直接66组XY：66个热图通道、66个X偏移通道及66个Y偏移通道。
作者`tracker.py`对每点热图argmax，取同位置的两个offset，以clip后的logit/16转偏移，再映射回crop；
结果的前两列按作者实现为行/列顺序，不能未经核对就当普通XY。输入BGR转RGB，resize224，
按ImageNet mean `[0.485,0.456,0.406]`、std `[0.229,0.224,0.225]`归一化。

IR4、标准opset11的图还实际使用20个`com.microsoft::FusedConv`。
本轮没有将其展开为标准Conv及对应激活，没有转换成功或ARM执行证据；后续转换必须读取每个节点的官方attributes，
不能假定全部同一种激活。NCNN张量探针还需支持上述热图输出及解码，不能将现有向量探针直接称为完整支持。
