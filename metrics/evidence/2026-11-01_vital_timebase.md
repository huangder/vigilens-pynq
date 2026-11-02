# 板端 rPPG 时间基准（2026-11-01）

【已验证，主机】板端原生链路的 rPPG 输入来自分析环（600 缓冲实测 0.909534 fps），
而冻结 FIR 的设计速率是 30 Hz。本轮量化了"多快才可能出心率"，并把静默 null 改成明确拒报。

| 项 | 证据 |
|---|---|
| 喂养速率边界（合成 72 bpm） | `2026-11-01_vital_feed_rate.json`：30/29/28/27 Hz 接受（71.5–72.8 bpm），≤26 Hz 报 `sampling_too_slow` |
| 端到端慢节奏回放（1.667 fps） | `2026-11-01_vital_slow_cadence.log`：60 帧中 58 帧 `vital_reject=sampling_too_slow`，仍产出 59 条合法契约 |
| 契约不变 | 658 帧回放改动前后 SHA256 均为 `44f01041b901e7a30c823a45205c0d78736eab4175361f3e6ad945c468a2de84` |

## 同期修掉的缺陷

声明的容差是 `fps±10%`（27–33 Hz），但扫描发现 29/28/27 Hz 全部返回
`warmup_history_unavailable`：环形缓冲按样本数分配，而重采样网格是 30 Hz，
低速喂养时网格点数超过缓冲容量。样本环与网格暂存已分开分配。

## 边界

- 全部为主机（x86）与合成信号结果；`hardware_test=false`，板卡未连接。
- 不构成真人准确率结论，也不改变契约字段或阈值。
- 要让心率真的可报，必须让 `vital` 的样本来自相机帧率（≈30 Hz），设计与预算见 `docs/63` §7。

复现：

```powershell
. .\env.ps1
wsl -d Ubuntu -- python3 metrics/scripts/measure_vital_feed_rate.py `
  --output metrics/evidence/2026-11-01_vital_feed_rate.json
```
