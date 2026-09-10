# Skill: C 线 FPGA / Vitis HLS 开发(本工作区专用)

- 适用对象:三人小组 C 线(FPGA/HLS),赛制 AMD 3.3 自主选题初级组
- 配套文档:《01 评估》《02 分工》《03 实现方向》《04 基础框架》《05 AI 约束》《06 工具调研》
- 本文件用途:任何 C 线任务开始时,**先读本文件再动手**;把新踩坑追加到"常见坑"并登记变更记录。

---

## 一、适用场景(何时调用本 Skill)

1. 写/改 HLS IP 源码(`roi_statistic` / `rgb2gray` / `motion_quality` / `fir_filter`)并做 C 仿真;
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
| `rgb2gray` | 640×480 RGB 流 | 384×288 灰度流 + `sum_gray` | NumPy 冻结算式 `(77R+150G+29B+128)>>8` + 3/5 抽取 |
| `motion_quality` | 当前/上一帧灰度 | `{diff_total, motion_pixels, motion_ratio_q16}` | OpenCV 帧差（严格大于阈值） |
| `fir_filter` | 时间序列（int16 Q1.15） | 滤波序列 + `saturation_count` | Python 精确整数：`acc=Σh[k]x[n−k]; y=sat16(acc>>15)` |

**四个 IP 的比对容差全部 = 0**（逐样本严格相等）；`fir_filter` 的系数冻结在 `fpga/src/fir_coeffs_q15.h`。

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

    **本项目实际怎么解的(可直接照抄思路)**:让上游 `rgb2gray` 顺带做 **3/5 相位抽取**
    (保留 `x%5<3 && y%5<3`,640x480 -> 384x288 = 110592,正好落在 2^17 台阶),
    `motion_quality` 的工作尺寸随之降到 384x288 -> **BRAM 64 个(23%)**,Fmax 不退化(140.05 MHz)。
    同台阶内 384x288 与 320x240 同价,故选了更清晰的 384x288。
    ⚠️ 代价:**点采样会保留混叠**(运动检测可能偏"敏感");若不接受,改块均值(黄金参考要同步改)。
    ⚠️ 抽取比例非整数(3/5)时要**相位步进**,别用整除取模去算,并且 Python 侧用 `np.ix_` 逐位镜像。
20. **自定义整数口径必须写进契约并让对方对拍**(2026-09-10):灰度式 `(77R+150G+29B+128)>>8` 给纯红 **77**,
    而"按浮点系数四舍五入"给 **76**(0.299×255 = 76.245)。差 1 LSB 且**系统性存在**,
    比"完全对不上"更难发现。凡自定义定点口径,必须在契约里写死 + 给对方一段可直接跑的对拍脚本。
21. **`vitis-run` 的日志会被下一次运行覆盖**:`fpga/logs/hls_run_tcl.log` 每次跑都重写。
    想要"某次通过的证据",**跑完立刻另存**(本项目存到 `fpga/report/logs/<日期>_<ip>_v<版本>.log`)。
    每个 IP 自己的 `component_<ip>/hls/syn/report/*.rpt` 不会被互相覆盖,可作长期证据。
22. **cosim 的 `-argv` 不继承,漏传会"假通过"** ※重要※(2026-09-10 实撞):
    `csim_design` 和 `cosim_design` 是**两个独立命令**,`-argv` 必须**分别传**:
    ```tcl
    csim_design  -argv "$data_dir"
    cosynth_design
    cosim_design -rtl verilog -tool auto -argv "$data_dir"   # ← 少了这个,Layer 2 不会跑
    ```
    漏传的后果极具欺骗性:测试台拿不到数据目录 -> 走"没有数据目录就跳过 Layer 2"的分支 ->
    **cosim 只用 Layer 1 的几个玩具用例就报 `C/RTL co-simulation finished: PASS`**。
    **判据:看日志里有没有 Layer 2 的通过行和对应的 RTL 事务数,不要只看 PASS 字样。**
23. **cosim 会主动帮你查死锁,别自己猜**(2026-09-10 实测):HLS 会编译进
    `AESL_deadlock_idx0/idx1_monitor.v` + `AESL_deadlock_kernel_monitor_top.v`,
    RTL 侧真死锁会被报出来。本项目实测:测试台"灌满整帧再调用"的写法(一次写 4800 像素,
    而输出 FIFO 综合深度只有 6)**不会死锁** —— cosim 通道是事务级的,会边消费边推进。
    ⚠️ 但这**不等于上板也安全**:真实 DMA 场景的握手节奏不同,流深度仍要在 M3 实测确认。
24. **cosim 用命令行能白拿"每帧真实延迟"**(2026-09-10):日志里的
    `// RTL Simulation : N / M [n/a] @ "<ps>"` 是事务时间戳,相邻两条之差 ÷ 10000 ps 就是该次调用的**拍数**。
    本项目实测三例:`roi_statistic` 3096 拍/3072 px、`rgb2gray` 4829 拍/4800 px、`motion_quality` 1804 拍/1728 px
    -> **拍数 ≈ 像素数 + 固定开销**(24 / 29 / 76 拍),即 RTL 上确实 II=1。
    固定开销不同是因为 `motion_quality` 每次调用要跑一次 **64 位除法**(算 ratio),
    这条数据在 M4"软硬件延迟对比"里直接能用,记得**区分实测与外推**。

    ⚠️ **补充(2026-09-11)**:这条只在"一次调用 = 一个事务"的干净场景成立。测试台若按
    "先灌满整段/整帧、再调用、后排空"的写法写,**时间戳里混进了喂/排数据的拍数**,
    逐条差的含义就不再是"单次调用延迟"。此时只能用"末条时间戳 ÷ 时钟周期"当**含开销的总量**,
    并如实标注"含喂数据/AXI-Lite 事务开销",**不要**硬算成单次延迟。
    `fir_filter` 实测:985 样本 / 28 次调用 / 13 次复位,总计 4004.5 拍 @100 MHz。

25. **窗函数法设计 FIR:过渡带宽是"阶数"的直接代价,先算再选口径** ※C5 经验※(2026-09-11)
    Hamming 窗的过渡带宽(Hz) ≈ **3.3/(2πN)·fs**,N=63、fs=30 时 = **0.25 Hz**。
    这条公式必须先算,因为它决定"边缘口径"怎么选:
    - 窗函数法下实现的 **−6 dB 点落在设计边缘**(0.7/3.5 Hz),**−3 dB 点会往里缩**;
    - 若把设计边缘外扩让 **−3 dB 点**落在 0.7/3.5,则 −6 dB 点跑到 0.5/3.7,
      代价是**阻带抑制变差**(0.35 Hz 从 −16.2 dB 退化到 −9.5 dB),对 rPPG 是硬伤(呼吸/漂移带就在那)。
    **做法**:写个 `--scan` 把两种口径的实测数字并排打出来,用数据定口径并写进契约;
    不要"先写死一个口径再解释"。本项目最终选 **−6 dB = 0.7/3.5 Hz**(可测、可复现、阻带最好)。
    ⚠️ 推论:**窄带(带宽 ≈ 过渡带宽度)用 FIR 是不可行的** —— 0.1~0.5 Hz 呼吸带需要 N ≳ 105~158 阶,
    正确出路是**先降采样再滤波**(采样率降到 2 Hz,同阶数过渡带就降到 0.017 Hz)。

26. **`static const` 系数表不要写 `ARRAY_PARTITION`** ※C5 经验※(2026-09-11)
    `static const short COEFF[N] = {...}` 会被 HLS **常量折叠**进乘法器,
    再写 `#pragma HLS ARRAY_PARTITION variable=COEFF complete` 只会被工具标成
    `Ignored Pragmas: ... Not implemented`,在综合报告里留一条**误导性记录**(别人会以为你没写对)。
    ✅ 删掉它。**删前删后各跑一次 csynth 对比资源**(本项目删前删后 LUT/FF/DSP/Fmax 完全一致)。

27. **"static 变量上电未定义"这个说法要修正** ※重要,修正坑 #17※(2026-09-11)
    C++ 语言层面,**static 存储期对象是零初始化**(有保证);HLS 的真正行为是把它实现为
    **上电初始化(power-on initialization)** —— 综合日志里逐 bit 一条
    `Register '...' is power-on initialization`,**复位不会把它清零**。
    ✅ 结论不变(有状态数组不能依赖上电值),但**理由要说准确**:不是"C 不给初值",
    而是"**HLS 用上电初始化实现、复位不清零**",且这个上电值在真实器件上依赖 bitstream 的 INIT 内容。
    → 有状态的 IP **必须给显式的 `reset` 端口**(本项目的 `fir_filter` 就是这么做的),
    PS 侧与测试台统一约定"**先 reset 再喂数据**"。

28. **有状态 IP 必须测"分段调用 == 一次调用"** ※C5 经验※(2026-09-11)
    对带片内状态(延迟线/上一帧)的 IP,黄金参考只能证明"算得对",证明不了"状态机语义对"。
    补一条**不依赖黄金参考**的独立检查:同一串数据
    (a) 段1(reset=1) + 段2(reset=0) 分别调用,拼接结果
    (b) 一次调用处理两段
    **必须逐样本相同**;并且再补一条"段2(reset=1) 的结果 ≠ 段2(reset=0) 的结果",
    以证明状态**真的被继承**(否则 (a)==(b) 可能只是因为状态压根没用上)。
    `fir_filter` 的 `split_part1/2/3` vs `random_full` 就是这个用例。

29. **csim 里 `static` 计数器会跨"测试层"累加,测试台别假设它从 1 开始** ※C5 实测踩到※(2026-09-11)
    `fir_filter` 的 `seg_id` 是 `static` 调用计数器。第 1 层(内嵌用例)先调了 13 次,
    于是第 2 层(黄金参考)第一次调用的 `seg_id` 是 **14 而不是 1** —— 测试台按 CSV 段号比对时
    15 段全报 FAIL,而**样本比对其实是 0 不一致**(差点误判成 IP 有 bug)。
    ✅ 正确判据:**校验"每次调用 +1"**(并允许首条是任意 >0 的值),而不是"等于段号"。
    ⚠️ 同理:任何"上电/累加型"输出寄存器,在 csim 里都是**整个进程级**的,
    分层测试时要意识到它们会累积。

30. **软件侧的"流式实现"必须跨调用保持状态——它与硬件的"段间保持"是同一个坑** ※P0-4 实测抓到※(2026-09-11)
    写 PS 侧基线时,第一版 `numpy_stream` **每个 chunk 都新建状态**(历史清零),结果与
    "整段一次卷积"**不一致**。这不是 DUT 的问题,而是**软件侧把硬件语义写错了** ——
    和 `fir_filter` 的"`reset=0` 段必须承接上一段状态"是同一条语义。
    ✅ 教训:① 任何"分段处理"的实现在两侧都要显式维护状态,并在脚本里**内置一致性自检**
    (batch vs stream 必须逐样本相等),不一致就**退出码非 0**、不许拿坏数据当基线;
    ② 这条自检是**顺手写下的**,却当场抓到了 bug —— 值得作为所有基准/对拍脚本的默认配置。

31. **加了复位之后,`power-on initialization` 警告**不会**消失——别拿警告当判据** ※P0-2 实撞※(2026-09-11)
    给 `static` 计数器加 `#pragma HLS RESET variable=fid` 之后,综合日志里
    `Register 'fid' is power-on initialization` **依旧存在**(HLS 仍保留 `#0 fid = 32'd0;` 上电值,
    警告只是如实描述"它有上电值")。我第一版就是按"警告消失=生效"判断的,**差点误判成 pragma 无效**。
    ✅ **正确判据:看生成的 RTL** —— 计数器要出现在
    ```verilog
    always @ (posedge ap_clk) begin
        if (ap_rst_n_inv == 1'b1) begin  fid <= 32'd0;  end   // ← 这才叫"有复位"
        else begin ... fid <= fid + 1; end
    end
    ```
    ⚠️ 通用教训:**综合日志的"警告消失/出现"经常不是充分判据**;要确认某个 pragma 是否落地,
    去看 `csynth.rpt` 的 Implemented/Ignored Pragmas 表 **+ 生成的 RTL**。
    另:该 pragma 写在变量声明**之后**(与 BIND_STORAGE 同一条顺序要求)。

32. **窄带/低频信号的"加速比"叙事要小心:先算清实时预算** ※P0-4 实测※(2026-09-11)
    实测某 63 阶 Q15 FIR 在 PC 上逐样本处理约 **1.9 µs/样本**;而 30 Hz 的时间序列
    **每秒只需 30 个样本 ≈ 58 µs**,占 1 秒实时预算的 **0.006%**。
    也就是说:**"PS 跑不动"根本不成立** —— 把"加速 192×"当卖点会被评委一句话问穿。
    ✅ 正确叙事(写报告/M4 对比时用):①**延迟与抖动的确定性**(不受 GC/GIL/调度影响);
    ②**释放 PS 主核**(让给 MediaPipe/指标/界面);③**数据不出 PL**(与像素链同源)。
    ⚠️ 反过来,**像素链 IP 的吞吐叙事是硬的**(640×480×3B×30fps = 27.6 MB/s,逐像素在 PS 上会吃掉大量预算);
    **两类 IP 要分开写,不要用同一套话术**。

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
| 2026-09-10 | C 线 | **BRAM 问题闭环**:三组对照实验(320×240→64、512×512→128、640×480→256)坐实"片内数组按 **2 的幂地址空间**分配";据此升 `rgb2gray` v2(新增 3/5 相位抽取,640×480→384×288,8/8+10/10,Fmax 137.46 MHz)与 `motion_quality` v2(工作尺寸 384×288,**BRAM 64 = 23%**);三个 IP 合计 BRAM 23%,留出 216 个给 M3。常见坑第 19 条补入"台阶表 + 实际解法 + 点采样混叠取舍" |
| 2026-09-10 | C 线 | **C3.5 cosim 完成**:三个 IP 全部 RTL 协同仿真 PASS(Layer1 28/8/6 + Layer2 **45/10/9** 黄金参考),**无一死锁**,"csim 不建模流深度"这一最大未知项关闭;实测每调用拍数并取得固定开销(24/29/76 拍)。`run_hls.tcl` 支持 `HLS_EXEC` 环境变量并**修复 cosim 漏传 `-argv` 导致只验 Layer 1 的假通过**。追加常见坑 22~24:cosim 的 `-argv` 不继承(假通过陷阱)、cosim 自带死锁监控(实测灌满整帧不死锁,但上板仍需确认)、用事务时间戳白拿每帧真实延迟 |
| 2026-09-11 | C 线 | **C5 `fir_filter` 完成**:N=63 Q15 带通 @30fps,Hamming 窗,**−6 dB 口径 0.70/3.5 Hz**;csim **8/8 + 16/16**、**比对容差从 ±1 LSB 收紧为 0**、cosim PASS、II=1、Fmax 146.97 MHz、LUT 4077/FF 6172/**BRAM 0**/DSP 25。新增 `design_fir_coeffs.py`(系数设计)、`gen_fir_vectors.py`(向量+黄金参考,解析 C 头拿系数)、`host_model_fir.cpp`(**主机端算术模型**,秒级自检且可本机运行)、`fir_coeffs_q15.h`(冻结系数唯一来源)。追加常见坑 25~29:FIR 过渡带公式与"−6dB vs −3dB"口径的取舍方法、`static const` 系数表别写 ARRAY_PARTITION、**修正坑 #17 的表述**(static 是"上电初始化/复位不清零",不是"C 不给初值")、有状态 IP 必须测"分段==一次调用"、csim 里 static 计数器跨测试层累加导致误判 |
| 2026-09-11 | C 线 | **P0 四项收口**。① **契约 4.6 节**:补 `fir_filter` 输入序列的 **Q1.15 量化口径**(整数权威式、中心 128→0、合法均值永不裁剪、`count==0` 帧丢弃),实现 `sim/q15_ref.py`(5 条性质自检 PASS,含 `--sums-csv` 给 A 线用)。② **四个 IP 计数器加 `#pragma HLS RESET`**:随 `ap_rst_n` 清零,四 IP csim 回归全过、代价 **+2 LUT**、Fmax 不变,风险表第 8 条关闭(`report/counters_reset_v1.md`)。③ **M3 系统级预算**(`report/m3_system_budget_v1.md`):四 IP 实测占用 + 四种接入方案(推荐"像素链 DMA + 时间序列走 Stream FIFO") + 9 条上板验收门限 + 降级路径。④ **M4 基线**(`report/m4_baseline_v1.md` + `metrics/scripts/bench_filter_ps.py`):PS 侧实测基线、PL 延迟推算口径、上板测量方法。追加坑 30~32:软件的"流式实现"必须跨调用保持状态(与硬件"段间保持"同一个坑,自检当场抓到 bug)、**加了复位后 `power-on initialization` 警告不会消失,判据要看 RTL 复位分支**、窄带信号别拿"加速比"当卖点(先算实时预算:实测只占 1 秒的 0.006%) |
