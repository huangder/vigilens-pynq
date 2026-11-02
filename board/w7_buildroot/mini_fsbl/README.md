# mini_fsbl —— 自己写的最小一级引导（**不是** Xilinx zynq_fsbl）

> 目的只有一个：**在不用 Vitis GUI、不用 Vitis BSP 的前提下，产出一个能上板、串口会出字的 `fsbl.elf` 和 `BOOT.BIN`。**
> 【已验证】2026-10-25 在本机用 `arm-none-eabi-gcc 13.4.0` 编出、`bootgen v2026.1` 打包成功（见下方实测产物）。

## 它做什么

1. `ps7_init()` —— 由 `sdtgen` 从 XSA 生成（DDR / PLL / MIO / 外设初始化序列）
2. 自己初始化 UART1 并打印三行状态 —— **唯一不需要示波器的"我还活着"证据**
3. `ps7_post_config()`，然后跳到 `0x00100000`（U-Boot 的常规装载地址）

## 它不做什么（别误解成"正式 FSBL"）

- 不解析 SD 分区、不做镜像校验（MD5/RSA）、不管 QSPI/NAND 备份启动 —— 那些是 UG821 的正式 FSBL 职责
- 不加载 `zImage` / `system.dtb` / rootfs
- 不配置 PL（overlay / bitstream 是后面的事）

正因为这样，它只用 3 个源文件 + 2 个头文件就能编出来，**不依赖任何 BSP 生成物**。

## 文件

| 文件 | 作用 |
|---|---|
| `start.S` | 入口：设栈 → 清 `.bss` → `bl main` |
| `mini_fsbl.c` | `ps7_init` → 串口打印 → `ps7_post_config` → 跳 U-Boot |
| `mini_fsbl.ld` | 链接到 OCM：`ORIGIN = 0x0`，栈顶 `0x0002F000` |
| `shim/xil_io.h`、`shim/xil_types.h` | 极简 MMIO 与类型定义，**替代** standalone 的 `xil_io.h`（后者会拉 `bspconfig.h`/`xparameters.h` 这两个 BSP 生成物） |
| `build_mini_fsbl.ps1` | 编译 + 校验 ELF + 可选 `bootgen` |

## 怎么用（Windows 普通终端，仓库根）

```powershell
# -Dt 指向 sdtgen 的输出目录（里面要有 ps7_init.c / ps7_init.h）
powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\mini_fsbl\build_mini_fsbl.ps1 `
    -Dt C:\w7\dt -Out C:\w7\mini_fsbl -BootgenSwitch
```

> ⚠️ `-Out` **不要指到仓库里**：受限沙箱下子进程写不进 `D:\Desktop\AMD`；默认值是 `%TEMP%\mini_fsbl`。

脚本会自己校验三件事，任何一条不满足就非零退出：

- `Entry point address` 必须等于 `0x0`（BootROM 跳到 ELF 入口）
- `Machine:` 必须是 `ARM`
- 第一个 `LOAD` 段的 `VirtAddr` 必须是 `0x0`（必须落在 OCM）

## 【已验证】2026-10-25 实测输出

```
== entry: 0x0 / machine: ARM
== first LOAD vaddr: 0x00000000
== dtc: elf looks loadable (entry 0x0, OCM)
****** Bootgen v2026.1
[INFO]   : Bootimage generated successfully
== boot.bin : 14936 bytes
== RESULT: PASS
```

| 产物 | 事实 |
|---|---|
| `fsbl.elf` | 15,912 B；`text 1814 / data 7232 / bss 0`；1 个 `LOAD` 段，`VirtAddr 0x0`、`FileSiz 0x2358`、`RWE`；sha256 `407040c451914b232f810be91f96e55349fc0bb9e5809d16f5fc730d73bafc4a` |
| `boot.bin` | 14,936 B；前 8 个字是 `0xeafffffe`（原地跳），偏移 `0x20` 起是同步字 `0xAA995566`、`0x24` 起是 `XLNX`；sha256 `5d7556e69cabd38210c9bbb201e959074ccf0612f9fd2db724319f1e7a015a1f` |

`boot.bin` 里能搜到 `[mini_fsbl]` 字符串（偏移 `0x1ccd`），说明我们的代码确实在镜像里。
再往下挖一层：镜像头表在 `0x8c0`（版本 `0x01020000`、条目数 1），分区表在 `0x8c0+0x240`，
**代码本身从 `0x1700` 开始**（正好是 ELF 里 `LOAD` 段 `0x1000` 偏移处的内容），镜像名 `fsbl.elf` 写在 `0x910`。
`preflight.ps1` 现在会顺带校验这份头（同步字 + `XLNX`），本机实测 **13 PASS / 0 WARN / 0 SKIP / 0 FAIL**。

## 上板怎么验（还没做）

1. `boot.bin` 改名 `BOOT.BIN`，拷到 FAT32 卡的根目录
2. 启动模式跳线切到 SD（详见 `docs/30`），串口 115200 8N1
3. 期望看到（原样）：

```
[mini_fsbl] VigiLens W7 first stage
[mini_fsbl] ps7_init rc = 0x00000000  (PS7_INIT_SUCCESS)
[mini_fsbl] ps7_post_config done
[mini_fsbl] jumping to U-Boot @ 0x00100000
```

4. 之后会挂住 —— **这是正常的**：`0x00100000` 没有 U-Boot。要让板子继续往下走，得等 Buildroot 产出 `u-boot.elf` 与 `zImage`。

### 串口没输出怎么办（按顺序查）

1. **UART1 的 MIO 到底在哪**：本仓库现写 `MIO 48 .. 49`（属 bank1，默认 1.8 V），而 `docs/12`、`docs/30`、`docs/37` 都说 CH340 接 **MIO14/15**（bank0，3.3 V）。串口静默时先换 `14 .. 15` 重导出 XSA → 重跑 `sdtgen` → 重编。
2. `ps7_init` 是否成功：就算串口不通，`ps7_init` 失败一般表现为卡在 DDR 训练里 —— 这时连不上 xsdb 也是正常的。
3. 供电/启动跳线：SD 启动模式必须真的切过去了（`docs/30`）。

## 踩过的四个坑（都已在脚本里修掉）

| 症状 | 原因 | 修法 |
|---|---|---|
| `Cannot convert value "9_1" to type Int32` | `-split '_v'` 会把 `standalone_v9_1` 拆成三段 | 改成正则 `.*_v(\d+)_(\d+)$`（后来直接用 shim，不再找 standalone） |
| gcc 的 warning 让脚本整体失败 | PS 5.1 在 `ErrorActionPreference='Stop'` 下把原生程序的 stderr 当终止错误 | 整脚本用 `Continue`，成败只看 `$LASTEXITCODE` |
| `cannot open linker script file :` | ① `$args` 是 PowerShell 自动变量，别拿它存参数；② `-Wl,-T,<path>` 被拆分后路径丢了 | 改用 `'-T', <abs path>` 直接交给 gcc 驱动 |
| `first LOAD segment is at 0x00000000, expected 0x0` | `readelf` 打印 8 位十六进制 | 比较前先 `[Convert]::ToInt64($hex, 16)` |
