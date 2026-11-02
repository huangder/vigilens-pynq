# A2 素材实测（第二段真实素材）：窗口**第一次攒满** 900/900，但心率仍为 null（2026-10-25）

> **素材**：手机竖屏 720×**1280**、30.053 fps、**1000 帧 / 33.27 s**、9.83 MB。
> 原始文件在聊天工具目录（不入库，隐私红线）：`...\nt_data\Video\2026-10\Ori\1de6dd11ed9a6f8a10266eba9ec6d655.mp4`；
> 入库时按 `data/raw/a2_still_portrait_33s.mp4` 命名（`data/raw/*.mp4` 被 `.gitignore` 挡住，**不要提交**）。
> **一句话结论**：`rppg_window_samples = 900 / need 900`（**第一次真正满窗**），
> `rppg_reject = null`，但 `vital.*` 四个键全 `null` ⇒ 卡点已从"窗口没满"前移到"**频谱没有主导峰**"。
> **表述边界**：本文数字只证明"链路走到哪一步"，**不是**心率测量精度，也不做任何健康结论。

---

## 1. 环境与硬约束（先钉死）

| 项 | 值 | 出处 |
|---|---|---|
| 窗口长度 | `window_seconds: 30` | `config.yaml` |
| 标称帧率 | `fps_nominal: 30` | `config.yaml` |
| 窗口需求样本 | `round(30 × 30)` = **900** | `backend/vital.py`（`rppg_window_need`） |
| 采样率容差 | `rppg_fs_tol: 10%` | `config.yaml` / BUG-029 |
| 质量门控 | `vital_require_quality: 0.75` | `backend/decision.py:44`（`gate_vitals`） |
| 本文是否改过窗口/阈值 | **没有** | —— |
| 本文是否填过任何 `vital.*` 假值 | **没有** | —— |

三条**不许做**（本条与 `2026-10-24_a2_real_rppg.md` 一致）：

1. **不许**为了"出个数字"改 `window_seconds` 或 `fps_nominal`；
2. **不许**给 `vital.*` 填假值 / 从别的素材搬数字；
3. **不许**用 `--loop` 把同一段 33 s 循环播放去"凑满样本"——重复内容会让人频谱出现人为周期峰，得到的数字**没有测量意义**。

---

## 2. 复现命令（原样可跑）

```powershell
cd D:\Desktop\AMD
. .\env.ps1

# 素材在聊天工具目录时（不复制也能跑；把它换成本机真实路径）
$src = "$env:USERPROFILE\Documents\Tencent Files\3115858279\nt_qq\nt_data\Video\2026-10\Ori\1de6dd11ed9a6f8a10266eba9ec6d655.mp4"

python backend\run_pipeline.py --source $src `
    --json    metrics\logs\a2_portrait.json `
    --jsonl   metrics\logs\a2_portrait.jsonl `
    --summary metrics\logs\a2_portrait_summary.json
```

（`metrics/logs/` 是生成物、不入库；`--summary` 是**唯一**能看到 `rppg_window_samples` / `rppg_reject` 的地方，
只存 `--json` 快照是看不到这些诊断键的——快照已被 `strip_internal()` 剥成契约帧。）

---

## 3. 实跑输出（贴原样关键行）

```text
[face_landmark] 使用 MediaPipe FaceMesh（478 点）。
方向   : 原样（探测 8 帧，各角度检出帧数 {0: 8, 90: 7, 180: 2, 270: 7}）

=== 运行摘要 ===
处理帧数  : 1000（逻辑时长 33.33s，墙上耗时 27.08s）
关键点来源: mediapipe
状态分布  : fatigue_risk=776  normal=186  adjust_posture=38
末帧指标  : 眨眼 4 次 / 8.0 per min，PERCLOS 0.053，哈欠 1，长闭眼 0
末帧质量  : overall 0.84 (light 0.71 / motion 0.05)
末帧状态  : fatigue_risk —— 疲劳风险升高，建议休息 5 分钟或远眺放松
```

`--summary` 里的 rPPG 诊断键（**本轮最关键的四行**）：

```json
"rppg_window_samples": 900,
"rppg_window_need": 900,
"rppg_time_base": "fid_nominal",
"rppg_fs_eff": 30.0,
"rppg_reject": null
```

契约帧末帧（`--json` 快照）：

```json
"vital": {"hr_bpm": null, "hr_conf": null, "rr_per_min": null, "rr_conf": null},
"quality": {"light_score": 0.7108, "motion_score": 0.0475, "overall": 0.8411}
```

逐帧扫描 1000 帧 + 收尾帧：`hr_bpm` 非空 **0 次**、`rr_per_min` 非空 **0 次**（`rr_*` 恒 null 是契约 §3.5 的既定口径，不是缺陷）。

---

## 4. 三件可以下结论的事

1. **"≥35 s 素材"这条经验值偏保守**。本段 33.27 s / 1000 帧就把窗口攒到 **900/900**。
   真实要求是**≥30 s 的有效可见帧**（`window_seconds × fps_nominal`），33 s 够；
   建议仍录 **40 s**，留出"开头找脸/低头"的余量。**旧结论按本条更新，但不要据此去改 `config.yaml`**。
2. **质量门控没拦它**：末帧 `overall = 0.8411 > 0.75`，`gate_vitals` 会把估计值放行——所以 null 不是门控造成的。
3. **卡点已前移到频谱判据**。`rppg_reject = null` 且估计值为 null，对应 `backend/vital.py:382-383`：

   ```python
   if conf <= 0.5:
       return dict(none4)          # ← 这条路径**不设** last_reject
   ```

   即"峰邻域能量占不到带内一半"，判为没有真峰。

   > ⚠️ 这里有一个**诊断缺陷**（本轮只记录、未改代码）：这条分支不写 `last_reject`，
   > 于是 summary 读起来像"没给原因"。修法应是 `self.last_reject = "no_dominant_peak"`，
   > 并按仓库规矩补一条单测（改 A 线算法文件必须带测试，且我不在本轮顺手改）。

---

## 5. 这段素材还不能证明什么

- **不能**证明心率算法在真人身上准或不准（它压根没出数）；
- **不能**当作"心率趋势"证据，因为 `vital.*` 全空；
- **不能**推广到竖屏以外的场景：本段是竖屏 720×1280（ROI 按当帧真实尺寸裁剪，见
  `backend/run_pipeline.py:464-466` 的注释与 BUG 记录），所以它同时验证了"非 640×480 素材也能跑"这一条；
- 状态分布里 `fatigue_risk` 占 776/1000，**只能用来说明阈值还没标定**（A1 欠账），
  `ear_close_threshold` 等仍是 `config.yaml` 里的占位值，**不得**作为疲劳结论引用。

---

## 6. 与既有证据的关系

| 证据 | 素材 | 帧数/时长 | 窗口 | 结论 |
|---|---|---|---|---|
| `2026-10-24_a2_real_rppg.md` | `data/raw/blink.mp4`（640×480） | 656 / 21.87 s | 629 / 900 ⇒ `window_not_full` | 缺口 ~271 样本，**要长得多的素材** |
| 本文 | 竖屏 720×1280 | 1000 / 33.27 s | **900 / 900，reject=null** | 窗口已满，**卡在"没有主导峰"** |

两段合起来把 rPPG 的问题**收敛成一句话**：素材长度已经不是瓶颈，接下来要动的是
额头 ROI 的取色质量（压缩/自动曝光/欧拉放大）与峰判据，**不是**继续加长录制时间。

---

*本文所有数字来自 2026-10-25 本机真实运行；命令见 §2。未跑过的项已在 §5 明确标注。*
