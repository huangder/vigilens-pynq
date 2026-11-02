# 2026-10-30 板端计算组件与关键点预算证据

本轮在 **Mizar-Z7020 / 双 Cortex-A9 / ARMv7 NEON / Buildroot Linux** 上实际执行。
用户先要求离线移植，接板后追加实测，并明确保留 **30 fps** 目标、评估轻量关键点模型。
这份文件记录技术执行结果，不是人类必须亲手填写的 `report/llm_log/` 协作记录。

**结论边界：计算核心与模型张量执行已有真板证据；完整相机→关键点→指标→网页自治尚未完成。**
量化 Peppa 已进入单模型预算，但存在点位与跨架构数值差异，不能作为模型替换、阈值标定或整机30 fps通过的结论。
用户确认暂无当前素材的人工标注；本轮没有生成或冒充人工真值。

## 1. 环境与运行范围

- COM11 / 115200，经既有 JTAG 流程载入 Linux 和 `system_wrapper_720p60.bit`；所有模型/程序只落板端 RAM `/tmp`。
- 板卡直连 `169.254.73.29`，主机已有地址 `169.254.73.28`；没有改 PC 网卡、防火墙或持久板端镜像。
- 启动日志确认 USB 相机在 VGA/720p 均实采30张；实际协商为1/60，**原生30 fps相机模式未通过**。
- 基准期间没有运行完整相机/PL/关键点应用；推理输入为预载合成张量，模型载入、文件读取/写出不计入推理均值。
- NCNN 每次新建 Extractor，先预热一次；线程数是请求值，OpenMP 构建配置另存，不是逐线程调度审计。

原始启动与设备清点：[首次启动](2026-10-30_autonomy_boot.log)、[设备清点](2026-10-30_autonomy_inventory.log)、
[Peppa测试前重启](2026-10-30_autonomy_peppa_boot.log)、[对应JTAG输出](2026-10-30_autonomy_peppa_boot_xsdb.log)。
启动辅助脚本的退出码不代表原生30 fps通过；以日志内 `CAPTURE_FRAMES_OK ... requested_mode=unmet` 为准。

## 2. C 组合核心

【已验证】`behavior/quality/decision/measure` 组合核心，对900帧**合成**几何与统计量，
主机与真板输出逐字节相同。895行接受、5行按设计拒绝，含无效运动量、时间/帧号回退和非有限输入。
这证明实现对齐，不是真人疲劳准确性测试。

| 绑定项 | SHA256 |
|---|---|
| `config.yaml` | `8a480f594461276776630ae22a6acaa76ae406229c56be486300e0dd28eead39` |
| 合成回放输入 | `0334bad2f49fad65be0c9d6fda73cac96c2aa480d79394cbeb1e73421c06f3f9` |
| 主机/真板相同输出 | `f16d1d99516d1d28fecfdd4d03811ae2379dbe53ce34078cf5a31dadba26ae40` |

[回放串口日志](2026-10-30_autonomy_core_replay.log)、[检查结果](2026-10-30_autonomy_board_checked.json)。
PERCLOS 保留现有 Python 的窗内帧占比；配置从原加载器导出，没有新增阈值默认表。
`measure` 尚未接入 rPPG，四个 `vital` 槽位保持 null。

【已验证：组件离网执行】停止本任务的 PC 文件服务、板端 `eth0 down` 后，已载入的模型仍执行，
900帧核心回放仍得相同散列，随后恢复板端直连地址。
[完整离网日志](2026-10-30_autonomy_network_off.log) 含 `ETH_OPERSTATE=down` 和 `AUTONOMY_NETWORK_OFF_PASS`。
**没有验证摄像头自治或无JTAG冷启动**。

## 3. 真板推理预算

`config.yaml` 的 `fps_nominal=30` 对应33.333… ms帧周期。低于它只是单项必要条件，
还需要检测/跟踪、裁剪、JPEG、PL、指标与服务共同满足预算。

| 模型/运行时 | 请求线程 | 次数 | 均值 ms | p95 ms | 单项预算 |
|---|---:|---:|---:|---:|---|
| attention原始张量，TFLite未启用XNNPACK/RUY | 2 | 30 | 240.2884 | 253.0435 | 未过 |
| attention原始张量，TFLite XNNPACK/RUY | 2 | 30 | 99.7528 | 99.9889 | 未过 |
| 人脸检测128，TFLite XNNPACK/RUY | 2 | 30 | 50.9524 | 51.1700 | 未过 |
| 468点，TFLite XNNPACK/RUY | 2 | 60 | 74.7118 | 74.9784 | 未过 |
| GhostOne98，NCNN FP32 | 2 | 60 | 114.7077 | 114.9643 | 未过 |
| Peppa68，NCNN FP32 | 2 | 60 | 36.2982 | 36.4643 | 未过 |
| Peppa，NCNN FP32精确零通道压缩 | 2 | 300 | 33.5145 | 33.7116 | 未过 |
| Peppa，KL INT8（26层量化） | 2 | 60 | 22.9387 | 23.0369 | 仅单项进入 |
| Peppa，KL混合（24层量化，首末层FP32） | 2 | 300 | 24.6001 | 24.7075 | 仅单项进入 |

对应原始日志：[TFLite基线](2026-10-30_autonomy_model_baseline.log)、[优化版](2026-10-30_autonomy_model_xnn.log)、
[468点](2026-10-30_autonomy_mesh468.log)、[GhostOne](2026-10-30_autonomy_ghostone.log)、
[Peppa FP32](2026-10-30_autonomy_peppa_fp32.log)、[Peppa INT8](2026-10-30_autonomy_peppa_int8.log)、
[正确混合精度图](2026-10-30_autonomy_peppa_mixed.log)。

Peppa FP32 还比较8组 Winograd/SGEMM 允许策略与 packing 开关，最快均值36.1521 ms，仍未进入预算；
[全部策略日志](2026-10-30_autonomy_peppa_kernel_sweep.log)。这些开关不等于逐层实际内核使用审计。

attention模型包含三个version-2自定义算子，探针编译官方MediaPipe CPU实现。
其六组坐标与faceflag尚未细化/投影成完整478点结果，不能把raw mesh张量当最终关键点管线。
XNNPACK探针在创建Interpreter前设置线程，记录实际delegate节点数；优化attention有3个节点，检测/468各1个。

## 4. 数值对拍与模型迁移风险

【已验证：固定合成输入】GhostOne的ONNX Runtime→NCNN真板196个标量最大绝对差
`8.642673492431641e-7`；Peppa143个标量最大差 `8.106231689453125e-6`，前136坐标最大差
`1.1026859283447266e-6`。这里只记录差异，没有设立新的神经网络容差或准确性标准。
[GhostOne报告](2026-10-30_autonomy_ghostone_comparison.json)、[Peppa FP32报告](2026-10-30_autonomy_peppa_fp32_comparison.json)。

【已验证：量化跨架构】同一合成文件的主机NCNN与A9输出，INT8前136坐标最大绝对差
`0.023729771375656128`，正确混合图为 `0.014510571956634521`。
这比FP32对拍差异大，**量化跨架构一致性尚未闭合**。
[板端量化检查](2026-10-30_autonomy_peppa_quantized_board.json)。

【已验证：现有468/478差异】同一真实视频300帧，两模式共同检脸273帧，闭眼分类差4帧，
决策状态差223帧，yaw绝对差p95约73.77°。两侧均通过现有PnP护栏，也不能判定任一侧姿态正确。
不能仅因索引在468范围内就沿用旧几何/阈值。
[数值摘要](2026-10-30_autonomy_landmark_modes.json)、[方法与限定条件](../../board/autonomy/LANDMARK_MODES.md)。

## 5. 本地视频量化校准与开发验证

素材 `blink.mp4` SHA256：`5921c741864f3a1daeee0eb619e285363c544783e92970fe4e64e3d3eabcfab1`。
实际解码600帧，每3帧选1帧；真实MediaPipe bbox无脸即跳过。
索引<300得到90张量化校准样本，索引300–599得到99张开发验证样本，11张无脸跳过。
按固定作者ONNX路径：1.4倍裁剪、BGR、160×160双线性、`(pixel-127)/127`、NCHW。
作者PyTorch版本采用1.5倍，不能混用。最后7个输出语义未核实，只比较前136个坐标。

| 方案（主机同输入比较） | 点位数 | 位移均值 px | p95 px | 最大 px |
|---|---:|---:|---:|---:|
| KL全INT8 vs FP32 | 6732 | 5.0847 | 9.5999 | 21.7615 |
| KL正确混合图 vs FP32 | 6732 | 4.9163 | 8.7846 | 20.7755 |
| ACIQ全INT8 vs FP32 | 6732 | 7.3729 | 13.7299 | 24.2586 |

坐标按每帧实际clamped crop宽高缩回原图像素，再计算68点欧氏距离。
[KL报告](2026-10-30_autonomy_peppa_int8_development.json)、[混合报告](2026-10-30_autonomy_peppa_mixed_development.json)、
[ACIQ报告](2026-10-30_autonomy_peppa_aciq_development.json)。这些数值来自主机，不是板端真人误差。
FP32是数值参考，**不是人工真值**；此集已参与方案比较，不能再称独立测试集。
同视频时间分割不代表跨人群、姿态、光照的泛化验证，更不能代替EAR/MAR/PnP/额头ROI标定。
原始人脸裁剪与张量留在忽略目录，未放入本证据或对外上传。

## 6. 工具防误判与复现

本轮发现并保留了两处固定版本工具风险：

1. `ncnn2table` 每个浮点文本只读19字符。过长的 `1/127` 小数令norm只解析1通道，
   产生异常激活；该产物未上板使用。改为相同float32的短表示 `0.007874016`，日志确认三通道。
2. `ncnn2int8` 缺权重scale会中止该类层量化，但main仍可能exit0写图。
   只注释权重行的首次混合图实际上仅量化深度卷积，不是预期方案；其39.02 ms结果不冒称正确混合图。
   正确方法同时移除该层激活与权重表行，并检查生成param中实际量化层集合。

这些校验已写入 [`quantize_peppa.py`](../../board/autonomy/quantize_peppa.py)；
真实复跑得到26/24层，权重散列与板测图一致。
[全INT8工作流](2026-10-30_autonomy_peppa_int8_workflow.json)、[混合工作流](2026-10-30_autonomy_peppa_mixed_workflow.json)。
上游表的无穷权重scale已计数保存，未擅自替换；不能将其存在或模型能执行当作准确性通过。
完整构建/校准/复现命令见 [模块README](../../board/autonomy/README.md)，模型来源/授权/点位边界见
[候选审计](../../board/autonomy/lightweight_candidates.md)。

## 7. 离线回归与未完成项

【已验证】项目总检查 PASS26 / FAIL0，用时71.6s；新增主机工具72项通过（2条protobuf弃用警告），
WSL C核心/NCNN探针50项通过。
[总检查](2026-10-30_autonomy_check_all.log)、[工具测试](2026-10-30_autonomy_tool_tests.log)、[C/CLI测试](2026-10-30_autonomy_c_tests.log)。
后续新增试验以相应补充日志为准。

仍未完成：完整检测/跟踪/裁剪与关键点后处理、68点语义与人工阈值标定、PnP与额头ROI验证、
最新rPPG/POS/FIR/频谱移植、板端HTTP/WS、相机与推理并发的整机30fps、最终镜像独立冷启动。
没有修改 `config.yaml`、契约字段或PL位流，也没有切换原PC业务入口。

来源/产物/执行日志散列见 [归档清单](2026-10-30_autonomy_manifest.json)。

## 8. FP32精确零通道压缩

【已验证】以已融合的NCNN FP32图为参考，完整filter为精确0、bias<=0且确认fused ReLU时，
才允许删除恒零输出；深度卷积仅跟随已证零输入，正偏置依赖反向保护；空间mean/Concat/FC保留原通道身份。
保留部分零通道令通道数向上对齐4，支持pack4；不是按epsilon剪去小权重，也没有量化或训练。
源ONNX中存在subnormal，不能把对融合图的证明扩成原ONNX逐位等价。

实际权重928676→475524字节；末层256→88，FC输入448→236，输出仍143。
[结构审计](2026-10-30_autonomy_peppa_pruning_audit.json)、[工具与比较测试](2026-10-30_autonomy_pruning_tests.log)。
同视频99帧、6732点相对原NCNN FP32位移p95为 `0.00003207815440339075` px，
最大 `0.00006384856670623522` px；[开发集对拍](2026-10-30_autonomy_peppa_pruned_development.json)。
同合成输入的原板端/压缩板端前136坐标最大差为 `5.960464477539063e-8`；
[板端数值对拍](2026-10-30_autonomy_peppa_pruned_board.json)。

板上300次、双线程均值33.5145 ms、p95 33.7116 ms，**仍超过33.333… ms，不能四舍五入称30fps通过**。
[原始计时](2026-10-30_autonomy_peppa_pruned.log)。这说明可以保持接近FP32参考的输出，
但本轮该NCNN配置仍未留出整机预算，后续继续评估同权重FP32运行时。
