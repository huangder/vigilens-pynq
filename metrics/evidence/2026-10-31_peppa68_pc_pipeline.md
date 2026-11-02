# 2026-10-31 Peppa 68 点 PC 链路实测（检测 → 裁剪 → 68 点 → 几何）

对应 [docs/54](../docs/54_板端自治交接_handoff_20261031.md) §5 的 P1「检测/跟踪/裁剪接入，真实帧送入 68 点模型」
与「68 点几何与行为迁移」。**全部在本机 PC 上完成，没有访问板卡**。

## 本次新增的代码

| 文件 | 作用 |
|---|---|
| [`board/autonomy/peppa_pipeline.py`](../board/autonomy/peppa_pipeline.py) | MediaPipe 检测 → 作者 1.4× 裁剪 → 160×160 BGR → Peppa ONNX → 68 点 → 回原图坐标；逐帧 JSONL + summary |
| [`board/autonomy/peppa_geometry.py`](../board/autonomy/peppa_geometry.py) | EAR / MAR、iBUG 点序自检、每只眼的可用性护栏、2D 姿态代理、额头 ROI 构造 |
| `board/autonomy/tests/test_peppa_pipeline.py` | 裁剪/反投影/重检测节奏/记录契约，33 项单测中的 19 项 |
| `board/autonomy/tests/test_peppa_geometry.py` | EAR/MAR 闭式值、点序自检、退化护栏、代理量单调性 |

预处理**照抄作者 ONNX 约定**（不是重新设计）：1.4 倍框、BGR、160×160 双线性、
`(pixel-127)/127`、NCHW float32、前 136 个输出按 68 组 XY 读取、乘**裁剪后**宽高再加偏移。
裁剪贴边时会小于 1.4 倍，代码按实际裁剪尺寸反投影，这一点有单测钉住。

## 真实运行【已验证：2026-10-31 本机 Windows / Python 3.12.13】

```powershell
. .\env.ps1
python board/autonomy/peppa_pipeline.py --source data/raw/blink.mp4 --frames 656 `
  --output metrics/logs/peppa_68_blink_full.jsonl `
  --summary metrics/logs/peppa_68_blink_full_summary.json
```

| 项 | 实测 |
|---|---|
| 视频 | `data/raw/blink.mp4`，640×480，30 fps，656 帧（约 21.9 s） |
| 检出人脸 | **656 / 656**（detector 短距 128 模型，`model_selection=0`） |
| 几何可用帧 | **621**；退化 35；单眼可用 15（全部落在 214–250、300–330 的转头/模糊段） |
| iBUG 点序自检 | 6 项检查（双眼左右分离、两个眼环、下颌弧、嘴环、鼻尖低于眼线）**656/656 全通过** |
| 推理耗时（**PC**） | detect p50 4.46 ms / p95 4.84 ms；Peppa p50 1.81 ms / p95 2.17 ms |
| 额头 ROI | 656/656 非空，中位 145×31 px，`roi_contains_points` 不含眉/眼点（由构造与单测保证） |

⚠️ 上表耗时是**本机 x86**，不能当 Mizar-Z7020 的数据；板端单模型预算是 27.59 ms（docs/54 §3.2）。

## EAR 与现有 MediaPipe 路径的同视频对照

同一帧、同一公式（`( |p2-p6| + |p3-p5| ) / 2|p1-p4|`）：

| 子集 | 配对帧 | Pearson | EAR 中位差 | 绝对差中位 / p95 | MediaPipe 中位 | Peppa 中位 |
|---|---:|---:|---:|---:|---:|---:|
| 全部可用帧 | 618 | 0.593 | +0.039 | 0.0409 / 0.1472 | 0.349 | 0.387 |
| 仅正视（\|yaw_proxy\|<0.30） | 567 | 0.623 | +0.039 | 0.0392 / 0.1140 | 0.345 | 0.385 |

结论（只说这些）：

- 【已验证】两套几何**中位相差约 0.04**，Peppa 系统性偏高；相关系数 0.6 左右，不是同一把尺子。
- 【已验证】冻结的 `ear_close_threshold=0.21` 直接搬到 Peppa 上，这段素材 **0 次眨眼**；
  MediaPipe 在同一段也判 0 次（其 EAR 最低 0.212）。所以**不能**把「0 次眨眼」写成 Peppa 漏检，
  只能说这段素材在两种模型下都没有达到 0.21 的闭眼判据。
- 【已验证】用冻结阈值跑完整行为链：眨眼 0、长闭眼 0、哈欠 1、PERCLOS 0。
  这三个数字都是**该阈值下的真实输出**，不是准确率结论。
- 【不确定】阈值该改成多少：没有人工标注（用户已答「暂无」），不能由 AI 生成真值，本轮不做标定。

## 新增的退化护栏（为什么必须有）

第 214–250、300–330 帧左右，远侧眼的 6 个点会塌成几个像素：第 240 帧 `ear_left=3.17`、
IOD 从约 94 px 掉到 21 px（检测框宽度几乎没变，所以不是人脸变小）。
这种值若进 PERCLOS，会把"转头/模糊"记成"睁眼异常"。

`eye_validity()` 用两个**无量纲**比值判定（不是新的测量阈值）：

- `iod / 检测框宽 >= 0.18`：正常段约 0.39，退化段约 0.10；
- 单眼宽度 / 较宽一眼 >= 0.35：正常约 1.0，退化眼约 0.05–0.19。

被判定不可用的眼**不参与 EAR 平均**；整帧不可用时记录 `geometry_valid=false`、`ear=null`，
既不报数也不补 0。单测固定了这三条行为。

## 明确没有做的事

- **没有跟踪器**：只有「每 N 帧重检测 + 最多保持 M 帧」，记录里 `source=hold`。
  光流跟踪是另一套要移植的算法，没实现就不写"已跟踪"。
- **没有 PnP 姿态**：68 点在本仓库没有 3D 参考点，`pose_proxy` 只给 `roll_deg`（真 2D 角）
  和 `yaw_proxy` / `pitch_proxy`（无量纲比值），**不写进契约 `face.pose`**。
- **没有额头 ROI 的 rPPG 验证**：ROI 由眉线与眼线几何构造，尚未证明能采到可用脉搏。
- **没有准确率**：无人工标注；EAR 对照只是两套模型的相互一致性。

## 复现与单测

```powershell
. .\env.ps1
python -m pytest board/autonomy/tests/test_peppa_pipeline.py board/autonomy/tests/test_peppa_geometry.py -q
# 实测：33 passed
```

机器可读结果：[summary](2026-10-31_peppa68_summary.json)、
[EAR 对照](2026-10-31_peppa68_vs_mediapipe.json)。
逐帧 68 点（约 1 MB）在 `metrics/logs/peppa_68_blink_full.jsonl`，不入库，可用上面命令重建。
