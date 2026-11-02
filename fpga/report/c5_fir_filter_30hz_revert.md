# C5 回退：`fir_filter` 采样率 45 → 30 Hz —— 系数重生成 + 三重对拍（🚧 契约 v1.2 草案）

> **日期**：2026-09-20 　**线**：C 线（FPGA/HLS）　**发起人**：C 线
> **契约状态**：🚧 **v1.2 草案，尚未会签**。会签完成前 **v1.1（45 fps）仍是生效版本**
> （`backend/contract.py` 的 `CONTRACT_VERSION` 保持 `v1.1`）。
> **改动依据**：`AGENTS.md` §4（契约变更流程）；契约 `docs/interface.md` §0 / §3.5 与第 6 节变更记录。

---

## 1. 为什么改

需求来自人类（2026-09-20）：**把 C 线的采样率改回原本的 30 Hz**，用于前期适配低帧率采集源
（OpenMV 等），并**保留后续升级到 60 fps 的接口**。

除"适配采集源"之外，**C 线自己的实测结论也支持这一步**（见 `c5_fir_filter_45hz_reserved.md` 第 4 节）：
45 fps 对"心率/呼吸测得更细"**没有正向帮助**，反而**降低了呼吸/DC 分离能力** ——
`0.1~0.5 Hz` 抑制从 30 Hz 的 −11~−28 dB 退化到 −9~−15 dB，原因是同阶数 N=63 下
Hamming 过渡带宽 `Δf ≈ 3.3/(2πN)·fs` 随 fs 线性变差（0.250 → 0.375 Hz）。

---

## 2. 一句话结论 + 诚实边界

**结论**：30 Hz 系数**能生成、能算对、与 v1.1 之前的 30 Hz 表逐项完全相同**（即"改回原本的 30"，
不是另设计一个 30），且与 A 线实现的逐样本对拍**容差 0 通过**。

**诚实边界（必读）**：

| 项 | 状态 |
|---|---|
| 系数设计 / 黄金参考 / 主机端模型 / A↔C 对拍 / `pytest` | ✅ **已跑，全过**（命令与输出见第 4~6 节） |
| **csim / csynth / cosim @30 Hz** | ❌ **未重跑**（本次在受限沙箱内完成，跑不了 HLS —— 需命名管道，`Win32 error 5`）。**这是本草案唯一未闭合的验证项** |
| `xp`/上板相关结论 | ❌ 不存在（M3 未开始） |

> ⚠️ **本报告不含任何"@30 Hz 已综合/已仿真"的说法**。第 6 节表里的资源/时序数字一律标注了它们来自
> **v1.1 的 @45 Hz 实测**，不得当作 @30 Hz 的结论引用（《05》铁律 1、`AGENTS.md` §1.1）。

---

## 3. 改了什么

**契约与共享件**（属三方共享，已在提交正文写明）：

| 文件 | 改动 |
|---|---|
| `docs/interface.md` | 头部加 v1.2 草案声明；§0 帧率 45→30 fps；§3.5 采样率 45→30 Hz 及其各项实测数字；已知限制改为 30 fps 口径（0.250 Hz / N≳105/158）；§6 追加 2026-09-20 变更记录 |
| `config.yaml` | `fps_nominal` 45→30（**契约 §0 的唯一来源**），并写明"改它必须同步重生成系数与黄金参考" |
| `AGENTS.md` | §7.1 帧率行 + §7.6 加 v1.2 草案说明 |
| 根 `README.md` | C 线成果段与契约版本段同步 |
| `data/README.md` | 录制规范 45→30 fps、转码 `-r 30`、`ffprobe` 期望 `30/1`，并改写过时的"30 已作废"警告 |
| `metrics/scripts/check_p4_readiness.py` | `EXPECT_FPS` 45.0→30.0 |

**C 线自身**：

| 文件 | 改动 |
|---|---|
| `fpga/src/fir_coeffs_q15.h` | **重生成**（`FIR_FS_HZ 30` + 63 个新系数） |
| `fpga/sim/data_fir/{golden_fir.csv,golden_fir_out.csv,meta.txt}` | **重生成**（`fs=30`；`series.bin` 不进库、可由 seed 重建） |
| `fpga/sim/design_fir_coeffs.py` | **采样率不再写死**：默认从 `config.yaml` 的 `fps_nominal` 读（`_config_fps()`）；`--fs` 与 config 不一致时**显式告警**；用法注释补上"换采样率 = 换契约"的成套步骤 |
| `fpga/src/fir_filter.cpp` | 注释同步（群延迟 1033 ms、呼吸带 0.250 Hz） |
| `fpga/sim/tb_fir_filter.cpp` | DC 判据注释同步（判据本身由系数表当场算出，不硬编码，无需改逻辑） |
| `fpga/README.md` | C5 行、时间序列 30 Hz、器件行、复现命令、验收清单同步 |

**受影响的其他线（已改，需对应线复核）**：

| 文件 | 线 | 改动 |
|---|---|---|
| `backend/config.py` | A | `FALLBACK["fps_nominal"]` 45→30（**不装 PyYAML 的机器会走这个兜底**，不同改就会静默按旧帧率跑） |
| `backend/vital.py` | A | `load_fir_coeffs()` 的 `FIR_FS_HZ` 兜底 45→30 |
| `backend/tests/test_vital.py` | A | 把 `assert fs == 45` 改成 **`assert fs == float(CFG["fps_nominal"])`** —— 从"写死一个数字"升级为"抓两个单一来源（C 线头文件 vs config.yaml）之间的漂移" |
| `backend/README.md` | A | `rr_*` 限制说明、节流示例（3 秒 = 90 帧） |
| `frontend/app.js` + `frontend/README.md` | B | 呼吸率卡的契约限制文案 `@45 fps`→`@30 fps` |

**未改（有意留下，交对应线处理）**：
`backend/A_LINE_DEV_STEPS.md`、`backend/P4_CALIBRATION_GUIDE.md`、`docs/07`、`report/llm_log/**`
里大量 `45 fps` 属于**历史记录或 A 线过程文档**，按其归属由 A 线自行决定是否加注；
`metrics/evidence/**` 与 `fpga/report/c5_fir_filter_45hz_reserved.md` 是**已有证据与历史报告**，
**不改写**（改写既有证据等于伪造）。

---

## 4. 系数设计（真实运行）

```
python fpga/sim/design_fir_coeffs.py --fs 30 --d 0.0
```

| 项 | 实测值 |
|---|---|
| 阶数 / 群延迟 | N = 63（I 型线性相位）；31 样本 = **1033.3 ms**（@45 fps 时是 688.9 ms） |
| 设计边缘 | 0.700 / 3.500 Hz（外扩 d = 0.00，与 v1.1 同约定） |
| 实测 −3 dB 点 | **0.895 / 3.305 Hz** |
| 实测 −6 dB 点 | **0.704 / 3.496 Hz** |
| `Σh` (Q15) | **897** → DC **−31.25 dB**（@45 fps 时是 5710 / −15.18 dB） |
| `Σ|h|` | **55073**；峰值系数 **6101** |
| 溢出界 | `|acc| ≤ 32768·Σ|h| =` **1,804,632,064** < 2³¹−1 ✅ |
| 通带 1.0~3.0 Hz 起伏 | **1.90 dB**（@45 fps 是 2.99 dB） |
| 阻带最差 | 低阻带 **−16.2 dB @0.350 Hz**；高阻带 **−16.0 dB @3.850 Hz** |
| 呼吸带（对照） | 过渡带 **0.250 Hz**（@45 fps 是 0.375 Hz）；仍**做不了** 0.1~0.5 Hz |

**「改回原本的 30」的证据**（关键一步）：把新头文件与 **git 历史里 v1.1 之前的 30 Hz 头文件**
（`git show 5ee003d^:fpga/src/fir_coeffs_q15.h`）逐项对比：

```
新: taps=63 fs=30 shift=15  系数63个  Σh=897 Σ|h|=55073
旧: taps=63 fs=30 shift=15  系数63个  Σh=897 Σ|h|=55073
系数逐项完全相同: True
taps/fs/shift 相同: True
```

日志：`fpga/report/logs/2026-09-20_fir_filter_30hz_design.log`

---

## 5. 黄金参考重生成（真实运行）

```
python fpga/sim/gen_fir_vectors.py          # 从 fir_coeffs_q15.h 读 fs，不另抄一份系数
```

- 16 段 / **4036 样本**（与 @45 fps 版本同段数、同样本数）
- 冲激响应自检：峰值下标 **31** = 群延迟 ✅、偶对称 ✅
- **numpy 独立对拍：4036 / 4036 逐样本相等 ✅**（`np.convolve` 路径，与主实现完全独立）
- 饱和覆盖：`square_1hz_full` 饱和 **33** 样本（@45 fps 时是 0 —— 系数变小不再削波）、
  `saturate_matched` 饱和 **7** 样本 → 有饱和/无饱和两条分支都被覆盖

日志：`fpga/report/logs/2026-09-20_fir_filter_30hz_vectors.log`

---

## 6. 三重对拍

| # | 对拍 | 命令 | 结果 |
|---|---|---|---|
| 1 | **C++ 主机端模型 vs Python 黄金参考** | `g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe && host_model_fir.exe fpga/sim/data_fir` | `int32+对称折叠 vs Python 黄金参考：逐样本相等 ✅（不一致 0 个样本）`；`对称折叠 vs 朴素长整型累加：逐位相同 ✅`；`段：16 passed, 0 failed`；饱和合计 40；**`== PASS ==`** |
| 2 | **A↔C 跨语言（`backend/vital.py::fir_process`）** | `python metrics/scripts/check_fir_golden.py` | **16 段 / 4036 样本逐样本全等、段统计一致（容差 0）✅** |
| 3 | **仓库自检** | `.venv\Scripts\python.exe -m pytest -q` | **78 passed** |

> ⚠️ 第 1 项自报家门：**这是主机端算术模型，不是 HLS 仿真/综合证据**（脚本自己也会打印这句）。

**过程中抓到的一个真实问题（有价值）**：在"系数已换 30 Hz、`config.yaml` 还是 45"的**中间态**下跑
`pytest`，失败 1 项：`test_fir_uses_frozen_coefficients_and_arithmetic` 断言 `fs == 45`。
这正说明**系数与帧率必须同改** —— 该断言已改为与 `config.yaml` 对拍，从此这类漂移会被直接抓住，
而不是靠人记得改。

---

## 7. 未验证项：csim / csynth / cosim（**必须补跑**）

**为什么没跑**：本次改动在受限沙箱内完成，HLS 需要命名管道（`Win32 error 5`），
`vitis-run` 无法执行；且综合/cosim 耗时较长，应由 C 线在**完整权限终端**跑。

**要在会签前补的命令**（`AGENTS.md` §6.3）：

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
set "HLS_IP=fir_filter" && vitis-run --mode hls --tcl run_hls.tcl
:: 期望：csim 8/8 + 16/16（内嵌边界 + 跨语言黄金参考）、0 errors、II=1
:: 再跑一次 cosim（hls_exec=2），归档到 fpga/report/logs/
```

**为什么"预计资源不变"但仍不许当结论**：结构/阶数/位宽一字未改，只有 63 个 **系数值**不同，
而 HLS 会把 `static const` 系数表**常量折叠**进乘法器（`c5_fir_filter_45hz_reserved.md` 第 6 节已论证过同一点）。
所以**预计** LUT/FF/DSP/BRAM/Fmax 与 @45 Hz 相同（LUT 4043 / FF 6174 / BRAM 0 / DSP 26 / Fmax 154.38 MHz）——
但**这是推测，不是实测**，补跑前不得写进报告或答辩材料。

---

## 8. 「保留升级到 60 fps 的接口」—— 具体是什么

**没有**采用"IP 内放两组系数 + 一个选择寄存器"的方案（那要改契约 §3.5 的寄存器映射，代价大且此刻无收益）。
采用的是**单一来源 + 一条命令链路**：

1. **采样率只有一个来源**：`config.yaml` 的 `fps_nominal`。
   `design_fir_coeffs.py` 已改为**默认从它读**（不再写死 30/45），`gen_fir_vectors.py` 从**头文件**读 fs。
2. **升级到 60 fps 的完整步骤**（改 config + 跑两条命令 + 重跑仿真 + 走契约流程）：

```powershell
# ① 改契约（§0 帧率 + §3.5 采样率）并登记第 6 节，然后：
#    把 config.yaml 的 fps_nominal 改为 60
python fpga/sim/design_fir_coeffs.py --d 0.0        # → fpga/src/fir_coeffs_q15.h
python fpga/sim/gen_fir_vectors.py                  # → fpga/sim/data_fir/（黄金参考）
# ② 三重对拍
g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe
host_model_fir.exe fpga/sim/data_fir
python metrics/scripts/check_fir_golden.py
.venv\Scripts\python.exe -m pytest -q
# ③ 完整权限终端重跑 csim / csynth / cosim
# ④ 群公告 → A/B 确认 → 会签升级版本号（contract.py 的 CONTRACT_VERSION）
```

3. **防错设计**：`--fs` 与 `config.yaml` 不一致时会**显式告警**（实测 `--fs 60` 会打印
   "写出的系数表会与契约 §0 漂移，只应用于评估/对比，不要直接提交"）；
   `backend/tests/test_vital.py` 会把"C 线头文件 vs config.yaml"的不一致变成**红灯**。

> 若将来确实需要**运行时切换**（同一比特流同时支持 30/60），那要改契约 §3.5 的寄存器映射
> （两组系数 + 选择寄存器），属另一个变更，**本次未做**。

---

## 9. ⚠️ 必须说清的一点：30 Hz 并不等于"能适配 OpenMV"

改用 30 Hz 是为了让**采样率**与低帧率采集源对齐，但 **OpenMV Cam H7 在 640×480 上拿不到 30 fps**：

| OpenMV H7 口径 | 实测/官方数字 |
|---|---|
| 片上采集（sensor 在板内） | 640×480 灰度/RGB565 60~75 fps |
| **送 PC（USB VCP，JPEG）** | **VGA 11.7 fps**（官方示例）→ 定时器双缓冲写法 **20 fps** |
| QVGA 320×240 | JPEG 流约 25~32 fps |

即：**要 640×480 就只有 10~20 fps；要 ~30 fps 必须降到 320×240**（而分辨率是契约 §0 的冻结项）。
所以本草案解决的是"**采样率口径**"，**没有**解决"OpenMV 作为 640×480@30 采集源"这件事 ——
后者需要另行决定（换 UVC 摄像头 / 接受 QVGA（要改 §0 分辨率）/ 只用 OpenMV 做低帧率演示）。

---

## 10. 复现步骤（陌生机器，从零到本报告全部数字）

```powershell
# 依赖：Python（标准库即可，numpy 可选但强烈建议）+ g++ + （补跑仿真时）Vitis HLS 2026.1
python fpga/sim/design_fir_coeffs.py --d 0.0        # fs 自动取 config.yaml 的 fps_nominal（30）
python fpga/sim/gen_fir_vectors.py
g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe
host_model_fir.exe fpga/sim/data_fir
python metrics/scripts/check_fir_golden.py
.venv\Scripts\python.exe -m pytest -q
```

**判据**：`Σh=897 / Σ|h|=55073`、冲激峰值下标 31、numpy 对拍 4036/4036、
主机模型 `== PASS ==`、A↔C `容差 0 全等`、`pytest 78 passed`。

---

## 11. 证据文件

| 文件 | 内容 |
|---|---|
| `fpga/src/fir_coeffs_q15.h` | **30 Hz 系数表**（自动生成；与 v1.1 之前的 30 Hz 表逐项相同） |
| `fpga/sim/data_fir/golden_fir.csv` | 每段小结（16 段，含 `saturate_matched`），**入库** |
| `fpga/sim/data_fir/golden_fir_out.csv` | 逐样本期望输出 **4036 行**，**入库可人工评审** |
| `fpga/sim/data_fir/meta.txt` | `fs=30` / 16 段 / 4036 样本 |
| `fpga/report/logs/2026-09-20_fir_filter_30hz_design.log` | 系数设计真实输出 |
| `fpga/report/logs/2026-09-20_fir_filter_30hz_vectors.log` | 黄金参考重生成 + numpy 对拍真实输出 |
| `fpga/report/logs/2026-09-20_fir_filter_30hz_hostmodel.log` | 主机端模型真实输出 |
| `fpga/report/c5_fir_filter_45hz_reserved.md` | **历史报告，不改写**（45 fps 的探索与结论） |

*本报告由 C 线维护。它是 **v1.2 草案**的一部分：会签完成前不得作为"契约已改"引用。*
