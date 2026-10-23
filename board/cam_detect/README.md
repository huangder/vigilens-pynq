# board/cam_detect —— 上板测试前的两件工具

> C 线（`board/`）目录，2026-10-12 新增。解决两个具体问题：
> **① 板卡能不能自己判断「FFC 处的 MIPI 摄像头插上了没」**（程序一）；
> **② 用提前录好的影像直接给现有程序做功能测试**（程序二）。
>
> ⚠️ **本目录的所有 RTL 从未上板，也从未综合过** —— 只做过 `xvlog` 语法检查与
> `xsim` 行为仿真（三段场景，`RESULT: PASS`）。**不要**把这里的结果说成"上板验证过"。
> 与归档的 `board/imx219_probe/`（2026-10-11 那次真机实验，**不许改动**）并列，是它的后继。

---

## 0. 一句话结论（先看这个）

| 程序 | 干什么 | 现在的状态 |
|---|---|---|
| **程序一** `pl_cam_detect.v` | 纯 PL 的 SCCB 主控：扫地址 → 谁应答就登记 → 读 MODEL_ID → 测 CAM_CLK 频率，全部塞进 4 条 ILA 探针 | 【已验证】仿真 PASS；【未验证】真机（**位流没建过**） |
| **程序一** `board/check_camera.py` | 把 ILA 导出的 CSV 判读成「连上 / 没连上 / 没时钟 / 总线没器件」 | 【已验证】`--selftest` = **PASS (9/9)** |
| **程序二** `board/cam_detect/inject_video.py` | 录好的 `.mp4` → 板卡与 PC 都能吃的测试向量（RGB888 帧序列 + 黄金参考 + 灰度序列） | 【已验证】`--selftest` = **PASS (17/17)**，并在真录像 `data/raw/blink.mp4` 上实跑通过 |
| **程序二** 板卡侧比对 | `board/hw_sw_compare.py --data-root <向量目录>` | 【未验证】**依赖 `board/build_bd.tcl` 建的像素链 overlay，而它从未跑通**（`board/bitstream/` 仍为空） |
| **程序二** PC 侧功能测试 | 现成的 `backend/run_pipeline.py --source <录像>` | 【已验证】真录像跑通，见 §3.3 |

---

## 1. 文件清单

| 文件 | 作用 |
|---|---|
| `pl_cam_detect.v` | 探针 RTL（616 行）。参数 `CLK_HZ=50_000_000 / SCL_HZ=100_000 / PWRUP_MS=200 / CAM_ADDR=7'h10 / DRIVE_MCLK=0 / MEAS_N=4096` |
| `pl_cam_detect_dbg.v` | ILA 包装层（唯一的开关是 `localparam DRIVE_MCLK_SEL`，默认 `1'b0` = 不驱 CAM_CLK） |
| `cam_detect_pins.xdc` | 引脚约束（`clk=H16 / rst_n=R19 / mclk=U9 / sccb_scl=A20 / sccb_sda=E17 / led[0..3]=G14,C20,B20,H17`） |
| `build_dbg.tcl` | 建带 ILA 的位流（Vivado 2026.1，`xc7z020clg400-1`），产物落 `_cam_build/cam_detect_dbg/` |
| `read_ila.tcl` | 编程板卡 → 等 2 s → `trigger_now` → 导出 `cam_detect_ila.csv` |
| `inject_video.py` | 程序二：录像 → 测试向量（**复用** `board/regmap.py` + `fpga/sim/gen_frames.py` + `fpga/sim/gen_motion_vectors.py`，不重写算法） |
| `board/check_camera.py` | 程序一的判读器（父目录，与 `board/` 其他上板脚本平级） |
| `cam_detect_top.v` | ⚠️ **早期草稿，已放弃**：它走 UART 上报，但板上 USB-UART 接在 PS 的 MIO14/MIO15，**PL 拿不到**，遂改为 JTAG+ILA。没有任何脚本引用它，**别拿它综合**（受限 shell 删不掉，请手动删） |

---

## 2. 程序一：检测 FFC 处的 MIPI 摄像头到底连上没有

### 2.1 它按四层回答，不猜一句话

| 层 | 问题 | 依据 |
|---|---|---|
| A | **PL 的 SCCB 主控 + 板上这一段总线好不好？** | 板载 24C32 EEPROM 有没有 ACK |
| B | **相机那一段（排线 + 模组）有没有东西在应答？** | 除 EEPROM 外，有没有别的 8 位写地址被 ACK |
| C | **应答的是不是 IMX219？** | 两种寄存器地址宽度各读一遍 0x00~0x03，看 MODEL_ID 是不是 `0x02 0x19` |
| D | **相机拿到时钟了吗？** | `CAM_CLK`(pin12/U9) 上实测频率 |

流程（上电即自动开跑，整轮约 **0.23 s**）：`IDLE → PWRUP(200 ms) → SCAN(128 个 8 位写地址) → 记录命中器件 → 读相机 16 位地址口径 → 读相机 8 位地址口径 → DONE`。

- **只读不写**：读的全是 IMX219 的只读寄存器（0x00~0x03），**不写任何寄存器**，不会碰板载 EEPROM 的内容。
- `hit_a[]` 记的是**总线上真正被 ACK 的 8 位写地址**（升序）：相机若是 7bit `0x10` 就会记成 `0x20`，板载 EEPROM 记成 `0xA0`/`0xB0` —— 所以**相机一定落在 `hit_a[0]`**。
- `rec0/rec1` = 前两个"8 位地址口径"器件的寄存器 0x00~0x03（**大端**：第 0 字节在 `[31:24]`）。

### 2.2 上板三步

```bat
:: 1) 建带 ILA 的位流（**完整权限**终端；几分钟）
cd /d D:\Desktop\AMD\board\cam_detect
vivado -mode batch -source build_dbg.tcl
::    成功标志：最后打印 === DBG_BUILD_ALL_DONE ===
::    产物：..\..\_cam_build\cam_detect_dbg\cam_detect_dbg\cam_detect_dbg.runs\impl_1\pl_cam_detect_dbg.bit
::          ..\..\_cam_build\cam_detect_dbg\cam_detect_dbg.ltx

:: 2) 上板：编程 + 触发 + 导出 CSV（板卡上电、USB-JTAG 已连）
vivado -mode batch -source read_ila.tcl
::    成功标志：打印 === READ_ILA_END ===，并出现 ..\..\_cam_build\cam_detect_dbg\cam_detect_ila.csv

:: 3) PC 侧判读（**结论只在这一步给出**，不要在 Vivado 里肉眼判）
cd /d D:\Desktop\AMD
.\.venv\Scripts\python.exe board\check_camera.py _cam_build\cam_detect_dbg\cam_detect_ila.csv
::    想留证据：加 --json _cam_build\cam_detect_dbg\verdict.json
::    想看原始采样：加 --raw
```

### 2.3 判据表（`check_camera.py` 的五种结论）

| 结论 | 含义 | 退出码 | 下一步 |
|---|---|---|---|
| `CONNECTED_OK` | 相机 ACK 了 0x20，MODEL_ID = `0x0219`，且有时钟 | **0** | 相机链路通；可以去做像素链 |
| `CONNECTED_NO_MODEL` | 0x20 有 ACK，但 MODEL_ID 不是 0x0219 | 1 | 有器件在应答但不是 IMX219（或寄存器地址口径不同） |
| `CAMERA_NOT_ACK` | 只有 EEPROM 应答，0x20 没人理 | 1 | 相机没接好/没供电/排线没插到底 → 见 §4 人工三根线 |
| `CAMERA_NO_CLOCK` | 0x20 有 ACK 但 `CAM_CLK` 一个沿都没测到 | 1 | 时钟没到相机（R156/晶振/排线），相机不会工作 |
| `BUS_NO_DEVICE` | 连 EEPROM 都没应答 | 1 | **PL 侧的 SCCB 也没动** ⇒ 先怀疑位流没编进去/引脚错，而不是相机 |

> 退出码 0 **仅当** `CONNECTED_OK`，可以直接接在 CI/脚本里。

### 2.4 板上的四颗 LED（低电平点亮）

| LED | 含义 |
|---|---|
| D6 `led[0]` | 心跳（约 3 Hz） |
| D7 `led[1]` | `i2c_act`：SCCB 有活动 |
| **D8 `led[2]`** | **`cam_ack`：相机地址真被应答过**（最要紧的一颗，不用电脑就能看） |
| D9 `led[3]` | `step == DONE`：整轮跑完了 |

---

## 3. 程序二：用提前录好的影像做功能测试

一句话：**录像不再"只是喂给 PC 看"，而是变成板卡与 PC 都能吃的、带黄金参考的测试向量。**

### 3.1 录像 → 向量

```bat
cd /d D:\Desktop\AMD
.\.venv\Scripts\python.exe board\cam_detect\inject_video.py --source data\raw\blink.mp4 --frames 10
::    默认产物目录 _cam_build\vec_blink\ ；也可以 --out-dir 指定
::    档位 --tier 640x480（默认）/ 720p60 / 1080p45，取值来自 board/regmap.py
::    运动阈值默认读 config.yaml 的 motion_thresh_gray（现在是 16），可用 --motion-thresh 覆盖
```

产物（形状**刻意与 `board/hw_sw_compare.py --data-root` 要求一致**）：

```
<out>/data/frames.bin                     N 帧 × 921600（RGB888，契约布局 bytes[frame*W*H*3 + 3*(y*W+x) + c]）
<out>/data/golden_roi.csv                 3 行 # + 表头 + 9 用例 × 5 帧 = 45 行
<out>/data_motion/rgb_frames.bin          N 帧 × 921600（运动量用）
<out>/data_motion/gray.bin                N 帧 × GRAY_PIXELS（640×480 档 = 110592，384×288）
<out>/data_motion/golden_motion.csv       4 行 # + 表头 + 帧 1..9 共 9 行（第 0 帧刻意排除）
<out>/data_motion/meta.txt                键值对（含 gray_formula / decim）
<out>/frame0.rgb  <out>/frame0.gray       单帧样本（bringup_check.py / dma_test.py 直接吃）
<out>/manifest.txt                        帧数 + frames.bin/gray.bin 的 SHA256（**不含时间戳** ⇒ 同输入同产物）
```

**确定性**：同一段录像跑两次，产物逐字节相同（自检 T6.8 就是在钉这一条）。

### 3.2 两条使用路线

```bat
:: A) 板卡侧：把向量灌进 PL 像素链，与软黄金参考逐字节对拍
.\.venv\Scripts\python.exe board\hw_sw_compare.py --bit system.bit --data-root _cam_build\vec_blink
::    ⚠️ 这一步需要 board/build_bd.tcl 建出的**像素链 overlay**（PYNQ 通路），
::       而它**从未跑通**（board/bitstream/ 为空）⇒ 目前【未验证】，等 overlay 建好再跑。

:: B) PC 侧：现在就可用，验"算法/管线/网页"这条链
.\.venv\Scripts\python.exe backend\run_pipeline.py --source data\raw\blink.mp4 --seconds 5
.\.venv\Scripts\python.exe metrics\scripts\run_demo.py --source data\raw\blink.mp4 --loop   :: 带网页
```

### 3.3 已跑过的真实证据（2026-10-12，本机）

- `inject_video.py --selftest` → **RESULT: PASS (17/17)**（含"用 LCG 帧复算入库黄金参考、容差 0"）。
- `inject_video.py --source data/raw/blink.mp4 --frames 10` → **RESULT: PASS**，产物
  `frames.bin 9216000`、`gray.bin 1105920`、`golden_roi.csv 45 行`、`golden_motion.csv 9 行`、`meta.txt 224 B`。
- `run_pipeline.py --source data/raw/blink.mp4 --seconds 5 --stub` → **退出码 0**，处理 150 帧，
  状态分布 `normal=127 / fatigue_risk=23`，末帧质量 `overall 0.89 (light 0.82 / motion 0.03)`。
  ⚠️ **`landmark_source = stub`**：EAR/MAR 是**占位几何量**，不是算法结果 —— 按 AGENTS.md §9，
  这种运行的指标**不得**作为算法数字引用（要真数字得先装 mediapipe 去掉 `--stub`）。

---

## 4. 人工三根线（万用表要做的三件事，探针做不到）

> 探针只能看**信号**（SCCB / CAM_CLK），看不到供电、看不到座子里的通断、也不知道 pin11 接哪。
> ⚠️ 顺序：**先断电量通断，再上电量电压**；不要在上电状态下用电阻档去戳信号脚。

| # | 量什么 | 怎么量 | 为什么必须人工 |
|---|---|---|---|
| 1 | **相机模组的供电与地** | 上电后，直流电压档在**相机模组侧**量模组电源脚对地 | 【不确定】`docs/31 §2` 的 FFC 表只列了 pin11~15 的**信号名**，仓库里查不到电源脚编号；模组没电时 SCCB 必然不应答，会被判成 `CAMERA_NOT_ACK`，**看起来像"没插好"** |
| 2 | **pin11 `CAM_GPIO` 的 ball 号** | 断电，蜂鸣档在 FPGA 侧与座子 pin11 之间找通断（或翻原理图） | 【不确定】**全仓库（含 `docs/11`/`docs/12`/`docs/31`）都查不到它的 ball 号** ⇒ 按 AGENTS.md 不许猜，故 XDC 里**不约束**这个脚 |
| 3 | **排线/座子通断** | 断电，蜂鸣档量座子 pin12↔U9、pin13↔A20、pin14↔E17，以及排线两端对应脚 | 排线没插到底/插反时，症状与"摄像头没连"**完全一样**，只有通断能区分 |

---

## 5. 已知风险与不确定性（不许当成已解决）

1. **从未上板、从未综合**：`pl_cam_detect.v` 只过了 `xvlog` + `xsim`（三场景 PASS）。
   `build_dbg.tcl` / `read_ila.tcl` **一次都没执行过** ⇒ 首次真机动作可能撞上 Vivado 版本差异
   （如 `ila` IP 的 `CONFIG` 名称），**留好日志**。
2. **CAM_CLK 是 50 MHz，超 IMX219 规格**：板上 U19 的 50 MHz 经 R156 直驱 pin12，而 IMX219 的
   INCK 规格是 **6~27 MHz**（`docs/31 §2.3`）⇒ 这是"相机没反应"的**头号嫌疑**。本探针
   `DRIVE_MCLK=0` **只测不驱**（避免与晶振两源对打），把实测频率暴露在 `dbg1.mclk_period` 里：
   `f = MEAS_N * 50 MHz / mclk_period`（50 MHz ⇒ 4096，24 MHz ⇒ 8533；饱和 0xFFFF ⇒ < 3.1 MHz）。
3. **"总线上有人应答" ≠ "相机在"**：SCCB 两条线与板载 24C32 EEPROM **共用**，板上已有 4.7K 上拉。
   必须看 `0x20` 这个地址（`check_camera.py` 已按此判）。
4. **ILA 只有 2048 拍 @50 MHz = 41 µs，抓不住 0.23 s 的整轮过程**：靠的是"跑完后 `dbg*` 静止"
   ⇒ `read_ila.tcl` 先编程、等 2 s、再 `trigger_now`。`check_camera.py` 另外会在所有行里挑
   "`step=DONE` 且 `hit_n` 最大"的一档，防止将来改成持续刷新时读错。
5. **不解决 IMX219 寄存器表缺失问题**：本探针只读 0x00~0x03，**不写寄存器**，所以
   `docs/27 §1.1` 那个"初始化序列无出处"的 BLOCKER 依然在（`board/imx219_driver.py` 仍拒绝下装）。
6. **程序二的板卡侧通路依赖不存在的 overlay**：见 §3.2 A 的警告。

---

## 6. 与既有资产的关系

- **`board/imx219_probe/`（2026-10-11 归档）一个字都没动**。本目录是它的后继：那份探针只报
  "扫到几个地址 + 头两个命中地址 + MODEL_ID"，`0x50/0x58` 分不清是谁，而且**没测 MCLK** ⇒
  上一轮的负面结论**无法区分**「没插好 / 没供电 / 没时钟」；本设计把四件事一次测完。
- 判读沿用它的 **JTAG + ILA** 路线（板上 USB-UART 在 PS 的 MIO14/MIO15，PL 拿不到裸 GPIO UART）。
- ILA CSV 格式与归档一致（第 1 行探针名、第 2 行 radix、第 3 行起 `采样号,窗口号,TRIGGER,各字段`），
  所以 `check_camera.py` 也读得动归档 CSV —— 但归档位流的探针里没有本设计的新字段，
  它会明确打印"逐信号表头里认不出这些字段"并给 `RESULT: FAIL（没能判读）`，**不会抛栈、不会瞎猜**。

---

## 7. 待办

1. **上板（第一次）**：跑 §2.2 三步，把 `cam_detect_ila.csv` 与判读结论一起归档。
2. **把两个自检登记进回归**（现在是"能跑但没人自动跑"）：在
   `metrics/scripts/check_all.py` 的 `TOOL_SELFTESTS` 里各加一行
   `("board/check_camera.py", "--selftest")` 与 `("board/cam_detect/inject_video.py", "--selftest")`，
   并同步 `AGENTS.md §6.1`、`docs/29 §1` 的"工具自检 **14/14**"基线 ⇒
   这是**共享文件 + 共享文档**改动，按 AGENTS.md §5 先公告再改。
3. **程序二的板卡侧**要等 `board/build_bd.tcl` 的像素链 overlay 跑通。
4. **LLM 协作记录**：`report/llm_log/2026-10-12_*.md` **必须人类亲手写**（含自我纠错轨迹），AI 不得代写 —— 这是赛制必交项。
5. 手动删掉 §1 表里那份已放弃的 `cam_detect_top.v`。
