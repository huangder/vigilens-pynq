# board/imx219_probe —— 「相机存活实验」探针（纯 PL）

> 归档自 2026-10-11 的实机实验现场（原开发目录 `_cam_build/`，被 `.gitignore` 的 `/_*` 挡住）。
> **本目录的文件是原样归档的**：与产生 `ila_capture.csv` 的那一版完全一致，归档时未改逻辑。
> 完整实验记录、判定与未闭合项见 **`docs/31_相机存活实验上板记录_20261011.md`**。

## 0. 这是什么

回答四个问题的一小块 PL 逻辑，**不进 Block Design、不启 PS、不用 DDR**（与 S1 的
`board/openmv/pl_uart_echo.v` 同一套纪律）：

1. IMX219 排线插对没有？
2. 3.3V 有没有？
3. MCLK 有没有？
4. SCCB(I2C) 通不通？

**判据**：读到 IMX219 chip ID —— `reg 0x0000 = 0x02`、`reg 0x0001 = 0x19`。
结果原本按 ASCII 打在 115200 8N1 的 UART 上；因为板载 USB-UART 接的是 PS 侧 MIO，
拿不到 PL 的裸 GPIO UART，本次改用 **JTAG + ILA 读回**。

**当前结论：未通过 —— IMX219 全程未应答。** 详见 `docs/31`。

## 1. 文件

| 文件 | 作用 |
|---|---|
| `pl_imx219_probe.v` | 探针主体：SCCB 位翻转主机 + 全总线扫描 + 双地址宽度寄存器读 + UART 报文 + dbg 口 |
| `pl_imx219_probe_dbg.v` | 在探针外面套一层 `ila_0`，把 dbg 口接到 JTAG 可读的探针上 |
| `tb_pl_imx219_probe.v` | 自检 TB：内建 IMX219 从机模型 + UART 解码 + 报文断言 |
| `probe_pins.xdc` | 引脚约束（主时钟/LED/UART 照抄 `board/openmv/mizar_z7_openmv_uart.xdc`） |
| `run_sim.bat` | 跑仿真（`xvlog` → `xelab` → `xsim`） |
| `syn_probe.tcl` | 综合 + 实现 + 出 `.bit` 与 util/timing 报告（**不带 ILA**） |
| `build_dbg.tcl` | 带 ILA 的工程式构建（`create_ip ila` + `write_debug_probes` → `.ltx`） |
| `read_ila.tcl` | 烧 ILA 版 + `trigger_now` + 上传，导出 `ila_capture.csv` |
| `program_probe.tcl` | 只烧不带 ILA 的那版（现场快速验证） |
| `ila_capture.csv` | **原始实测证据**：1024 个采样（导出格式，未编辑过） |

## 2. 关键前提（踩过坑才写在这里）

- **MCLK 由板上晶振驱动，不是 PL。** 原理图上 `U19 (50M_OSC)` 经 `R156 33R` 直驱
  连接器 pin 12（`CAM_CLK`）。所以 RTL 用 `parameter integer DRIVE_MCLK = 0`
  把 `mclk` 置高阻，避免两源对打。**若确认 `R156` 未贴装 / `U19` 无输出**，
  把 `DRIVE_MCLK` 改成 `1`（PL 输出 50 MHz 二分频 = 25 MHz，落在 IMX219 的 6~27 MHz 规格内）。
- **`sccb_scl` / `sccb_sda` 与板载 24C32 EEPROM 共总线**，板上已有 4.7K 上拉。
  所以扫描**一定会**命中 `0xA0`（EEPROM）——那是主机侧的自检对照组，不是相机。
- **Vivado BASIC 授权下 ILA 最多 5 个 probe**（`ERROR: [Chipscope 16-620]`），
  所以 `dbg_sq` 与 `dbg_hit_n` 拼成了一个 7-bit probe。
- **`write_hw_ila_data` 覆盖已有 CSV 必须加 `-force`**（`ERROR: [Labtoolstcl 44-54]`）。
- `xelab` **不要**加 `-debug typical`：快照会让 xsim 崩在
  `Could not open file mapping object (2).` + `EXCEPTION_ACCESS_VIOLATION`。
- **`.bat` 必须纯 ASCII**（cmd 按 CP936 解码，中文注释会让脚本失效）。

## 3. 怎么跑

⚠️ **`syn_probe.tcl` / `build_dbg.tcl` / `read_ila.tcl` / `program_probe.tcl`
里的路径是原样的绝对路径 `D:/Desktop/AMD/_cam_build/...`**（归档时未改，以保持
「这一版就是产生那次结果的那一版」）。换机器或换目录要先改这几个 `set OUT` / `set PROJECT_DIR`。
`pl_imx219_probe.v`、`pl_imx219_probe_dbg.v`、`tb_pl_imx219_probe.v`、`probe_pins.xdc`
本身不含路径，可直接复用。

```powershell
# ① 仿真（自检，不需要板卡）
cd board\imx219_probe
cmd /c "call D:\Xilinx\2026.1\Vivado\settings64.bat && run_sim.bat"
#    期望末行：==== RESULT: PASS ====

# ② 综合 + 实现 + 出 bitstream（不需要板卡）
cmd /c "call D:\Xilinx\2026.1\Vivado\settings64.bat && vivado -mode batch -source syn_probe.tcl"

# ③ 带 ILA 构建 + JTAG 读回（需要板卡上电 + JTAG 连着）
cmd /c "call D:\Xilinx\2026.1\Vivado\settings64.bat && vivado -mode batch -source build_dbg.tcl"
cmd /c "call D:\Xilinx\2026.1\Vivado\settings64.bat && vivado -mode batch -source read_ila.tcl"
```

> ⚠️ 在受限沙箱里跑 Vivado 会失败 —— 它要写
> `C:\Users\<用户>\AppData\Roaming\Xilinx\...`，被拒后 IP 目录为空，
> `create_ip` 建不出 ILA（`WARNING: [Ipptcl 7-1638] No Catalog IPs found`）。
> **用完整权限终端跑**。

## 4. 引脚（`probe_pins.xdc` 摘要）

| 信号 | 球号 | 说明 |
|---|---|---|
| `clk` | `H16` | PL 侧 50 MHz 有源晶振，`create_clock -period 20.000` |
| `rst_n` | `R19` | PL_KEY1（按下为低） |
| `uart_tx` | `H15` | JP2 pin 7（裸 GPIO，主机够不着） |
| `led[0..3]` | `G14` `C20` `B20` `H17` | 低电平点亮 |
| `mclk` | `U9` | → 连接器 pin 12 `CAM_CLK` |
| `sccb_scl` | `A20` | → 连接器 pin 13 `CAM_SCL` |
| `sccb_sda` | `E17` | → 连接器 pin 14 `CAM_SDA` |

## 5. 诚实边界

- 本探针**只读不写**：不配置相机、不修改任何寄存器 ⇒ 不可能把相机配坏。
- IMX219 的 7-bit SCCB 地址 `0x10` 与「16-bit 寄存器地址」两条口径，按
  `board/imx219_driver.py` 的登记都还是 **UNVERIFIED**。所以本设计**扫全总线
  0x00~0x7F**，并且**两种寄存器地址宽度各读一遍**，把「到底哪一种」变成一次实测
  而不是一个假设。
- 仿真通过**只能证明 RTL 的时序逻辑自洽**，它用的从机模型是我自己写的 ——
  **不等于**真相机能应答。真相机的结果以 `ila_capture.csv` 为准。
