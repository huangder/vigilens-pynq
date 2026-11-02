# 2026-10-31 rPPG C 移植第一阶段（Q1.15 + POS + FIR + 频谱）

对应 [docs/54](../docs/54_板端自治交接_handoff_20261031.md) §5 的 P2「最新 POS/rPPG、FIR、频谱计算移植」。
本轮完成**帧号时间基准**下的确定性核心；真实时间戳的等间隔化/恢复策略是下一阶段，**未伪装成已完成**。

## 范围

新增 [`board/autonomy/vital.c`](../board/autonomy/vital.c) / [`vital.h`](../board/autonomy/vital.h)：

| 段 | 与 Python 参考的关系 |
|---|---|
| ROI 均值 → Q1.15 | 与 `rppg_color.pos_q15` 同式：`128*(1+pulse)`，半远离零 |
| POS 投影 | 与 `pos_waveform` 同语义：完整局部窗、总体标准差、逐样本 support |
| FIR | 冻结 `FIR_COEFF_Q15`，int64 累加、一次算术右移、最后饱和 |
| 频谱 | 只在 HR 带内做直接 DFT（`k*fs/N`），与 numpy `rfft` 同一批频点 |
| 判据 | 峰邻域能量占比 > 0.5；半频局部峰能量比 ≥ `rppg_harmonic_ratio_min` 时拒报 |
| 真实时间戳 | 线性等间隔化到冻结采样率后再做 POS；`fs` 偏差、`p95` 抖动、最大间隔三条护栏 |
| 帧号路径 | 保留 Python 的 `nonuniform_frame_ids` 拒绝 |

**软恢复**已按 `GreenRppg.invalidate` 移植：短片暂停保留窗口并阻塞 `recovery_samples` 次估计，
长缺口或超时清窗；对应单测见下。

## 与 Python 的同输入对拍【已验证】

合成 RGB：30 fps、基线 `(122,118,112)`、1.15 Hz 色度脉冲，1200 帧；
每 30 帧在 960–1170 之间取一个估计点，共 8 个：

| 项 | 结果 |
|---|---|
| 判定一致 | 8 / 8（全部 accepted） |
| 最大 \|ΔHR\| | **0.0 bpm** |
| 最大 \|Δconf\| | **0.0** |
| 首/末点 | 68.0 bpm（conf 0.9797）/ 70.0 bpm（conf 0.9797） |

68→70 的变化来自 900 点、30 Hz 下的频率分辨率（1 个 bin = 2 bpm），两侧一致。

带真实时间戳（±2 ms 确定性抖动、30 fps）再跑同一组检查点：

| 项 | 结果 |
|---|---|
| 判定一致 | 8 / 8 |
| 最大 \|ΔHR\| / \|Δconf\| | **0.0 / 0.0** |
| 额外护栏 | 0.53 s 缺口 → `sampling_gap`；帧号跳变 → `nonuniform_frame_ids` |

机器可读：帧号 [parity](2026-10-31_vital_c_parity.json)、
时间戳 [parity](2026-10-31_vital_c_parity_timestamps.json)。

### 软暂停/恢复对拍【已验证】

同一段 1000 帧合成序列，在末尾 `invalidate("adjust_posture", ts, allow_recovery=1)`：

| 步骤 | Python | C |
|---|---|---|
| 暂停后立即估计 | `hr=null`、`last_reject="adjust_posture"` | `reject="adjust_posture"` |
| 暂停后第 1/2 次 push | `"recovering"` | `"recovering"` |
| 第 3 次 push（`recovery_samples=3`） | 恢复正常判定 | 与 Python 判定一致 |
| 0.5 s 长缺口 | 清窗后 `"adjust_posture"` | 同 |

`python -m pytest board/autonomy/tests/test_vital_c.py -q` → **8 passed**。

```powershell
. .\env.ps1
python -m pytest board/autonomy/tests/test_vital_c.py -q   # 5 passed
```

单测覆盖：干净脉冲对拍、`window_not_full`、带时间戳显式拒绝、非法 ROI 求和、恒定输入。
C 库用本地编译器现场构建（`gcc -std=c11 -Wall -Wextra -Werror`），通过 ctypes 驱动。

## 边界

- 合成信号只证明**移植一致性**，不证明真人心率准确率；真实素材仍受采样抖动与门控限制。
- 这是 PS 侧 C 代码，**没有上板**；耗时不能由本机 x86 推断。
- `rr_per_min` / `rr_conf` 仍为 null（呼吸带系数尚未冻结，与 Python 一致）。
- 未修改 `docs/interface.md`、`config.yaml` 或任何阈值；FIR 系数仍只来自 `fpga/src/fir_coeffs_q15.h`。
