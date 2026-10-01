# c6 2026-09-30 七个 IP 的 csim/csynth 复跑（720p 档第一步）

> **证据文件**（A/B/C 三方共享）：本报告的全部数字来自 **2026-09-30 本机真实运行**，
> 命令与原始输出见下。**引用前请自己重跑一遍**（命令在 §2）。
> 契约依据：`docs/interface.md` §0/§3（既有一档）+ `docs/20` §5（MIPI/720p 档，**提案未会签**）。
> 相关卡片：BUG-004、BUG-018、BUG-019、DOC-002；本轮新发现见 §4。

---

## 1. 结论一句话

**七个 IP 现在全部 `RESULT: PASS`。**
既有 4 个 IP 的资源/时序与 `fpga/report/` 里归档的旧报告**逐项一致**
（说明"仓库里的旧数字"是可信的，不是编的）；
新增 3 个 IP 里有 **2 个在本轮第一次送进 csim 时就是红的**
（`bayer_demosaic` 输出字节序反了、`frame_scale` 测试台断言恒假），
**两条都已定位、修好并复跑验证**。另有一处**我先判错、后自我更正**的结论
（`raw10_unpack` 的 `Final II = 4` 起初被我当成"吞吐瓶颈、威胁 720p60"，
实测与 csynth 原文证明**它完全够用，且 II=4 是输出端口宽度决定的、与算得对不对无关**），
原文与更正都保留在 §4.1 —— 那是个值得记的推理陷阱。

---

## 2. 复现命令（本机真实可跑）

```powershell
cd D:\Desktop\AMD\fpga
call D:\Xilinx\2026.1\Vitis\settings64.bat
# 逐个 IP（默认会 csim + csynth）：
$env:HLS_IP = "roi_statistic"
vitis-run --mode hls --tcl run_hls.tcl
```

⚠️ 本轮新增了一个**可选**环境变量 `HLS_COMPONENT`（`run_hls.tcl`，默认行为不变）：
当旧的 `fpga/component_<ip>/` 因 NTFS 拒绝项删不掉、导致
`error deleting ".../hls/hls.aps": permission denied` 时，用它可以换一个新目录名继续跑：

```powershell
$env:HLS_COMPONENT = "component_roi_run2"
```

（`fpga/component_*/` 本就在 `.gitignore` 里，不入库。）

---

## 3. 本轮实测结果（7 个 IP）

| IP | csim Layer 1 | csim Layer 2 | 结论 | Fmax | Final II | LUT | FF | BRAM18 | DSP |
|---|---|---|---|---|---|---|---|---|---|
| `roi_statistic` | **28 / 28** | **45 / 45** | ✅ PASS | 138.99 MHz | 1 | 1267 | 723 | 0 | 1 |
| `rgb2gray` | **8 / 8** | **10 / 10** | ✅ PASS | 137.46 MHz | 1 | 1410 | 918 | 0 | 5 |
| `motion_quality` | **6 / 6** | **9 / 9** | ✅ PASS | 140.05 MHz | 1 | 1501 | 1158 | **64（22%）** | 1 |
| `fir_filter` | **8 / 8** | **17 / 17** | ✅ PASS | 146.97 MHz | 1 | 4075 | 6172 | 0 | 25 |
| `raw10_unpack` | 3 / 3 | 5 / 5 | ✅ PASS | **151.98 MHz** | 4（**端口宽度使然**，非缺陷；见 §4.1） | 969 | 548 | 0 | 1 |
| `bayer_demosaic` | ~~1/3~~ → **3 / 3** | ~~2/5~~ → **5 / 5** | ✅ PASS（已修，§4.2） | 142.05 MHz | 1（另有一循环 II=5） | 1539 | 807 | 0 | 5 |
| `frame_scale` | ~~2/3~~ → **3 / 3** | **5 / 5** | ✅ PASS（已修，§4.3） | 151.68 MHz | 1 | 2282 | 2479 | 0 | 3 |

**对照归档报告**（`fpga/report/c3_c7_roi_statistic_v1.md`、`c4_rgb2gray_motion_quality_v1.md`、
`c5_fir_filter_v1.md`）：LUT/FF/BRAM/DSP 与 Fmax **逐项一致**（`fir_filter` 的 146.97 MHz 是
本轮实测；旧档案里的 154.38 MHz 是 45 Hz 系数那一轮的数，见下方"口径提醒"）。

### 3.1 原始输出（原样摘录，未改一个数字）

```
==== roi_statistic ====
---- [Layer 1] embedded boundary cases (8x6) ----
  Layer 1: 28 passed, 0 failed
---- [Layer 2] cross-language golden reference ----
  meta     : 640x480, 5 frames, 921600 bytes/frame, 45 golden rows
  Layer 2: 45 passed, 0 failed  (pass-through mismatched pixels: 0)
==== RESULT: PASS ====
INFO: [HLS 200-789] **** Estimated Fmax: 138.99 MHz

==== rgb2gray ====
  Layer 1: 8 passed, 0 failed
  Layer 2: 10 passed, 0 failed  (mismatched pixels: 0, header errors: 0)
==== RESULT: PASS ====
INFO: [HLS 200-1470] Pipelining result : Target II = 1, Final II = 1
INFO: [HLS 200-789] **** Estimated Fmax: 137.46 MHz

==== motion_quality ====
  Layer 1: 6 passed, 0 failed
  Layer 2: 9 passed, 0 failed  (stream header errors: 0)
==== RESULT: PASS ====
Final II = 1 / Estimated Fmax: 140.05 MHz

==== fir_filter ====
  Layer 1: 8 passed, 0 failed
  Layer 2: 17 passed, 0 failed  (样本不一致 0, 流头错误 0)
==== RESULT: PASS ====
Final II = 1 / Estimated Fmax: 146.97 MHz

==== raw10_unpack ====
  Layer 1: 3 passed, 0 failed
  Layer 2: 5 passed, 0 failed (mismatched pixels: 0, header errors: 0)
==== RESULT: PASS ====
INFO: [HLS 200-1470] Pipelining result : Target II = 1, Final II = 4, Depth = 5
INFO: [HLS 200-789] **** Estimated Fmax: 151.98 MHz

==== bayer_demosaic ====
  OK   flat fields (0/511/1023) all channels == to8(v), bad=0
  FAIL corner clamp: (0,0) -> R=64 G=128 B=255 (expect 255,128,64) hdr=0
  FAIL G direction probe: Gr(3,2).R=13(exp 38) .B=38(exp 13) | Gb(2,3).R=25(exp 25) .B=25(exp 25) ref_bad=60
  Layer 1: 1 passed, 2 failed
  FAIL frame=0 bad_bytes=611606 hdr=0 pc=307200
  FAIL frame=3 bad_bytes=614400 hdr=0 pc=307200
  FAIL frame=4 bad_bytes=18 hdr=0 pc=307200
  Layer 2: 2 passed, 3 failed (mismatched bytes: 1226024, header errors: 0)
==== RESULT: FAIL ====

==== frame_scale ====
  OK   12x9 crop(3,6) -> 4x6 : bad=0 hdr=0 pc=24 exp_size=72
  OK   dropped rows/cols never appear : whites=0 (expect 0)
  FAIL aspect check 1280x720 crop(160,960) -> 640x480 (pc=307200) hdr=0
  Layer 1: 2 passed, 1 failed
---- [Layer 2] cross-language golden reference (per-byte) ----
  Layer 2: 5 passed, 0 failed (mismatched bytes: 0, header errors: 0)
==== RESULT: FAIL ====
```

---

## 4. 本轮**新发现**（必须进台账，别让它烂在报告里）

### 4.1 `raw10_unpack` 的 `Final II = 4` —— **不是问题，是端口宽度使然**（含一次自我更正）
> 🔴 **本节在 2026-10-01 被更正过 —— 保留原文并说明为什么错了，因为它是一个很好的反面教材。**
>
> **原文（错）**：`Target II = 1, Final II = 4` 被解释成"每 4 个时钟才出一个像素，
> 需要 221 MHz 才够，威胁 720p60"。**这个推论是错的。**
>
> **更正后的结论**：`II = 4` 在这里的单位是**"一拍输入组"**，不是"一个输出像素"。
> csynth 给出的 II violation 原文把原因说得很清楚：
>
> ```
> WARNING: [HLS 200-880] The II Violation in module 'raw10_unpack_Pipeline_VITIS_LOOP_97_1':
>   Unable to enforce a carried dependence constraint (II = 1, distance = 1, offset = 1)
>   between axis write operation ('pix_out_V_data_V_write_ln125') on port 'pix_out_V_data_V'
>   and axis write operation ('pix_out_V_data_V_write_ln125') on port 'pix_out_V_data_V'.
> ```
>
> —— 是**同一个 `pix_out` 端口在一个迭代里被写 4 次**（4 个像素），
> 端口一拍只能收一个 beat ⇒ 每次迭代**必然**占 4 拍。**与计算逻辑无关**，
> 换任何写法都一样（除非把输出流加宽到能装下多个像素）。
>
> **真实吞吐**：一拍输入组 → 4 个输出像素，但输出侧被 1 像素/beat 夹住 ⇒ **每拍 1 像素**：
>
> | 档位 | 需要 | `raw10_unpack` 可支撑（Fmax 151.98 MHz × 1 px/beat = **152.0 Mpx/s**） | 余量 |
> |---|---|---|---|
> | 640×480 @30 | 9.2 Mpx/s | 152.0 Mpx/s | **16.5×** |
> | 640×480 @60 | 18.4 Mpx/s | 同上 | **8.3×** |
> | **720p60** | 55.3 Mpx/s | 同上 | **2.75×** |
> | **1080p45** | 93.3 Mpx/s | 同上 | **1.63×** |
>
> ⇒ **`raw10_unpack` 在两档目标上都有余量，不是瓶颈。** 我原来那句"威胁 720p60"没有依据。
>
> **教训（写进这里以免再犯）**：`Final II` 是**相对于该 pipeline 的循环迭代**而言的，
> 一个迭代里吐几个像素必须先数清楚，再谈"每几拍一个像素"。
> 只看到 `II=4` 就乘 4 倍算频率需求，是把**每迭代的写次数**当成了**每像素的周期数**。

#### 补充（2026-10-01）：流水线已扁平化，正确性不变

顺手把两层循环（`y` 外层 / `g` 内层）合并成**单层扁平循环**（一次迭代 = 一整组），
并去掉 `idx / wpl` 那种运行时除法（改用自由计数器推进行内组号）。

- **正确性**：`Layer 1: 3/3`、`Layer 2: 5/5`、**逐像素 0 不符**（与既有黄金参考逐位一致）。
- **II 仍是 4**，原因就是上面那条端口依赖 —— **符合预期，不再是"待修的问题"**。
- ⚠️ 若将来真需要 >152 Mpx/s，唯一的办法是**把输出流加宽**（例如
  `ap_axiu<32,...>` 一拍装 2 个像素，或 3 像素/beat），**不是**改循环结构。
  这件事**等真有需求再做**，现在两档都不需要。

### 4.2 `bayer_demosaic` 输出字节序反了（**新发现，已修**）
- **现象**：`corner clamp` 期望 `(255,128,64)` 实得 `(64,128,255)` —— R 与 B 恰好互换；
  Layer 2 三帧逐字节不符（`frame=0` 差 611606 B、`frame=3` 差 614400 B、`frame=4` 只差 18 B）。
- **根因**（【已验证】，逐行核对源码）：`fpga/src/bayer_demosaic.cpp:159-161` 写的是
  `(to8(R) << 16) | (to8(G) << 8) | to8(B)` —— 那是 **BGR**（R 跑到 bits[23:16]），
  但**同一个文件的注释**（`:158`）、**测试台的解码**（`tb:97-99`，R=bits[7:0]）、
  **`docs/19` §6 的"byte0=R"**、以及**下游消费方**
  （`rgb2gray.cpp:100` 的 `R=[7:0]`、`roi_statistic.cpp:87` 的 `p.data(7,0)`）
  **全都要求 R 在 byte0**。相位本身（RGGB，(0,0)=R）与插值都是**对的**。
- **为什么"差 18 B"能反证相位没错**：frame 4 是 (320,240) 上一个 +1023 的孤立冲激，
  交换 R/B 只影响 3×3 邻域内的 8 个像素 × 2 字节 = **18**，与实测完全吻合；
  若是相位错，G 相位也会跟着错，**不可能只差 18 B**。
- **改法**：把 `R` 放到 bits[7:0]、`B` 放到 bits[23:16]（3 行）。
- **验证**：`Layer 1: 3 passed, 0 failed`（`(0,0) -> R=255 G=128 B=64`、`ref_bad=0`）、
  `Layer 2: 5 passed, 0 failed (mismatched bytes: 0)`、`RESULT: PASS`、Fmax 142.05 MHz。
- **无需重新生成任何黄金参考**（`golden_rgb.bin` / `golden_mipi.csv` 本来就是 byte0=R）。
- ⚠️ **这个错误不会编译报错**：`rgb2gray` 会悄悄按 `77*B + 29*R` 算亮度。

### 4.3 `frame_scale` 的 "aspect check" 是恒假断言（**新发现，已修**）
`fpga/sim/tb_frame_scale.cpp:212` 原来写的是

```cpp
(960u * 3u == 640u * 2u * 2u) && (720u * 3u == 480u * 2u * 2u)
```

`960*3 = 2880 ≠ 640*4 = 2560`、`720*3 = 2160 ≠ 480*4 = 1920` —— **两边全是编译期常量，
与 DUT 无关 ⇒ 该断言永远 FAIL**，而且与同一表达式里的 `ow == 640` 自相矛盾
（`960*3/4 = 720 ≠ 640`）。也就是说：这条 "aspect check" **从来没有在测任何东西**，
它报 FAIL 不含任何关于 IP 的信息。（IP 的抽取口径是 **3:2**（每 3 取 2），
不是 2:1 —— `720→480` 与 `960→640` 都是 3:2，与 `docs/20` §2.2 一致。）

- **改法**：换成与 `host_model_scale.cpp:204`、`gen_scale_vectors.py:164` 同一条比例恒等式
  `out * DEN == in * NUM`（用测试台已有的 `SCALE_NUM` / `SCALE_DEN` 宏，避免以后再漂）。
- **验证**：`Layer 1: 3 passed, 0 failed`、`Layer 2: 5 passed, 0 failed`、`RESULT: PASS`，
  并首次拿到该 IP 的资源：**LUT 2282 / FF 2479 / BRAM 0 / DSP 3 / Fmax 151.68 MHz / Final II = 1**。
- **无需重新生成任何黄金参考。**

---

## 5. 口径提醒（引用 C 线数字时的硬规则）

1. **`fir_filter` 的系数取决于采样率档**：本轮跑的是仓库当前默认系数表。
   `docs/interface.md` 的 v1.1 是 **45 Hz**、v1.2 草案是 **30 Hz**、`docs/22`/`37bf473` 又加了
   **60 Hz（N=127）**。（`docs/22` 已于 2026-10-01 并入 `docs/20` §7 并删除）**说 Fmax 必须同时说"哪一档系数"**，否则数字不可比。
2. **`raw10_unpack` / `bayer_demosaic` / `frame_scale` 的接口当前只在 `docs/19`/`docs/20` 里是"提案"**，
   **未进 `docs/interface.md`** ⇒ 按 `AGENTS.md` §4，它们**还不能被当作已冻结接口对接**。
   本报告只报"仿真结果"，不构成契约。
3. **csim 不建模 `hls::stream` 的 FIFO 深度**；`raw10_unpack`/`frame_scale` 的流深度达到
   307200 / 921600 —— **流深度不足导致的死锁只有 cosim 会暴露**。cosim 已于 §5 补跑，见该节。

---

## 5. cosim（C/RTL 协同仿真，2026-10-01 补跑）

脚本：`fpga/run_cosim_all.ps1`（7 个 IP 逐个 `HLS_EXEC=2`，一律写 `_cosim_<ip>.log`）。
⚠️ 该脚本**刻意只用 ASCII** —— Windows PowerShell 5.1 会把无 BOM 的 `.ps1` 按 GBK 解码，
中文注释会被解坏并引发 `The string is terminater` 类语法错误（本轮踩过一次，见 `docs/24` §8.2.1）。

| IP | cosim 结果 | 说明 |
|---|---|---|
| `roi_statistic` | ✅ **C/RTL co-simulation finished: PASS** | 无死锁 |
| `motion_quality` | ✅ **PASS** | 无死锁（**这条最关键**：它是唯一带整帧 BRAM 缓存的 IP） |
| `raw10_unpack` | ✅ **PASS** | 无死锁 |
| `bayer_demosaic` | ✅ **PASS** | 无死锁（3 行行缓冲） |
| `frame_scale` | ✅ **PASS** | **8/8 RTL 事务跑完**、Layer 2 `5/5, 0 mismatched bytes`、**无死锁** —— 它是流深度最大（**921600**）的 IP，这条最有价值 |
| `rgb2gray` | ✅ **PASS**（配套数据后） | 用 3/8（720p60 档）小尺寸数据：Layer 2 `10/10, 0 mismatched pixels`、**无死锁** |
| `fir_filter` | ✅ **PASS**（30 Hz 档，与当前默认系数表配套） | Layer 1 `8/8`、Layer 2 `17/17`、`0 不一致`、**无死锁**；45 Hz 档的 cosim 亦早已 PASS（2026-09-13） |

> 📌 **cosim 与 csim 是两种检查，别互相替代**：`csim` 不建模 `hls::stream` 的 FIFO 深度。
> 本轮 cosim **实际报出过** `hls::stream ... contains leftover data, which may result in
> RTL simulation hanging` —— 那正是只有 cosim 会说的话（该例成因是数据口径不匹配，见下）。

### 5.1 三个曾经的 FAIL，成因全是"数据与档位不配套"，不是 IP 缺陷

**① `rgb2gray`（已复跑 PASS）**：`data_motion_small` 是 **`decim=3/5`**（`out=48x36, gray_bytes=1728`），
我却按 720p60 的 **3/8** 编译 ⇒ 输出只有 1152 B ⇒ 测试台按 1728 B 读，
报 `leftover data` + `Layer 2: 0 passed, 10 failed`。
**复跑**：用与 3/8 配套的小尺寸数据（80×64 → 30×24）⇒ `RESULT: PASS` + `cosim PASS`。

**② `fir_filter`（已复跑 PASS）**：`data_fir_small/meta.txt` 是 **`fs=45`**，而仓库当前默认系数表是 **30 Hz**
（`docs/interface.md` v1.1 冻结 45、v1.2 草案改 30 ⇒ **`data_fir` 是 30、`data_fir_small` 是 45，两份不同档**）
⇒ Layer 2 `1 passed, 16 failed (样本不一致 1055)`。
**复跑**：用 `gen_fir_vectors.py --out-dir ... --scale 0.25`（它**解析当前** `fir_coeffs_q15.h`）
生成与当前 30 Hz 表配套的小尺寸数据（meta 实测 `fs=30`、numpy 对拍 1087 样本逐样本相等）
⇒ `RESULT: PASS` + **`cosim PASS`**。另：`c5_fir_filter_45hz_reserved.md` 记载 45 Hz 档 cosim
于 2026-09-13 亦 PASS ⇒ **两种档位都有 cosim 交代**。

### 5.2 教训（与本轮 §4.1 的 II 误判同源）

**"数据/配置配错"和"IP 算错"的症状高度重合**：都是逐字节不符 + 流告警。
本轮已因此误判过一次（`raw10_unpack` 的 II）。
**所以任何一次 FAIL，先核对三件事**：`meta.txt` 的抽取比/采样率、
跑测试时用的 `VIGILENS_TIER`/`FIR_FS`、以及黄金参考是哪一档生成的。

### 5.3 跑 cosim 的操作要点（本轮踩出来的）

1. **用 `Start-Process ... -PassThru` 起成独立进程**，不要靠工具调用的后台。
   实测两次 `frame_scale` cosim 都在**工具超时那一刻**被杀，而日志显示 RTL 已跑到 **6/8 事务** ——
   "进程消失"是**工具超时连带杀掉子进程**，不是环境或 IP 的问题。
2. **看日志文件，不要用 `| Select-Object -Last N`**（它要等命令结束才输出）。
3. 日志里 `// RTL Simulation : k / 8` 是**进度条**，看到它在涨就说明没死锁。

---

## 5.4 补充（2026-10-01）：`roi_statistic` 在 **1280×720 与 1920×1080** 两档口径下都跑通了

**为什么补这条**：v1.5 草案里"`roi_statistic` 按档位各存一套工作尺寸/黄金参考"一直没做，
而它在链路**最前面**（吃的是原始 RGB），是 v1.5 会签时最容易被打回来的缺口。现已**两档都补上**：

```powershell
# 1) 生成向量 + 黄金参考（numpy 独立对拍 PASS，45 项）
python fpga/sim/gen_frames.py --width 1280 --height 720  --out-dir D:\Desktop\AMD\_roi720
python fpga/sim/gen_frames.py --width 1920 --height 1080 --out-dir D:\Desktop\AMD\_roi1080

# 2) csim + csynth（逐档）
cd fpga
$env:HLS_IP='roi_statistic'; $env:ROI_DATA_DIR='D:\Desktop\AMD\_roi720'   # 或 _roi1080
vitis-run --mode hls --tcl run_hls.tcl
```

**实测结果**：

| 口径 | Layer 1 | Layer 2 | meta | csynth | 用时 |
|---|---|---|---|---|---|
| **1280×720** | **28 / 28** | **45 / 45**（0 不符） | `1280x720, 5 frames, 2764800 bytes/frame, 45 golden rows` | **II=1、Fmax 138.99 MHz** | 92 s |
| **1920×1080** | **28 / 28** | **45 / 45**（0 不符） | `1920x1080, 5 frames, 6220800 bytes/frame, 45 golden rows` | **II=1、Fmax 138.99 MHz** | 126 s |

📌 **值得注意的一点**：`roi_statistic` 的 **Fmax 在 640×480 / 1280×720 / 1920×1080 三种口径下都是 138.99 MHz** ——
它是流式逐像素统计，**逻辑路径与帧尺寸无关** ⇒ **抬分辨率不改变它的时序结论**，
变的只是输入流长度（921600 → 2764800 → 6220800 B/帧）。

**而且资源也完全相同**（三种口径实测 csynth 表逐项相等）：

| 口径 | LUT | FF | BRAM18 | DSP | Fmax |
|---|---|---|---|---|---|
| 640×480 | 1267 | 723 | 0 | 1 | 138.99 MHz |
| 1280×720 | 1267 | 723 | 0 | 1 | 138.99 MHz |
| 1920×1080 | 1267 | 723 | 0 | 1 | 138.99 MHz |

⇒ **对 v1.5 有直接含义**：`roi_statistic`（以及任何"纯流式、不缓存整帧"的 IP）
**不需要"每档一套资源/时序表"**，只需要**每档一套黄金参考**。
真正需要按档重算的只有那些**片内缓存**与帧尺寸挂钩的 IP（如 `motion_quality` 的整帧缓存），
而 v1.5 的设计恰恰让两档都落在 480×270/2¹⁷ 同一个台阶里（64 个 BRAM18）——
所以**档位化在这个 IP 链路上几乎不增加硬件代价**，这条结论现在有三种口径的实测数据支撑。

### 5.4.1 `roi_statistic`@720p 的 cosim：**两道环境关卡**（2026-10-01 深挖，含一次更正）

> 🔴 **本节更正过两次**，把结论留在最后，因为"看起来像算力问题、其实是环境问题"这件事值得记。

**第一版（错）**：说它"73 个事务、本身是长作业"。**第二版（也错）**：说"超预算"。
**实际**：日志显示它**跑到过 `40 / 73` 事务**（超过一半）——**根本不是做不完**。

**真因是两道环境关卡**（绕开 `vitis-run`、直接跑 Vitis 生成的 `xsim` 后逐步逼出来的）：

**关卡一：xsim 默认多线程在本沙箱直接崩 —— 已找到绕法 ✅**
```
xelab: Multi-threading is on. Using 22 slave threads.
xsim : Exception at PC 0x00007FF9CFFF2165        (xsimcrash.log，裸访问违例)
       ERROR: [Simtcl 6-50] Simulation engine failed to start
```
**绕法：重建快照时加 `-mt off`**
```
xelab <...> -mt off -s <snapshot>
xsim <snapshot> -tclbatch <tb>.tcl
```
加完输出变成 `Turned off multi-threading.`，**引擎正常起来、跑到了 UVM 阶段**。
📌 **这条对任何人都有用**：本机跑 HLS cosim 若报 "shut down unexpectedly during initialization"，
**先试 `-mt off`**。

**关卡二：xsim 读不到 HDL 测试向量 —— 未绕过 ❌**
```
ERROR: File descriptor (0) passed to $fscanf is not valid.
UVM_FATAL ./file_agent/file_read_agent.sv(370) [file_rd_TVOUT_transaction_size]
```
从 `.run_sim.tcl` 读到：`set ap_argv {D:/Desktop/AMD/_roi720}`（数据目录这一环是对的），
但 cosim 还要求**先由 C 测试台写出 HDL 测试向量**（`gHdlTvIn`/`gHdlTvOut` 那套，`hls/sim/tv/` 下），
xsim 再用 `$fscanf` 读；而在 `fpga/` 下生成这些文件时撞上**该目录拒绝进程写入**
（同一条限制的原始报错是 `Could not open file xsim.dir/roi_statistic/xsim.type for writing`）。
⇒ 必须**完整权限终端**：① 让 `vitis-run` 写全 `tv/`；② 用 `-mt off` 起 xsim。

---

## 6. 未闭合项

- [x] `bayer_demosaic` 的 csim 失败 → **已修**（R 放回 byte0），复跑 3/3 + 5/5 PASS
- [x] `frame_scale` 的 csim 失败 → **已修**（测试台恒假断言），复跑 3/3 + 5/5 PASS
- [x] 这两个 IP 的 **csynth** → 已拿到资源/时序（见 §3 表）
- [x] `raw10_unpack` 的 II 问题 —— **查清后确认不是问题**（`II=4` = 一次迭代写 4 个像素到
      1 像素宽的流；Fmax 151.98 MHz ⇒ 152.0 Mpx/s，对 720p60 有 **2.75×**、1080p45 有 **1.63×** 余量）。
      顺手已把两层循环扁平化成单层（正确性不变、逐像素 0 不符）。见 §4.1
- [x] **cosim**（小尺寸向量）—— **7/7 全部 PASS、无死锁**，见 §5：
      `roi_statistic` / `motion_quality` / `raw10_unpack` / `bayer_demosaic` / `frame_scale` /
      `rgb2gray`(3/8 配套数据) / `fir_filter`(30 Hz 配套数据，45 Hz 档 2026-09-13 亦 PASS)。
      `frame_scale` 是流深度最大（**921600**）的 IP，它 **8/8 事务跑完且无死锁**最有价值。
- [x] **45 fps/1080p 与 60 fps/720p 两档的黄金参考与口径改造**（C 线侧已做，见 §5.4 与本文件 §3）：
      `rgb2gray` 抽取比**参数化**（3/5 缺省、3/8、1/4），既有 3/5 档**零回归**；
      两档黄金参考（480×270）生成 + numpy 对拍 PASS（两档 `motion_pixels` 均 **129,600**）；
      `rgb2gray` 两档 **csim/csynth PASS**（0 不符、II=1、140.81 MHz）；
      **`roi_statistic` 在 1280×720 与 1920×1080 下都 PASS**（两档均 28/28 + 45/45、0 不符、
      II=1、138.99 MHz，且**资源三种口径逐项相同**，见 §5.4）；
      `motion_quality` 两档 PASS（缩小尺寸）；`config.yaml` 档位键、`regmap.py` 按档推导均已验。
- [ ] ⚠️ **仍未做**：`roi_statistic` 720p 下的 **cosim**（测试台 73 个事务，本身是长作业，
      见 §5.4.1 —— 请在完整权限终端跑，数据 `_roi720` 已备好）；
      **480×270 全尺寸**的 `motion_quality` csim（本会话算不动，见 `docs/24` §8.2）
- [x] ⚠️ **需要拍板的那条已拍**：用户 2026-09-30 选 **(c) 两条都留、按档位切换** ——
      主/备两档走 `rgb2gray` 直抽（3/8、1/4），640×480 现行档保留 `docs/20` 的 `frame_scale` 路线。
      该决定已写入 `docs/interface.md` 头部 v1.5 补充块与 §6 的 2026-10-01 行（**草案，待 A/B 会签**）。

> ⚠️ 本文标 ❌ 的项**不得**在任何报告、答辩或对外材料里当作已完成成果引用（`AGENTS.md` §1.1）。
