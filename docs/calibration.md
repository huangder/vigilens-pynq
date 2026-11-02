# 标定与诊断

阈值统一来自根 `config.yaml`。当前工程参数尚未构成跨人群标定结果，不应凭观察到的单个输出修改阈值或宣称准确性。

## 行为与质量阈值

工具 `metrics/scripts/calibrate_thresholds.py` 从本地真实素材采集特征，再基于人工标签输出建议值，不写入配置。

```powershell
. .\env.ps1
python metrics/scripts/calibrate_thresholds.py --collect data/raw/blink.mp4 --out metrics/logs/feat_blink.csv --limit 900
```

上面的文件需自行在本地准备；先核对使用真实关键点而非 stub。人工编辑 CSV 的 `label` 列，再执行：

```powershell
python metrics/scripts/calibrate_thresholds.py --fit metrics/logs/feat_blink.csv --what ear
```

| `--what` | 标签 |
|---|---|
| `ear` | `closed` / `open` |
| `mar` | `yawn` / `neutral` |
| `yaw`、`pitch` | `turn` / `ok` |
| `light` | `usable` / `unusable` |
| `motion` | `unusable` / `usable` |

未标注行不作为标签数据。建议值仅适用于所提供素材，不能把同一段拟合数据上的分数当独立验证。采用变更时记录素材条件、标签方法、拟合与独立验证结果，修改唯一配置源，并按 [提交规则](contributing.md) 处理共享参数；板端需重新导出/编译。

## rPPG 拒报

依次检查：当前人脸与额头 ROI 是否有效、样本是否来自同帧 RGB、有效积样数与时间跨度、间隙/实际采样率、质量/姿态门控、主峰及倍频歧义。帧号不能代替真实采样时间，重复旧帧不能补成有效样本。

当前 HR 窗与行为窗独立。5 秒有效积样窗只允许开始频谱检查，既不保证 5 秒实际出数，也不提供准确性承诺。滤波前的 `raw_hr` 与拒报原因只作旁路诊断；当前无效时契约仍为 null，页面历史值不能当新测量。

可核对 [短测诊断](../metrics/evidence/2026-11-02_hr_continuous_live2_results.json) 与 [逐帧原始值对拍](../metrics/evidence/2026-11-02_hr_continuous_live2_raw_verification.json)。定位接口错误先读 [interface.md](interface.md)，性能与实际验收分层见 [validation.md](validation.md)。
