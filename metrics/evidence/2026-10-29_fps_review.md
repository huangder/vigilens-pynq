# 帧率改造复核与真实上板（2026-10-29）

本文件是技术证据，非 `report/llm_log/` 人类亲笔协作记录。

【已验证】Mizar-Z7020，1280×720 MJPG，相机仍协商 60 fps；按真实时间槽限制到 30 fps。
GEM0 100Mbps/Full，已有 720p PL 位流，RAM/JTAG 启动后经校验 SHA256 更新 RAM 中的程序。
没有写 SD/QSPI，没有保存/上传相机原始图像。本轮未检出人脸，只证明数据链路与算法接线。

## 实际结果与边界

| 实跑 | 相机缓冲数 | 交付帧数 | 审计帧 | 采样 fps | 间隔 p95 | PC 全程吞吐 |
|---|---:|---:|---:|---:|---:|---:|
| JPEG 短跑，审计间隔 5 | 60 | 31 | 7 | 29.64 | 36.06 ms | 见原始 summary |
| JPEG 长跑，审计间隔 30 | 300 | 255 | 9 | 22.86 | 101.45 ms | 20.6 fps |
| JPEG 长跑，TCP_NODELAY + PC 求和优化 | 300 | 236 | 8 | **25.29** | **68.03 ms** | **22.5 fps** |
| raw 串行逐帧严格回归 | 12 | 9 | 9 | 4.24 | 314.39 ms | 见原始 summary |

- 四次成功运行均完整接收 VLE1，板端与 PC 帧数一致，审计帧零差异；raw 回归每帧严格零差异。
- 最终 JPEG 长跑完成 MediaPipe → 指标 API **11 次**、同源预览 **90 次**投递。
- **采样 fps 按 V4L2 时间戳计算**；PC 吞吐包含连接、处理、收尾的墙钟耗时，二者不能混写。
- **最终实时帧率及抖动门限未过**。短跑越过帧率门限不能替代长跑结论。
- 原始 60 fps 相机有时间槽丢弃与驱动序号缺口，绝非无丢帧 30 fps。
- JPEG 审计是板端 CPU/PL 比对；PC 通道和校验有容差，不能表述成 PC/PL 像素逐字节相同。
- 没有疲劳准确性、原生 30 fps、独立启动或长期稳定性验收结论。
- 此前 raw 296 帧的历史结果为 3.91 fps；素材/光照并非严格同一输入，不将比较表当作受控加速比。

汇总：[真实结果 JSON](2026-10-29_fps_review_results.json)。
最终原始证据：[板端日志](2026-10-29_fps_long_final.log)、
[跨端校验](2026-10-29_fps_long_final_network.json)、
[PC summary](2026-10-29_fps_long_final_pipeline.json)、
[启动器 summary](2026-10-29_fps_long_final_runner.json)。
对照证据：[优化前长跑](2026-10-29_fps_long_before.log)、
[短跑](2026-10-29_fps_short_jpeg.log)、[raw 回归](2026-10-29_fps_raw_regression.log)。

## 实测暴露与修复

1. **队列结束丢尾帧**：原代码统一设置 stopping，decode 尚未排空时 PL 就退出。
   改为两级队列独立结束，失败不发成功包；补部分线程启动失败与遗留任务清理。
2. **参数失效**：C 端原来把 `serial` 也视为启用线程；PowerShell `$pipeline` 又与
   `[string]$Pipeline` 参数重名。现支持明确枚举并改用 `$pipelineProcess`。
3. **DMA 完成**：增加非审计帧的 DDR 末字节与错误位检查。
   本轮曾额外等待 HALTED，但真实 TLAST-less 位流不会给出该标志，实测超时后已移除。
   [失败原始日志](2026-10-29_fps_failed_halted.log)保留，不计入成功结果。
4. **JPEG 解码不一致**：原 PC 默认 ISLOW/fancy 与板端 IFAST/no-fancy 的通道和不符。
   [失败板端日志](2026-10-29_fps_failed_jpeg_decode.log)、
   [PC 拒收输出](2026-10-29_fps_failed_jpeg_receiver.log)保留。
   29 帧数值诊断中，匹配 IFAST 参数后，三通道和逐帧全部等于 PL；未放宽 0.5% 校验阈值。
   [诊断 JSON](2026-10-29_fps_jpeg_diagnostics.json)、[严格对拍日志](2026-10-29_fps_jpeg_diagnostics_board.log)。
   包中增加私有 `jpeg_decoder` 标签，PC 用 Pillow 12.3.0 匹配快速模式。
5. **传输与接收开销**：启用 TCP_NODELAY，PC 三通道求和改三个 uint32 归约；
   全白最大值和随机图测试验证结果不变。联合修改改善了本轮长跑，未单独归因。
6. **验收结论**：抽检分别报告 invariants/audit/every-frame，仿真工作线程失败会向调用者抛错，
   不再默默给出部分结果和 VLE1 成功包。审计时序按已处理帧计数，与板端一致。

【已验证】最终长跑解码中位 **36.648 ms**，transfer/PL 中位 **16.649 ms**。
【待验证】下一步需测解码并行与 CPU/IRQ 争用，以及 PC 逐阶段开销；尚不能承诺这些改动达到 30 fps。

## 构建与回归

- ARM 编译/链接使用现有 Buildroot 工具链，`-Wall -Wextra -Werror -pthread`；
  [最终构建输出](2026-10-29_fps_arm_build.log)。
- 实跑程序 SHA256：`af939db48df52626d1a3cbec283149c356ce954aa3f5b6174308785e43e18896`；
  [板端 RAM 安装与哈希读回](2026-10-29_fps_ram_install.log)。
- `python metrics/scripts/check_all.py` 对应最终代码：**PASS 26 / FAIL 0**，66.9 s。
  本轮由临时包装器保存了内部子进程输出，未改检查器实现。
  [完整输出](2026-10-29_fps_check_all.log)、[JSON](2026-10-29_fps_check_all.json)。
- 内含真实 `pytest`：**464 passed, 2 warnings in 25.03s**；
  [A 线完整输出](2026-10-29_fps_a_line.log)。起始基线为 437 passed。
- 生产 C 队列主机测试：ASan/UBSan 通过；排空 2/60 帧、工作线程失败、两处启动失败、参数解析。
  [真实输出](2026-10-29_fps_c_pipeline_host.log)。其中故障消息为用例主动注入，不是板端失败。

## 复跑

先按 [NETWORK_LOOP](../../board/w7_buildroot/NETWORK_LOOP.md) 用最新源码构建并加载 RAM 镜像，
再用新的 RunId 运行，避免覆盖既有证据：

```powershell
. .\env.ps1
python metrics/scripts/check_all.py
powershell -NoProfile -ExecutionPolicy Bypass -File board/w7_buildroot/run_network_demo.ps1 `
  -Frames 300 -Codec jpeg -Pipeline threaded -AuditEvery 30
```

日志在 `metrics/logs/network_demo_运行编号/`。结束后检查 `network.summary.json`，
特别是 `realtime_target_met`，不能只看 `closed_loop_pass`。
