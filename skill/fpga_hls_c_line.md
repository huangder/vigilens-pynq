# Skill: C 线 FPGA / Vitis HLS 开发(本工作区专用)

- 适用对象:三人小组 C 线(FPGA/HLS),赛制 AMD 3.3 自主选题初级组
- 配套文档:《01 评估》《02 分工》《03 实现方向》《04 基础框架》《05 AI 约束》《06 工具调研》
- 本文件用途:任何 C 线任务开始时,**先读本文件再动手**;把新踩坑追加到"常见坑"并登记变更记录。

---

## 一、适用场景(何时调用本 Skill)

1. 写/改 HLS IP 源码(`roi_statistic` / `motion_quality` / `fir_filter`)并做 C 仿真;
2. RTL(testbench/cocotb)验证或综合报告分析;
3. 需要"C 仿真结果 vs Python 黄金参考"比对;
4. 制定/核对 C 线 IP 接口(AXI-Stream + 寄存器),与 A/B 线契约对齐;
5. M3 之前的一切离线验证工作(不需要板卡);
6. 环境问题:工具链、版本、仿真器、依赖。

不适用:上板/DMA/Overlay(M3+,属 `board/` 与后续 PYNQ skill)。

---

## 二、工作区环境事实(动手前先核对,勿假设)

| 项 | 值(2026-09 第 0 天记录) | 备注 |
|---|---|---|
| 赛制指定工具 | **Vitis HLS 2026.1**(AMD 官方) | **已装**。2026.1 里 HLS 集成在 Vitis 产品内,入口是 **`vitis-run --mode hls --tcl <脚本>`**(不再是旧 `vitis_hls -f`) |
| 安装路径 | `D:\Xilinx\2026.1\Vitis`(无独立 `vitis_hls.bat`) | **不在 PATH**;先 `call D:\Xilinx\2026.1\Vitis\settings64.bat`,再验证 `where vitis-run` |
| Python | 系统 3.14.7(`C:\Users\31158\AppData\Local\Python\pythoncore-3.14-64`) | 已有 |
| uv | 0.12.3(`C:\Users\31158\.local\bin\uv.exe`) | 已有 |
| cocotb | 装在 `D:\Desktop\AMD\.venv` | 版本 2.1.0 |
| git | `D:\Git\cmd\git.exe` | 用户 PATH,新终端生效 |
| 仿真器 | Verilator / Icarus / GTKWave 未装 | 后装;M1 主流程是 Vitis HLS C 仿真 |
| 目标器件(占位) | `xc7z020clg400-1`(Zynq-7000,免费档) | 赛制板卡确定后更新 |
| 网络/沙箱 | 普通 shell 无外网;HLS C 仿真构建需 MSYS2 管道,普通沙箱会报 `cat.exe ... signal pipe` | 跑 `vitis-run` 需完整权限,或在自己终端跑 |

项目结构(按《04》):`fpga/src`(HLS 源码)、`fpga/sim`(testbench+黄金参考)、`fpga/report`(综合报告)、`board`(后期)、`skill`。

---

## 三、C 线铁律(与《02》一致,违背即返工)

1. **只对契约开发**:IP 接口(位宽/精度/顺序)必须先冻结进 `docs/interface.md`,改接口 = 发公告;
2. **能离线验证的绝不等上板**:全部用 "HLS C 仿真 + Python 黄金参考比对" 验证,板卡只在 M3 用;
3. **"看起来能用"不算完成**:以 黄金参考一致 + 重复运行可复现 + 综合无 ERROR 为准。

---

## 四、标准工作流(M1 目标:C 仿真 + 综合报告)

### Step 0 环境锁定(C1)—— 第 1 天必做
- 装 Vitis HLS 2026.1(手动,见上表);
- 跑通官方最小例程一次"仿真 + 综合"全流程:
  - 例程仓库:https://github.com/Xilinx/Vitis-HLS-Introductory-Examples
  - 产物:记录版本与路径到 `fpga/report/environment.md`,勾掉 C1。
- 无头模式命令范式(HLS 支持 Tcl 批处理):
  ```tcl
  # run_hls.tcl 骨架
  open_project proj
  set_top roi_statistic
  add_files ../src/roi_statistic.cpp
  add_files -tb ../sim/tb_roi_statistic.cpp
  open_solution sol1
  set_part {xczu3eg-sbva484-1-e}   ;# 以实际板卡型号为准,先与板卡方确认
  create_clock -period 10 -name default
  csim_design
  csynth_design
  exit
  ```
  命令行:`vitis-run --mode hls --tcl run_hls.tcl`(在含 run_hls.tcl 的目录执行;2026.1 已无 `vitis_hls -f`)

### Step 1 写 IP(按 C2 冻结的接口)
- 像素流接口照抄 Vitis_Libraries 的 `ap_axiu` 范式:https://github.com/Xilinx/Vitis_Libraries
- 数值精度(定点位宽)以 `docs/interface.md` 为准,和 A 线 Python 参考完全一致。

### Step 2 C 仿真 + 黄金参考比对(C3~C6)
- 每个 IP 必须有 testbench(`fpga/sim/`)与 Python 黄金参考(与 A 线同一口径);
- 边界用例必测:全零 / 全满 / 单点变化 / 随机输入;
- 比对口径见第五节。

### Step 3 综合报告(C7)
- 记录 LUT / FF / BRAM / DSP / 时钟 / WNS 到 `fpga/report/`;
- 验收:无 ERROR,目标时钟下时序收敛(有 WNS,无违规);
- 之后同步 A 线:同输入下 C 结果 vs A 线软件结果,差异在预设容差内。

---

## 五、黄金参考比对(核心纪律)

- 原则:**同一份输入,硬件算的 vs 软件算的,必须一致(整数运算逐点相等;定点运算在容差内)**;
- 实现:Python 参考脚本(A 线 numpy/OpenCV 版)与 cocotb 或 HLS C testbench 读同一份输入文件;
- cocotb 用法(装于 `.venv`,RTL 阶段用):
  ```powershell
  & 'D:\Desktop\AMD\.venv\Scripts\python.exe' -m pip list | findstr cocotb   # 确认已装
  # 典型:uv run --project . python -m pytest tests/  (RTL + cocotb 用例)
  ```
- 产出:可复现的比对报告(记录输入 seed、通过数、失败 diff)。

---

## 六、IP 接口速查(以 `docs/interface.md` 冻结版为准,这里是草案口径)

| IP | 输入 | 输出 | A 线黄金参考 |
|---|---|---|---|
| `roi_statistic` | 像素流 + ROI 坐标 | `{sum_r, sum_g, sum_b, count}` | Python 同 ROI 累加 |
| `motion_quality` | 当前/上一帧灰度 | `{diff_total, motion_ratio}` | OpenCV 帧差 |
| `fir_filter` | 时间序列 | 滤波序列 | NumPy 滤波 |

> 任何位宽/顺序改动 → 先改 `docs/interface.md` + 群公告,再动代码。

---

## 七、常见坑与排查(持续追加)

1. **csim 通过但结果与 Python 不一致**:几乎都是位宽/截断/顺序问题 —— 先核对定点格式与输出顺序;
2. **综合报 ERROR / 时序不收敛**:先查循环边界是否可静态分析、数组是否被综合成不可控内存;
3. **`set_part` 报错**:板卡型号没确认前,先用一个确定型号占位并在 `environment.md` 记录;
4. **接口 stream 与 register 混用**:AXI-Stream 必须配 FIFO 语义,hls::stream 只在循环内/函数间用;
5. **testbench 不退出**:检查 `csim` 是否缺少 exit 条件(例如文件尾标记);
6. **报告不可复现**:固定输入 seed + 固定脚本,报告文件名带版本号;
7. **cocotb 找不到仿真器**:M1 不依赖;到 RTL 阶段再装 Verilator/Icarus(Windows 上 Verilator 走 MSYS2,较折腾);
8. **普通 shell 无外网**:安装/下载类命令需更高权限(沙箱);日常开发不受影响;
9. **PATH 不生效**:`.tools\git\cmd`、`.local\bin` 已写用户 PATH,新开终端生效;当前终端可用全路径。
10. **本机 g++ 不能运行 `hls::stream` 的 C 仿真模型**(2026-09-10 实测):本机 MinGW 是 `win32` 线程模型
    (`x86_64-16.1.0-release-win32-seh`),而 `hls::stream` 的 C 仿真模型依赖 `std::thread`/`std::mutex`,
    链接后一运行就以 `0xC0000139`(STATUS_ENTRYPOINT_NOT_FOUND,DLL 入口点缺失)退出,**不是代码 bug,别去改代码**。
    ✅ 正确用法:本机 g++ **只做秒级语法自检**,执行一律交给 `vitis-run`:
    ```powershell
    $inc = 'D:\Xilinx\2026.1\Vitis\include'
    g++ -std=c++17 -fsyntax-only -I $inc fpga/src/<ip>.cpp
    g++ -std=c++17 -fsyntax-only -I $inc fpga/sim/tb_<ip>.cpp
    ```
    这条能在花几分钟跑 csim 之前先抓出拼写/类型/原型不匹配,实测省时间。
11. **C 仿真不建模 `hls::stream` 的 FIFO 深度**(2026-09-10 读头文件核实):
    `Vitis\include\hls_stream.h` 第 202 行是 `std::deque<std::array<char,_SIZE>> data;` —— **容器无界**。
    推论:testbench 可以用"先把整帧灌进流、再调用 IP、最后排空"的写法,不会溢出。
    ⚠️ 反面:**流深度不足导致的死锁只有 RTL 协同仿真(cosim)才暴露**,csim 全绿≠上板不死锁。
    M3 之前至少对每个 IP 跑一次 `hls_exec = 2`(csynth + cosim)。
12. **图像通道顺序必须是 RGB,而 OpenCV 默认 BGR**(2026-09-10 写进 `docs/interface.md`):
    `frames.bin` 与 `ap_axiu<24,1,1,1>` 的字节序是 `byte0=R, byte1=G, byte2=B`。
    A 线做黄金参考比对前必须 `cv2.cvtColor(img, cv2.COLOR_BGR2RGB)`,否则 R/B 互换 ——
    全图 ROI 下 `sum_r` 与 `sum_b` 会"看起来差不多",极难肉眼发现。同理:**ROI 是半开区间 `[x0,x1)`**,
    与 `img[y0:y1, x0:x1]` 等价,别做 ±1 心算。
13. **Python 环境不统一**:系统 Python 3.14.7 **有 numpy 2.5.2**;项目 `.venv` 有 cocotb 2.1.0 / pytest 9.1.1 但**无 numpy、无 pip**(uv 建的环境)。
    所以脚本要写成**只用标准库**、numpy 可选增强,才能两个解释器都跑得起来。
14. **`vitis-run` 不在 PATH,要手动加载环境**(2026-09-10 实测):裸敲 `vitis-run` 会报"无法识别为 cmdlet/程序"。
    Vitis 安装时**没有**把 `D:\Xilinx\2026.1\Vitis\bin` 写进用户 PATH。正确做法:
    ```bat
    call D:\Xilinx\2026.1\Vitis\settings64.bat
    cd /d D:\Desktop\AMD\fpga
    vitis-run --mode hls --tcl run_hls.tcl
    ```
    ⚠️ **PowerShell 里 `& '...\settings64.bat'` 不生效**(子进程设的环境变量留不下来)。PowerShell 要用:
    ```powershell
    cmd /c "call D:\Xilinx\2026.1\Vitis\settings64.bat && set" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
    }
    ```
    验证:`where vitis-run` 应输出 `...\Vitis\bin\vitis-run.bat`。
15. **受限沙箱(含 workspace-write)跑不了 csim**(2026-09-10 实测):报
    `cat.exe: *** fatal error - couldn't create signal pipe, Win32 error 5` + `csim_design failed: compilation error(s)`。
    **`Win32 error 5` = 拒绝访问**:HLS 的 C 仿真走 cygwin/MSYS2,**要创建 signal pipe(命名管道)**,
    而受限沙箱按设计禁止命名管道。**不是 Vitis 坏了,也不是代码错**,别去改代码。
    → 在自己完整权限的终端跑;或让 AI 以 `danger-full-access` 提权跑(实测可通)。
    另:csim 用的编译器是 **clang-16**(HLS 内置),不是本机 g++。
16. **s_axilite 输出会多出一个 `*_ctrl` 寄存器**(2026-09-10 综合实测)**:** 若输出变量只在函数末尾赋值,
    HLS 把它综合成 `s_axilite & ap_vld`,于是**每个输出寄存器后面 +4 字节**多一个 valid 寄存器
    (如 `sum_r=0x40` → `sum_r_ctrl=0x44`,`bit0 = sum_r_ap_vld`)。
    写寄存器契约时**必须把 `*_ctrl` 一起写进去**,否则 M3 上板时 PS 会误判"值没更新"。
    好在寄存器偏移本身是按 `offset=` 精确落地的,综合报告 `csynth.rpt` 的 `* S_AXILITE Registers` 表
    可以逐行核对契约 —— **出完综合一定要拿这张表对一遍契约**。
17. **`static` 变量是"上电初始化",不是"复位归零"**:综合会警告 `Register 'fid' is power-on initialization`。
    需要复位后归零的计数器不能靠 `static` 初值,得写显式复位逻辑。
18. **`#pragma HLS BIND_STORAGE` 必须写在变量声明之后**(2026-09-10 实撞)**:** 写在声明**之前**会 csynth 报
    `ERROR: [HLS 207-4637] use of undeclared identifier 'xxx'` —— HLS 按顺序解析 pragma。
    ```
    static ap_uint<8> buf[N];                                   // 先声明
    #pragma HLS BIND_STORAGE variable=buf type=RAM_2P impl=BRAM // 后写 pragma
    ```
    ⚠️ **最关键的教训:csim 不检查这条 pragma,所以这个错误只在 csynth 暴露。**
    **"csim 全绿"绝不等于"综合能过"** —— 每次改完都要 csim + csynth 都跑。
19. **大片内缓存按「2 的幂地址空间」吃 BRAM,而且是台阶式的**(2026-09-10 对照实验坐实)**※重要※:**
    640×480×8bit 的"上一帧"缓存实测占 **256 个 BRAM18 = xc7z020 的 91%**(共 280 个)。
    只改缓存尺寸重跑,三组对照:

    | depth | 地址位宽 | 实测 BRAM18 | 按 2^⌈log2(depth)⌉÷2048 | 按 depth÷2048 |
    |---|---|---|---|---|
    | 76800 (320×240) | 17 | **64** | **64** ✅ | 38 ✗ |
    | 262144 (512×512) | 18 | **128** | **128** ✅ | 128 ✅ |
    | 307200 (640×480) | 19 | **256** | **256** ✅ | 150 ✗ |

    **规律:工具按 2 的幂地址空间分配**(307200 落在 2^18~2^19 → 按 2^19=524288 → 256 个),
    比按真实数据量多约 1.7 倍。
    ⚠️ **推论(设计时很有用):缓存成本是台阶式的,跨过 2 的幂就翻倍** ——
    所以**像素数 ≤ 131072 的任意尺寸都只要 64 个**(320×240 与 384×288 同价,后者更清晰)。
    要存整帧的算法(帧差、时域滤波等)上 xc7z020 前**先按这个台阶表估 BRAM**;
    出路:降分辨率贴着台阶挑、或改"双流"方案(PS 同时送当前帧与上一帧,片内 0 缓存,DMA 流量翻倍)。
    > 对照实验方法(可复用):只改 `#define MOTION_MAX_*` + 用 `gen_motion_vectors.py --width X --height Y --out-dir ...`
    > 生成配套数据 + `set ROI_DATA_DIR=...` 重跑,改一个变量、看一个结果。
20. **自定义整数口径必须写进契约并让对方对拍**(2026-09-10):灰度式 `(77R+150G+29B+128)>>8` 给纯红 **77**,
    而"按浮点系数四舍五入"给 **76**(0.299×255 = 76.245)。差 1 LSB 且**系统性存在**,
    比"完全对不上"更难发现。凡自定义定点口径,必须在契约里写死 + 给对方一段可直接跑的对拍脚本。
21. **`vitis-run` 的日志会被下一次运行覆盖**:`fpga/logs/hls_run_tcl.log` 每次跑都重写。
    想要"某次通过的证据",**跑完立刻另存**(本项目存到 `fpga/report/logs/<日期>_<ip>_v<版本>.log`)。
    每个 IP 自己的 `component_<ip>/hls/syn/report/*.rpt` 不会被互相覆盖,可作长期证据。

---

## 八、失效条件(本 Skill 何时不适用)

- 已进入 M3 上板/DMA/Overlay 阶段(转 `board/` + 后续 PYNQ skill);
- 赛制指定版本变更(不是 2026.1);
- 项目主场景变更导致 IP 集合变化(先改接口契约再回来改本 Skill 第五节)。

---

## 九、参考与链接

- 《01 评估报告》《02 分工》《03 实现方向》《04 基础框架》《06 工具调研》(`docs/`)
- Vitis HLS Introductory Examples:https://github.com/Xilinx/Vitis-HLS-Introductory-Examples
- Vitis Libraries:https://github.com/Xilinx/Vitis_Libraries
- cocotb:https://www.cocotb.org/

---

## 十、变更记录

| 日期 | 谁 | 改动 |
|---|---|---|
| 2026-09-09 | C 线(经 AI) | 初建:环境事实 + 工作流 + 黄金参考纪律 + 首批避坑 |
| 2026-09-10 | C 线 | C1 完成(环境锁定 + 玩具版 IP 仿真/综合通过) |
| 2026-09-10 | C 线 | C2 草案:`docs/interface.md` v0.9(PYNQ-Z2 / 640×480 RGB888 / 寄存器映射 / 测试向量格式 / 容差 0) |
| 2026-09-10 | C 线 | 追加常见坑 10~13:`hls::stream` 仿真模型必须用 vitis-run 跑、csim 不建模流深度、RGB vs BGR 与半开区间、Python 环境 numpy 分布 |
| 2026-09-10 | C 线 | **C3/C6/C7 完成**:`roi_statistic` v1 csim 28/28 + 45/45、0 errors,II=1,Fmax 138.99 MHz,LUT 1267/FF 723/BRAM 0/DSP 1。追加常见坑 14~17:`vitis-run` 须先 `settings64.bat`(含 PowerShell 写法)、受限沙箱因命名管道跑不了 csim(`Win32 error 5`)、s_axilite 输出多一个 `*_ctrl`(ap_vld) 寄存器、`static` 是上电初始化非复位归零 |
| 2026-09-10 | C 线 | **C4 完成**:新增 `rgb2gray`(9/9 + 10/10,Fmax 151.98 MHz,BRAM 0/DSP 3)与 `motion_quality`(6/6 + 9/9,Fmax 140.05 MHz,**BRAM 256 = 91%**);`run_hls.tcl` 改为多 IP 通用(`HLS_IP` 环境变量)。追加常见坑 18~21:`BIND_STORAGE` pragma 必须在声明后且 **csim 不检查它(全绿≠综合过)**、大片内帧缓存实测吃掉 91% BRAM、自定义整数口径要写死并对拍、vitis-run 日志会被覆盖需立刻另存 |
