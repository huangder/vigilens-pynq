# board/w7_buildroot/dt/ —— sdtgen 生成物（**不入库**）

这个目录只是**放生成物的地方**，目录本身在仓库里，里面的东西不要提交。

## 放什么

在 Windows 上跑一次（约 20 s，见 docs/41 §15.2）：

```powershell
# ⚠️ 路径必须用正斜杠：Tcl 会吃掉反斜杠
D:\Xilinx\2026.1\Vitis\bin\sdtgen.bat `
  -xsa  "C:/path/to/system.xsa" `
  -dir  "D:/Desktop/AMD/board/w7_buildroot/dt"
```

产出四个文件（`system-top.dts` / `pcw.dtsi` / `pl.dtsi` / `zynq-7000.dtsi`）
外加 PS 初始化代码（`ps7_init.c` / `ps7_init.tcl` / `ps7_init_gpl.c`）和 `system.bit`。

## 为什么不留档

它们全部**由 XSA 决定**：换 XSA 就该重生成，提交进仓库只会制造"改了 BD 忘记重导"的假一致。
要复现就在同一份 `board/bitstream/system.hdf` / `.xsa` 上重跑上面的命令。

## 生成后立刻做的事

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File board\w7_buildroot\check_dts.ps1
```

期望末行 `== RESULT: PASS`。PS7 打开 USB0 之后要加 `-ExpectUsb okay`（SD 同理 `-ExpectSd okay`）。

## 2026-10-25 的真实运行（照抄可用）

```text
sdtgen 产物（14 项）：pcw.dtsi 43481 B / pl.dtsi 7505 B / system-top.dts 2788 B /
  zynq-7000.dtsi 14065 B / ps7_init.c 335957 B / ps7_init.tcl 26573 B /
  ps7_init_gpl.c 336574 B / system.bit 4045769 B / drivers/ extracted/ include/
check_dts.ps1 → == dtc ok, DTB = 63.8 KB，8 条 PASS，== RESULT: PASS，exit 0
preflight.ps1 → [PASS] dts  check_dts.ps1 exit 0 (== RESULT: PASS)
```

## 两个坑（都真踩过）

1. **`-xsa` 和 `-dir` 都必须用正斜杠。** 反斜杠会被 Tcl 当转义吃掉，
   报错长得像 `could not read "C:Users?58AppData...": no such file or directory`
   —— 报错里的路径被"啃"掉了分隔符，**不是你路径写错**，换成 `/` 就好。
2. **`sdtgen` 失败时也返回退出码 0。** 别用 `$LASTEXITCODE` 判成功，**看产物在不在**
   （`pcw.dtsi` 等四个文件）。同样的道理适用于 Vitis 2026.1 的几个 `.bat` 包装脚本。

## 沙箱里跑的时候（只影响 AI，不影响你）

DSH 的沙箱**不允许子进程在仓库里创建文件**（`Set-Content` 会 `访问被拒绝`，
但 `New-Item -ItemType Directory` 又能建目录），所以 AI 只能把 sdtgen 的输出放到
`%TEMP%\dt_final`，再由 `preflight.ps1` 自动回退过去找（顺序：
`board/w7_buildroot/dt/` → `$env:W7_DT` → `%TEMP%\dt_final`）。
**你自己在普通 PowerShell 里跑就写本目录**，不需要任何回退。
