# environment.md —— C1 环境锁定记录

> 对应《02》任务 **C1:锁定 Vivado/Vitis 版本 + 跑通官方最小 HLS 例程**。
> 验收标准:能完成一次"HLS C 仿真 + 综合"全流程。
> 每次换机器/换版本时更新本文件并公告。

## 1. 工具版本(2026.1 统一安装器,2026-09 记录)

| 项 | 值 | 校验命令 | 状态 |
|---|---|---|---|
| 安装根目录 | `D:\Xilinx` | — | ✅ |
| Vitis(含 Vitis HLS) | `D:\Xilinx\2026.1\Vitis` | 入口是 `vitis-run --mode hls --tcl <脚本>`(2026.1 已无 `vitis_hls` 命令;验证:`vitis-run --version`) | ✅ 已装 |
| Vivado | `D:\Xilinx\2026.1\Vivado` | `vivado -version` | ☐ |
| 操作系统 | Windows 11 | — | ✅ |
| 授权 | Vivado BASIC(free,node-locked,一年一续) | 到期提醒:____ | ☐ |

## 1.1 ⚠️ 怎么调用 vitis-run(2026-09-10 实测踩坑)

**症状**:在自己终端敲 `vitis-run --mode hls --tcl run_hls.tcl` 报
`无法将"vitis-run"项识别为 cmdlet、函数、脚本文件或可运行程序的名称`。

**原因**:Vitis 安装时**没有**把 `D:\Xilinx\2026.1\Vitis\bin` 写进用户 PATH
(实测:用户 PATH 里 `Xilinx|Vitis|Vivado` 匹配为 0 条),所以裸命令找不到。

**正确做法 —— 先加载 Vitis 环境,再跑(cmd 终端)**:

```bat
call D:\Xilinx\2026.1\Vitis\settings64.bat
cd /d D:\Desktop\AMD\fpga
vitis-run --mode hls --tcl run_hls.tcl
```

**PowerShell 用户注意**:`& 'D:\Xilinx\2026.1\Vitis\settings64.bat'` **不生效**
(bat 在子进程里设的环境变量不会留在当前会话)。要么改用 cmd,
要么显式导入环境变量:

```powershell
cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
}
```

**验证**:`where vitis-run` 应输出 `D:\Xilinx\2026.1\Vitis\bin\vitis-run.bat`。
另确认:**2026.1 确实没有 `vitis_hls`** 命令(`where vitis_hls` 无输出),入口只有 `vitis-run`。

### 1.2 ⚠️ 受限沙箱跑不了 C 仿真(2026-09-10 实测)

在 DSH 受限沙箱(workspace-write)下跑 `vitis-run` 会在 csim 阶段失败:

```text
0 [main] cat (17668) D:\Xilinx\2026.1\Vitis\tps\win64\msys2_bash\usr\bin\cat.exe:
       *** fatal error - couldn't create signal pipe, Win32 error 5
ERROR: [SIM 211-100] 'csim_design' failed: compilation error(s).
```

`Win32 error 5` = 拒绝访问:HLS 的 C 仿真用 cygwin/MSYS2 工具链,**需要创建 signal pipe
(命名管道)**,而受限沙箱按设计禁止命名管道。**这不是 Vitis 装坏了,也不是代码问题。**
→ 解决办法:在**自己的完整权限终端**跑;或让 AI 以 `danger-full-access` 提权跑(实测可通)。

> 附:csim 用的编译器是 **clang-16**(HLS 内置),不是本机 g++。本机 g++ 只能做 `-fsyntax-only` 自检。

## 2. License 备注

- BASIC 免费档覆盖:7 系列全系、Zynq-7000、Zynq US+ ZU1~ZU7、Kria SOM、低端 US+。
- Vitis HLS 的 C 仿真/综合不需要额外 License;调用 Vivado 后端的流程(实现/导出 bitstream)按器件档位校验。
- 学校 University Program / 板卡授权券:☐ 已问带队老师(结论:____)。

## 3. 官方最小例程验证(C1 验收)

| 项 | 状态 |
|---|---|
| 例程仓库(已克隆到 `fpga/examples/Vitis-HLS-Introductory-Examples`) | https://github.com/Xilinx/Vitis-HLS-Introductory-Examples | ✅ 已克隆 |
| 运行命令(2026.1) | `vitis-run --mode hls --tcl run_hls.tcl`(在例程目录内;或 Vitis IDE 打开) | — |
| 例程默认器件多为 VU9P/Versal(非免费档);免费档可用 7 系列例程:`Task_level_Parallelism/Data_driven/using_directio_none_in_tasks`(xc7v585t,备用) | — | ☐ 备用 |
| **自研最小 IP** `roi_statistic`(计数+累加)位于 `fpga/src/roi_statistic.cpp`,测试台 `fpga/sim/tb_roi_statistic.cpp`,脚本 `fpga/run_hls.tcl` | — | ✅ 已通过 |
| C 仿真通过 | — | ✅ `sum=7392 exp=7392 cnt=64 exp=64 → PASS`,0 errors |
| 综合(csynth)通过、无 ERROR | — | ✅ |
| 记录:目标器件 / 时钟 / 资源 | — | ✅ xc7z020-clg400-1;Fmax 179.21 MHz;LUT 134 / FF 41 / BRAM 0 / DSP 0(占~0%) |
| 备注:HLS 的 C 仿真构建需 MSYS2 管道,沙箱普通模式会报 `cat.exe ... signal pipe` | — | 跑 `vitis-run` 需完整权限,或在你自己终端跑 |

## 4. 变更记录

| 日期 | 谁 | 改动 |
|---|---|---|
| 2026-09-10 | C 线 | 建文件;工具装于 D:\Xilinx(BASIC 授权已领) |
| 2026-09-10 | C 线 | 补 1.1 节:`vitis-run` 不在 PATH,须先 `call settings64.bat`(含 PowerShell 用法);补 1.2 节:受限沙箱因命名管道被禁跑不了 csim |
| 2026-09-10 | C 线 | C3/C6/C7 完成 —— `roi_statistic` v1 的仿真与综合真实结论见 `fpga/report/c3_c7_roi_statistic_v1.md`(csim 28/28 + 45/45、0 errors;II=1;Fmax 138.99 MHz;LUT 1267/FF 723/BRAM 0/DSP 1) |
