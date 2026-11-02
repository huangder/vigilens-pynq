# 2026-10-31 MNN 板端输出离线对拍（handoff P0 第二项）

对应 [docs/54](../docs/54_板端自治交接_handoff_20261031.md) §5 的 P0「补齐 MNN 原图/压缩图的 ARM 输出对拍」。
**本次没有重新上板**：数据来自 2026-10-30 两次真板探针留下的串口转录，
本文件只把其中的 `VIGI_FILE_BEGIN ... VIGI_FILE_END` 块解出来，与同输入的主机引擎输出比较。

## 为什么之前只有 PC 对拍

串口日志里的 143 个 float 是 gzip+base64 文本块，仓库里没有工具能把它还原成张量；
`.tmp/autonomy/decode_serial.py` 是本地一次性脚本，白名单只到 NCNN 压缩阶段，
且 `.tmp/` 不入库。于是 `mnn_probe/README.md` 只能引用 host 对拍，板端数值一直悬空。

本轮新增 [compare_board_outputs.py](../board/autonomy/mnn_probe/compare_board_outputs.py)：
固定两个文件名白名单、严格 base64、1 MiB 解压上限、572 字节长度、有限值检查，
并要求日志里必须出现冻结的输入/模型/探针 SHA256，否则拒绝出报告。
它只回答「按这份日志，板端字节离各主机引擎有多远」，不认证日志来源、不重跑板卡。

## 固定输入与资产绑定

| 项 | 值 |
|---|---|
| 合成输入 `.tmp/autonomy/candidates/peppa_input_synthetic.f32` | `1eebed2a407647faa48624fb9b0756b6afd1867bacbf9499643dc763fa7a47dd` |
| 原图 MNN `peppa.mnn` | `2a22689a611d4d4b17da3f5e6824508d2e9ea0d08b197ce1fdc30836b8a683fc` |
| 压缩 MNN `peppa_pruned.mnn` | `be908e37a2dba5fb1b43fcf242f54db5290800f999ff18c2406432726f19b138` |
| ARM 探针 `vg_mnn_probe` | `3d42f95f68c13c0599e1547a919bd404a62983454ec5f4d735aa27f221790ad9` |

三种 SHA256 都出现在各自的串口转录里（脚本在缺任一项时报错），所以比较对象不是「某个同名文件」。

## 实际结果【已验证：读取日志后本机计算】

| 板端输出 | 日志 | 输出 SHA256 | 143 值全有限 |
|---|---|---|---|
| `peppa_mnn_t2.output0.f32` | `2026-10-31_autonomy_handoff_mnn_original.log` | `cde263832e4b17ea1f477df2d32b7799d1b047309a8382205a4979881a4996f7` | 是 |
| `peppa_mnn_pruned_t2.output0.f32` | `2026-10-31_autonomy_handoff_mnn_pruned.log` | `cde263832e4b17ea1f477df2d32b7799d1b047309a8382205a4979881a4996f7` | 是 |

绝对差（板端 vs 主机，同一 307200 字节输入）：

| 参考引擎 | 原图：143 值最大差 | 原图：前 136 坐标最大差 | 压缩图：143 值最大差 | 压缩图：前 136 坐标最大差 |
|---|---:|---:|---:|---:|
| MNN host FP32 | 1.3709068298339844e-06 | 3.5762786865234375e-07 | 1.430511474609375e-06 | 3.8743019104003906e-07 |
| ONNXRuntime | 1.1444091796875e-05 | 1.2516975402832031e-06 | 2.6226043701171875e-06 | 2.086162567138672e-07 |
| OpenCV DNN | 2.2649765014648438e-06 | 4.172325134277344e-07 | —（无该参考） | — |
| NCNN host（压缩图） | — | — | 1.5497207641601562e-06 | 2.086162567138672e-07 |

`docs/54` 的 P0 要求因此闭合：ARM 侧原图/压缩图都已有与 host/ORT/NCNN 绑定的数值对拍，
不再只有 PC 端数字。120 次差异全部来自浮点实现顺序，没有引入新容差，也不据此声明精度。

## 一个必须如实报告的异常

**两次板端运行的 572 字节输出逐字节相同**（同一个 SHA256），
但主机端同一对模型（MNN host 原图 vs 压缩图）最大差 `2.384185791015625e-07`、并非逐位相同。

- 【已验证】串口转录里的模型 SHA256、探针 SHA256、双线程耗时都不同
  （原图 31.2107 ms / 压缩图 27.5942 ms），所以确实是两次不同模型的运行，不是同一次日志复制。
- 【推测】`prune_peppa_fp32.py` 只删除已证明为零、且后接 ReLU 的通道，
  在 ARM 侧按通道顺序累加时被删项恰为 `+0`，因此结果可以逐位不变；
  x86 内核的分块/顺序不同，才会出现 2.4e-7 的差。
- 【不确定】上述推测未在 ARM 上验证，也无法排除「导出时两次取到同一文件」这种操作失误。
  板卡可用时应在设备上直接 `sha256sum /tmp/peppa_mnn_t2.output0.f32` 与压缩图各打印一次，
  用设备自己算的散列复现或推翻本条。

这条不影响「数值一致性」结论（两条都在 1.5e-6 内），但影响「压缩是否改变输出」的叙述，
在补到设备侧散列前不要写成「压缩已验证逐位等价」。

## 复现

```powershell
. .\env.ps1
python board/autonomy/mnn_probe/compare_board_outputs.py `
  --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_original.log `
  --log metrics/evidence/2026-10-31_autonomy_handoff_mnn_pruned.log `
  --out metrics/evidence/2026-10-31_mnn_board_output_comparison.json
python -m pytest board/autonomy/mnn_probe/tests/test_compare_board_outputs.py -q
```

本轮实际运行：报告写出成功（`ok: true`），单测 `17 passed`（2026-10-31，Windows 3.12.13）。
完整机器可读结果见 [对比 JSON](2026-10-31_mnn_board_output_comparison.json)。

## 边界

- 不是新上板运行；`board_rerun: false` 写在报告里。
- 不证明关键点准确率、真人输入、相机链路，也不证明 30 fps。
- 只验证「这两份已有日志里的 143 个 float 与主机参考的一致性」。
- 未修改 `docs/interface.md`、`config.yaml`、业务字段或任何阈值。
