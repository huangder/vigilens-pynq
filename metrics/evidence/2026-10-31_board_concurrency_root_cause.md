# 2026-10-31 板端并发退化：复现与根因（Mizar-Z7020）

handoff 的 88 ms / 21.65 fps 已在本机复现，并定位到 CPU 资源竞争。所有数字来自本次启动的串口日志。

## 复现结果

前提：使用**重新交叉编译**的 `vigi_camera_pl`（板内旧二进制不支持 `VIGI_PIPELINE`/`VIGI_AUDIT_EVERY`，
只能用 serial + 每帧 reference，实测只有 **3.5 fps**，不是本轮结论）。

| 运行 | 输入 fps | 交付 fps | MNN mean / p95 | 说明 |
|---|---:|---:|---:|---|
| 相机+PL+JPEG 发送（无推理） | 59.31 | **29.994** | — | 控制组，达标 30 |
| + MNN 300×2 线程 | 29.60 | **21.596** | 83.30 / 289.79 ms | 复现 handoff（21.65 / 88.46） |
| + MNN 300×1 线程 | 29.09 | **21.614** | 71.54 / 80.46 ms | fps 不变，长尾消失 |
| MNN 300×2 + 解码 1 worker | 18.28 | **17.910** | 109.13 / 198.42 ms | 更差 |

各次运行 `AUDIT_SUMMARY failures=0`，帧摘要校验无误；`native_30fps=no` 仍然成立。

## 根因判断

1. **不是相机**：无推理时 29.994 fps，采集与 JPEG 发送本身达标。
2. **不是 MNN 单模型**：单独 27.27 ms；并发 p50 仍是 27.8 ms，p95 冲到 289.8 ms —— 
   说明推理被**抢占/排队**，不是变慢。
3. **是两核被占满**：相机管线（2 解码 worker + PL + JPEG 编码/发送 + 主线程）在 30 fps 时
   基本吃满 2 个 A9 核；再叠加 2 线程推理，调度器只能在两者之间切。
   - 推理改 1 线程：长尾消失，但管线 still 21.6 fps（少一个核也没还给相机）；
   - 解码改 1 worker：管线更慢（17.9 fps），说明瓶颈不在解码线程数。
4. 因此**靠调线程数解决不了**；要做的是给推理**降占空比**（例如 5–10 Hz，检测/关键点之间保持框），
   让 30 fps 采集独占管线。

## 顺带修正的两个问题

- 板内 `/usr/bin/vigi_camera_pl` 是旧构建：没有 `VIGI_PIPELINE`/`VIGI_AUDIT_EVERY`，
  永远 serial + 每帧 reference → 3.5 fps。**必须部署新构建**。
- `uvc_probe` 的 PL 构建要求 6 个参数（device w h frames analysis-fps motion-threshold），
  少一个就 `EINVAL`；原 usage 只写了 4–5 个参数，已修正提示文案。

## 复现命令（板端）

```sh
export VIGI_STREAM_CODEC=jpeg VIGI_JPEG_FAST=1
export VIGI_PIPELINE=threaded VIGI_DECODE_WORKERS=2 VIGI_PL_WAIT=yield VIGI_AUDIT_EVERY=30
export VIGI_STREAM_BIND=169.254.73.29:9007
/tmp/vigi_camera_pl_new /dev/video0 1280 720 600 30 16
# 另开：/tmp/mnn/vg_mnn_probe /tmp/mnn/peppa_pruned.mnn /tmp/mnn/peppa_input.f32 /tmp/mnn/x 300 2
```

原始日志：`metrics/logs/board_newcam_conc.log`、`board_stream_only.log`、`board_variant_t1.log`、
`board_variant_d1.log`；PC 接收统计：`metrics/logs/board_*_20261031.json`。

## 结论边界

- 只测了「相机+PL+JPEG 发送 + 连续推理」；没有检测/裁剪/PnP，也没有长跑。
- 「降占空比即可达标」是【推测】，需在整合程序中实测；本轮未实现。
