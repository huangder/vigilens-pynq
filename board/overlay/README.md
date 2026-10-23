# `board/overlay/` —— PS 侧怎么把 overlay 加载起来、怎么访问寄存器

> 本文件回答 `docs/38` §3.1 **C5**（"overlay 的 PS 侧交付"）：`load_overlay.py` 只是"加载之后怎么用"，
> 这里补上"**在哪种运行环境里、用什么命令把 bitstream 送进 PL**"。
> 维护：C 线。最后核对 **2026-10-23**。

## 0. 先把话说清楚：哪条路是验证过的

| 路径 | 载体 | 状态 | 依据 |
|---|---|---|---|
| **裸机固件 + `xsdb`（JTAG）** | 无 OS，Cortex-A9 裸跑 | ✅ **本板唯一已验证** | `docs/36` §3；`fpga/report/logs/` 的 S4 记录；门限 3/4/5/6/8 全部由此产生 |
| **PYNQ `Overlay()`** | PYNQ 镜像（Linux） | 🚧 **未上板验证**（板上没有 PYNQ 镜像） | `load_overlay.py`；本文 §2 |
| **普通 Linux + `/dev/mem` mmap** | 任意 Zynq Linux | 🚧 **只验了离线那一半**（16 项自检 PASS） | `mmap_overlay.py`；本文 §3 |
| **PetaLinux + `fpga_manager`** | PetaLinux 镜像 | ❌ **不存在产物**（W7 未开工） | `docs/37` R9；`docs/38` §3.2 W7；本文 §4 |

⚠️ **Mizar-Z7020 现在没有 Linux**：`docs/36` §3 的现场记录是"**板上没有 Linux/PYNQ，COM11 无输出**"，
所以 §2/§3/§4 的全部命令**都没有在本板上真跑过**，一律按【未验证】引用，**不得**写成"已跑通"。
`docs/38` 给 C5 的完成判据是"文档 + **一条真跑通的命令**"——前一半本文交付，后一半**卡在 W7**。

## 1. 已验证的那条：裸机 + `xsdb`

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
xsdb
```

```tcl
connect
targets -set -filter {name =~ "*xc7z020*"}
fpga -file D:/Desktop/AMD/board/bitstream/system_wrapper.bit
targets -set -filter {name =~ "*Cortex-A9*#0"}
stop
source .tmp/s4/ps7_init.tcl      ;# 由 system.hdf 解出（ps7_init.tcl 在 HDF 根目录）
ps7_init
ps7_post_config
con                              ;# 从 0x00100000 起跑裸机固件
```

- 上面的顺序不是抄来的，是 `docs/36` §5 踩出来的：**必须先 `rst -dap` 清 AHB-AP sticky error**、
  `ps7_init` 中途被打断就只能**断电**、`mwr` 写 PL 地址要 `-force`。
- 全流程已封装成 `.tmp/s4/s4_run.py`（未入库，属一次性取证工具），阶段：`init/recm/long/full/mini/
  sinkfree/dec/roionly/probe`。
- **它只适合取证，不适合当产品路径**：没有文件系统、没有进程、改一个参数要重烧一遍。

## 2. PYNQ 路径（`load_overlay.py`，**未上板验证**）

前提是板上**真的有** PYNQ：

```bash
python3 -c "import pynq; print(pynq.__version__)"
```

- **有** → `load_overlay.py` 的 `load()` 可用。
- **没有** → 走 §3 的 mmap 回退（`load()` 会在 import 处直接失败，不会静默降级）。

PYNQ 的 `Overlay()` **要求 `.bit` 旁边有同名 `.hwh`**（没有它，`ip_dict` 是空的，按名字检索全部失败）。
本仓库的 `.hwh` 就在 `.hdf` 里 —— 【已验证】`board/bitstream/system.hdf` 是个 zip，成员含
`system.hwh`、`ps7_init.tcl`、`hwdef.xml` 与四个 HLS 的 `*_linux.c` 驱动：

```bash
python -c "import zipfile; zipfile.ZipFile('board/bitstream/system.hdf').extract('system.hwh','board/bitstream')"
python -c "import zipfile; zipfile.ZipFile('board/bitstream/system.hdf').extract('ps7_init.tcl','board/bitstream')"
```

板上用法（`docs/30` §7 的三脚本都走它）：

```bash
cd /home/xilinx/vigilens/board            # system.bit + system.hwh + regmap.py + overlay/
python dma_test.py --bit system.bit --frames 300 --golden-gray ../fpga/sim/data_motion/gray.bin
```

⚠️ 两个必须在板上核的点（`load_overlay.py` 头部已标【未验证】）：**DMA 的 arm 顺序**与
**`axi_fifo_mm_s` 的寄存器语义**。板上一验就知道，别在本机猜。

## 3. 普通 Linux（没有 PYNQ）：`/dev/mem` mmap

`mmap_overlay.py` 用 `/dev/mem` 直接 mmap AXI-Lite，**覆盖范围只到寄存器**：

```bash
python3 board/overlay/mmap_overlay.py --selftest        # 离线：16 项，PASS 16（2026-10-23 本机复跑）
```

- ✅ **已有**：`/dev/mem` mmap 的读写原语（`read_reg` / `write_reg` / `ap_*`）+ 从 `.hwh` 解析地址映射 + 16 项离线自检。
- ❌ **还没接**：`bringup_check.py` 的 `load()` 走的是 `overlay.load_overlay`（= PYNQ），
  **没有接到 mmap 路径**。所以"普通 Linux 上跑寄存器 smoke"这件事**现在做不到**，
  要补的是 `bringup_check.py` 的 mmap 分支（同 C5 的下一半，不另开条目）。
- ❌ **不能**验：`run_pixel_chain()` / `run_fir_segment()` —— DMA 与 FIFO 要**物理连续内存 + 缓存一致性**，
  纯 `/dev/mem` 做不了；得用 UIO + CMA，或内核的 `xilinx_dma` 驱动。

## 4. PetaLinux：`fpga_manager` 加载（**W7 未开工，本节全部【未验证】**）

PetaLinux 下 PL 由内核的 `fpga_manager` 管，**不接受 `.bit`**：Zynq-7000 只吃**去掉头部的 bin**。
这条是 Xilinx 文档口径（`bootgen -process_bitstream bin`），本仓库**没有跑过**，故标【推测】。

```bash
# 1) bit → bit.bin（在 Vivado 的 bin 目录里跑）
cat > system.bif <<'EOF'
all:
{
    board/bitstream/system_wrapper.bit
}
EOF
bootgen -arch zynq -image system.bif -w on -o system.bit.bin
#    （2026.1 的 bootgen 也接受 `-process_bitstream bin -image system_wrapper.bit -o system.bit.bin`）

# 2) 放进固件搜索路径，并让设备树指到同一个名字
cp system.bit.bin /lib/firmware/
#   设备树里 PL 区域需要：firmware-name = "system.bit.bin";  status = "okay";

# 3) 运行时装一次（设备树里已有 firmware-name 时，内核启动即自动加载）
echo 0 > /sys/class/fpga_manager/fpga0/flags
cat /sys/class/fpga_manager/fpga0/state      # 期望：operating
dmesg | grep -i fpga                          # 期望：fpga_manager fpga0: ... loaded from system.bit.bin
```

判据（按顺序，前一条不过就别看后一条）：

1. `/sys/class/fpga_manager/fpga0/state` 读回 **`operating`**（不是 `unknown`，也不是 `operating` 之前就报错）。
2. `dmesg` 里**没有** `fpga_manager: ... failed`；`firmware-name` 拼错是最常见原因。
3. 之后 AXI-Lite 能读到 IP 的 `CTRL=0x0`（空闲）而不是 `0xFFFFFFFF`（总线超时）——
   这一步等价于 §3 的 `bringup_check.py` 寄存器 smoke。
4. DMA 需要**另外**的 DT 节点与 `xilinx_dma` 驱动 + CMA 预留（`cma=128M` 量级）。**没有它就只到"寄存器能读"**，
   跑不了 `run_pixel_chain()`。

⚠️ **三条硬阻塞**（`docs/38` §3.2）：

- 仓库里**没有** `BOOT.BIN` / `image.ub` / `.dts`；
- **MicroPhase 是否给 Mizar-Z7 提供 PetaLinux BSP —— 【未知】**（`docs/37` R9）；
- 板上 USB Host 是 `USB3320C`（ULPI→PS MIO），所以"接 USB 相机"**必须先把 PS 跑起来**，绕不过去。

## 5. 三路径能力对照

| 能力 | 裸机 + `xsdb` ✅ | PYNQ `Overlay()` 🚧 | Linux mmap 🚧（半） | PetaLinux ❌ |
|---|---|---|---|---|
| 加载 bitstream | `fpga -file` | `Overlay(bit)` | 需先由别的手段烧好 | `fpga_manager` |
| AXI-Lite 寄存器 | ✅（`mrd/mwr`） | ✅（`MMIO`） | ✅（`/dev/mem`） | ✅（uio 或 `/dev/mem`） |
| DMA 像素链 | ✅（固件里写的） | ✅（`pynq.allocate`） | ❌ **未覆盖** | ❓ 需 `xilinx_dma` |
| 上板验证过 | ✅ 门限 3~8 | ❌ | ❌ | ❌ |

## 6. 待验清单（上板时逐条核，别在本机猜）

- [ ] 板上 `import pynq` 到底成不成（决定走 §2 还是 §3）。
- [ ] `system.hwh` 与 `system.bit` **同名同目录**（否则 `ip_dict` 空，报"找不到 IP"而不是"加载失败"）。
- [ ] `mmap_overlay.py` 读 `0x40040000`（`roi_statistic`）是否得到 `CTRL` 的空闲值，还是总线超时。
- [ ] PetaLinux：`state` 是否 `operating`；`xilinx_dma` 是否在 `/sys/class/dma/` 下出现。
- [ ] CMA 大小是否够一帧 RGB（921600 B）**加**灰度回读（110592 B + 384 B 余量，见台账 BUG-037）。

## 变更记录

| 日期 | 改了什么 | 依据 |
|---|---|---|
| 2026-10-23 | 新建：把"裸机 xsdb / PYNQ / mmap / PetaLinux"四条 PS 侧路径写成一张表 + 判据，明确只有裸机那条验证过 | `docs/38` C5；`docs/36` §3/§5；`mmap_overlay.py --selftest` 16/16、`board/bitstream/system.hdf` 成员清单 |
