# 端到端并行流水真实上板证据（2026-10-29）

本文件是技术证据，不是 `report/llm_log/` 人类亲笔协作记录。

【已验证】本轮选择并实现保留 720p 的端到端并行流水：双 JPEG 解码、按序进入 PL、
PL 等待让出 CPU、PC 两帧有界预取、后台单槽同源预览。
相机/PL/MediaPipe/指标 API/预览均开启，像素只在开发板和本机内存内处理。
使用 RAM/JTAG 运行并校验 SHA256 后更新 RAM 中程序，没有写 SD/QSPI。

## 最终结论

| 项 | 真实结果 | 结论 |
|---|---:|---|
| 相机缓冲数 | 1800 | 原生仍协商 60 fps，按真实时间槽选取 30 fps |
| 完整交付 | 909 帧 | PC/板端计数一致，VLE1 正常结束，source error=null |
| 采样 | 29.999264 fps | R1 帧率 ≥28.5，通过 |
| 相邻间隔 p95 | 36.85365 ms | R1 p95 ≤50 ms，通过 |
| 审计 | 31 帧，0 失败 | 板端 CPU/PL 逐字节与统计对拍通过；全部帧寄存器不变量通过 |
| 同包运动量、身份、时间戳 | 909 帧全部核对通过 | 没有用错相邻帧或重建假时间轴 |
| PC 全程耗时/吞吐 | 30.66 s / 29.7 fps | 严格 R2 ≥30，仍未通过 |
| 指标 API / 同源预览 | 31 / 305 次投递，0 错误 | 真实引擎与预览均在运行 |
| 预览后台线程 | 正常结束，worker_alive=false | 编码前节流，未阻塞测量；本次 replaced=0 |
| 原生 30 fps / R3 延迟 / 疲劳准确性 / 独立启动 | 未通过或未验收 | 不以 R1 或投递成功替代这些结论 |

全部长跑未检出人脸（`landmark_source=mediapipe`，`face_visible_frames=0`），
因此不能据此推出有人脸场景的吞吐、疲劳准确性、心率/呼吸有效性。
约 30.27 秒是本次采样时间范围，不是分钟/小时级长稳证据。

原始证据：[板端日志](2026-10-29_parallel_long_final.log)、
[原运行校验](2026-10-29_parallel_long_final_network.json)、
[最终验收器只读复核](2026-10-29_parallel_final_crosscheck.json)、
[PC summary](2026-10-29_parallel_long_final_pipeline.json)、
[启动器 summary](2026-10-29_parallel_long_final_runner.json)。
[汇总与源码 SHA256](2026-10-29_parallel_results.json)。

## 方案取舍的真实依据

| 配置 | 相机缓冲 | 采样 fps | p95 | PC 全程吞吐 | 原始证据 |
|---|---:|---:|---:|---:|---|
| 单解码 + 忙轮询 + 同步预览 | 300 | 25.94 | 67.97 ms | 22.6 fps | [日志](2026-10-29_parallel_single_spin.log) |
| 双解码 + 忙轮询 + 同步预览 | 300 | 26.33 | 67.98 ms | 22.4 fps | [日志](2026-10-29_parallel_dual_spin.log) |
| 双解码 + 休眠等待 + 同步预览 | 300 | 23.70 | 105.78 ms | 20.0 fps | [日志](2026-10-29_parallel_yield_sync.log) |
| 双解码 + 异步预览 | 300 | 29.98 | 36.67 ms | 24.9 fps | [日志](2026-10-29_parallel_dual_async.log) |
| 单解码 + 异步预览 | 300 | 27.99 | 52.08 ms | 24.7 fps | [日志](2026-10-29_parallel_single_async.log) |
| 双解码 + 异步预览，较长运行 | 1800 | 28.30 | 63.84 ms | 27.8 fps | [日志](2026-10-29_parallel_long_without_prefetch.log) |
| **再加 PC 两帧有界预取** | **1800** | **30.00** | **36.85 ms** | **29.7 fps** | [日志](2026-10-29_parallel_long_final.log) |

这些是同一开发板的实际运行，画面不是逐字节固定回放，不当作严格同输入加速比。
单纯加线程或休眠没有解决完整链路；中间未达标结果全部保留。
诊断接收器在不开算法/API 时可接收约 30 fps，说明还必须处理 PC 消费反压；
[接收统计](2026-10-29_parallel_fast_receiver.json)、[板端日志](2026-10-29_parallel_fast_receiver_board.log)。
该诊断运行不计作完整算法闭环证据。

## 正确性与资源界限

- 两个解码任务拥有各自 JPEG 对象和 RGB 缓冲，输出按输入 ticket 排序后才送入 PL。
  六个 RGB 缓冲有显式所有权，上一帧保持到当前运动量审计结束。
- PC 接收队列只允许两帧等待测量，**不丢指标帧**；协议错误仍传到 summary/退出状态。
  一次帧源只允许一次连接，不自动重连或补帧。
- 最新预览单槽只替换待发预览；图像副本和帧号/时间戳/bbox 一起入队。
  后台编码/HTTP 失败记录错误并停止预览，测量仍落盘；收尾回收线程。
- 仍用 JPEG CRC32、原有解码模式和通道和规则；没有放宽阈值，没有修改业务九字段或位流。
- JPEG 审计证明的是**板端 CPU 与 PL**。PC/PL 像素逐字节一致仍由 raw/无损路径证明，
  不能把通道和一致当作每个像素一致。
- 最终程序 raw 串行回退已实跑 **9 帧逐帧严格零差异**：
  [日志](2026-10-29_parallel_raw_regression.log)、[校验](2026-10-29_parallel_raw_regression_network.json)。
  初次双解码严格短跑也完成 16 帧逐帧零差异：[日志](2026-10-29_parallel_strict_smoke.log)。

## 构建与离线验证

- 最终 ARM ELF SHA256：`fcefa3e04b96925105aa87c7cb379a7d0e741cda02c6033964052594a31870a1`。
  现有 Buildroot ARM 工具链，`-Wall -Wextra -Werror -pthread` 编译链接通过。
  [构建日志](2026-10-29_parallel_arm_build.log)、[RAM 安装哈希读回](2026-10-29_parallel_ram_final.log)。
- 生产 C 主机测试带 ASan/UBSan：单/双解码、强制乱序完成、240 帧缓冲复用、历史保留、
  尾帧排空、四类工作线程失败、五个启动失败点及提前中止均通过。
  [原始输出](2026-10-29_parallel_c_host.log)。其中失败提示来自主动故障注入，不是硬件错误。
- 全量 `pytest`：**485 passed, 2 warnings in 26.27s**（本轮起点 464 passed）。
  新增测试覆盖后台预览的慢 HTTP/元数据一致性/编码前节流/错误收尾，以及有界接收预取、
  提前关闭、异常传播、单次连接与日志 R1/R2 分离。
  [A 线输出](2026-10-29_parallel_a_line.log)。
- `check_all`：**PASS 26 / FAIL 0**，65.0 s。
  [完整输出](2026-10-29_parallel_check_all.log)、[JSON](2026-10-29_parallel_check_all.json)。
  临时包装器仅保存内部检查输出，没有改检查逻辑。

## 复现与后续

完整构建/启动、参数与回退见 [docs/48](../../docs/48_端到端并行流水与30fps采样_20261029.md)。
板端启动后运行：

```powershell
. .\env.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_network_demo.ps1 `
  -Frames 1800 -Codec jpeg -Pipeline threaded -DecodeWorkers 2 `
  -PlWait yield -PreviewMode latest -AuditEvery 30
```

【待验证】有人脸负载、分钟级稳定性、无节流 R2 处理吞吐与 R3 展示延迟。
现有结果不替代这些验收，也不以更改门限、降低档位或补帧制造通过。
