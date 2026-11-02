# 2026-10-31 板端 68 点推理接入相机管线：占空比实测

目标：把 68 点推理真正放进 `camera_pl`（不是旁路进程），测「30 fps 采集 + 周期推理」能不能同时成立。

## 实现

- `board/autonomy/landmark_runner.{h,cpp}`：中心裁剪 → 160×160 BGR → MNN Peppa（2 线程），C API。
- `camera_pl.c`（`VIGI_LANDMARK` 编译开关）：
  - `VIGI_LANDMARK_EVERY=N`：每 N 个交付帧提交一次推理；
  - 推理在**独立线程 + 双缓冲**里跑，`process_rgb` 只做一次 memcpy，不阻塞 PL。

## 真板结果

三种配置（1280×720，PL 全开，JPEG 透传关闭）：

| 分析 fps | 推理节奏 | 交付 fps | 推理耗时（mean 范围） |
|---:|---:|---:|---:|
| 30 | 每 6 帧（5 Hz，同步版） | **25.24** | 61–94 ms |
| 30 | 每 6 帧（5 Hz，异步版） | **22.68** | 180–290 ms |
| 15 | 每 2 帧（7.5 Hz，异步版） | **14.95** | 113–199 ms |
| 30 | 无推理 | **29.994** | — |

## 结论（关键）

- **30 fps 的 720p 解码 + PL 已经吃满 2 个 A9 核**；再加任何推理线程都会掉帧。
- 异步线程没有帮助：它只是把抢占从"阻塞"变成"争核"，交付 fps 反而更低（22.68）。
- 推理在负载下从 27 ms 退化到 110–290 ms（被抢占），与并发实验一致。
- **15 fps 分析**可以保住 15 fps 交付，但 R1（≥28.5 fps）不成立。

## 必须做的取舍（需要拍板）

1. **30 fps 采集 + PC 侧 68/指标**（现有混合链路，已验证）——保住实时性指标；
2. **15 fps 分析 + 板端 5–7.5 Hz 68 点**——板端自治但实时门限不过；
3. 30 fps + 板端 68 点（1 Hz）——采样率不足以测眨眼，不建议。

在 2×A9 上"30 fps + 板端逐帧/5 Hz 关键点"没有可行解，除非把解码/关键点搬到 PL 或换更强 SoC。

## 复现

```sh
export VIGI_PIPELINE=threaded VIGI_DECODE_WORKERS=2 VIGI_PL_WAIT=yield VIGI_AUDIT_EVERY=30
export VIGI_LANDMARK_EVERY=6
/tmp/vigi_camera_pl_lm /dev/video0 1280 720 200 30 16
```

日志：`metrics/logs/board_lm_cam.log`（同步）、`board_lm_cam2.log`（异步 30 fps）、
`board_lm15.log`（15 fps）。
