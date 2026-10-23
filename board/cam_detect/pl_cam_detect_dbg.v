//=============================================================================
// pl_cam_detect_dbg.v —— 「FFC 处 MIPI 摄像头是否连上」探针的 ILA 包装（只用于上板取数）
//-----------------------------------------------------------------------------
// 用途
//   把 `pl_cam_detect` 的四条调试总线（dbg0..dbg3）接到一个 4 探针 ILA 上，
//   由 PC 侧 `board/check_camera.py` 读回 ILA 的 CSV 并给出「连上 / 没连上」的结论。
//
// 为什么要包装一层
//   `create_ip -name ila` 生成的 ILA 必须有一个实例，而 `pl_cam_detect` 本身
//   要保持"能被纯仿真验证"的样子（不带任何 Xilinx IP 依赖）。所以 ILA 只出现在
//   这个包装模块里；`tb_pl_cam_detect.v` 仿真的是 `pl_cam_detect` 本体，与本文无关。
//
// 构建
//   由 `board/cam_detect/build_dbg.tcl` 创建 ILA IP（名字必须是 ila_0）并综合实现。
//   ⚠️ 单独用 `xvlog` 做语法检查时 ila_0 是黑盒，能过；但**不能在 xsim 里 elaborate**
//      （没有 ila_0 的实现）——探针的仿真验证走 `tb_pl_cam_detect.v`，不走本文。
//
// 探针映射（与 `pl_cam_detect.v` 的 dbg0..dbg3 定义**逐位一致**；改一处必须改三处：
//   本文件、`pl_cam_detect.v` 的 dbg 赋值、`board/check_camera.py` 的解码表）
//   probe0[31:0] = dbg0 = { 7'b0, cam_ack, scan_a[7:0], step[3:0], ms[3:0],
//                           mclk_seen, mclk_self, i2c_act, pwr_done, hit_n[3:0] }
//   probe1[63:0] = dbg1 = { rec0[31:0], hit_a[0][7:0], mclk_period[15:0], mclk_npulse[7:0] }
//   probe2[63:0] = dbg2 = { rec1[31:0], hit_a[1][7:0], hit_a[2][7:0], hit_a[3][7:0], hit_a[4][7:0] }
//   probe3[63:0] = dbg3 = { cam16_rec[31:16], cam8_rec[31:0], hit_a[5][7:0], hit_a[6][7:0] }
//
// 「相机连上了」的判据（由 check_camera.py 判定，不在硬件里判）
//   ① hit_n ≥ 1            —— 总线上至少有器件应答（否则先查供电/上拉/排线）
//   ② cam_ack = 1          —— 7bit 0x10（8bit 0x20）这台相机应答了
//   ③ cam16_rec/cam8_rec 的高 16 位 == 0x0219（IMX219 的 MODEL_ID）—— 最硬的证据
//   ④ mclk_seen / mclk_period 给出 CAM_CLK(pin12) 上量到的频率
//      f_cam = MEAS_N * CLK_HZ / mclk_period（MEAS_N=4096、CLK_HZ=50_000_000）
//      ⇒ period=4096 → 50 MHz；period=8533 → 24 MHz；period=0/饱和 0xFFFF → 没量到/<3.1 MHz
//
// ⚠️ 已知风险（写入 README，别当成结论）
//   板上 U19 经 R156 给 CAM_CLK 送的是 **50 MHz**，而 IMX219 的 INCK 规格是 6~27 MHz
//   ⇒ 即使排线插好、SCCB 通了，相机也可能不启动。所以本探针**默认不驱动** CAM_CLK
//   （DRIVE_MCLK_SEL=0，只测量）；要反向验证"是不是时钟不对"，把下面那一个
//   localparam 改成 1'b1 重建即可（此时 FPGA 驱动 50/2=25 MHz）。
//=============================================================================

`timescale 1ns / 1ps

module pl_cam_detect_dbg (
    input  wire        clk,         // H16，50 MHz 有源晶振
    input  wire        rst_n,       // R19，PL_KEY1
    inout  wire        mclk,        // U9  CAM_CLK  = 连接器 pin12
    inout  wire        sccb_scl,    // A20 CAM_SCL  = pin13
    inout  wire        sccb_sda,    // E17 CAM_SDA  = pin14
    output wire [3:0]  led          // G14/C20/B20/H17（低电平点亮）
);

    //------------------------------------------------------------------------
    // 与 pl_cam_detect 的默认参数保持一致；DRIVE_MCLK_SEL 是本包装唯一的"开关"
    //------------------------------------------------------------------------
    localparam CLK_HZ      = 50_000_000;
    localparam SCL_HZ      = 100_000;      // 2026-10-20：回到标准 100 kHz（树莓派也用这个速率，已证能驱动本模组）
    localparam PWRUP_MS    = 200;          // 2026-10-20：回到 200 ms（1000 ms 那轮无差别）
    localparam RESET_MS    = 20;           // ★ 2026-10-20 新增：先按住 pin11 20 ms（MCLK 已在跑）再释放
    localparam CAM_ADDR    = 7'h10;        // IMX219 的 7bit 地址
    localparam MCLK_DIV    = 2;            // DRIVE_MCLK_SEL=1 时输出 50/2 = 25 MHz
    localparam MEAS_N      = 4096;         // 测频窗口：每 4096 个 cam_clk 翻转一次
    //  ★ DRIVE_MCLK_SEL = 1 ⇒ PL 自己给相机输出 INCK（50/2 = 25 MHz，落 IMX219
    //    Table 44 的 6~27 MHz 内）。2026-10-19 原理图复核确认：CAM_CLK(U9) 全网
    //    没有第二驱动源，U19 的 50 MHz 走的是 PL_CLK_50M(H16)，所以【不会对打】
    //    ——旧注释"会与 U19 同时驱动"是误读，见 docs/34 §2。
    localparam DRIVE_MCLK_SEL = 1'b1;   // 2026-10-20 复测：回到"PL 给 25 MHz INCK"的标准配置
    localparam MCLK_STATIC_SEL = 0;     // 0=按 DRIVE_MCLK（此处=高阻）；1=常低；2=常高
    //  ★ CAM_PWR_EN_SEL = pin11(CAM_GPIO) 的电平（H17→R33→pin11）。2026-10-20 起做成开关：
    //    各厂商对 pin11 的定义不一致 —— RPi/Arducam 说 POWER-EN 高有效，而本模组
    //    (Waveshare IMX219-77) 面向 Jetson，Jetson 侧 pin11 有可能是 PWDN（高有效=掉电）。
    //    第一次拉高（=1）实测仍无 cam 应答；本实验改成 0，用来判定极性。
    //    改这一行（1'b1 / 1'b0）→ 重建 → 编程 → 抓 ILA → check_camera.py 判读，即可换极性。
    localparam CAM_PWR_EN_SEL = 1'b1;   // 2026-10-20 复测：pin11 高（POWER-EN 高有效）

    wire [31:0] dbg0;
    wire [63:0] dbg1;
    wire [63:0] dbg2;
    wire [63:0] dbg3;

    pl_cam_detect #(
        .CLK_HZ      (CLK_HZ),
        .SCL_HZ      (SCL_HZ),
        .PWRUP_MS    (PWRUP_MS),
        .CAM_ADDR    (CAM_ADDR),
        .DRIVE_MCLK  (DRIVE_MCLK_SEL),
        .MCLK_DIV    (MCLK_DIV),
        .MEAS_N      (MEAS_N),
        .MCLK_STATIC (MCLK_STATIC_SEL),
        .CAM_PWR_EN  (CAM_PWR_EN_SEL),
        .RESET_MS    (RESET_MS)
    ) u_dut (
        .clk      (clk),
        .rst_n    (rst_n),
        .mclk     (mclk),
        .sccb_scl (sccb_scl),
        .sccb_sda (sccb_sda),
        .led      (led),
        .dbg0     (dbg0),
        .dbg1     (dbg1),
        .dbg2     (dbg2),
        .dbg3     (dbg3)
    );

    //------------------------------------------------------------------------
    // ILA：由 build_dbg.tcl 用 create_ip 生成（CONFIG.C_NUM_OF_PROBES=4，
    //      C_PROBE0_WIDTH=32，C_PROBE1..3_WIDTH=64，C_DATA_DEPTH=2048）
    //------------------------------------------------------------------------
    ila_0 u_ila (
        .clk     (clk),
        .probe0  (dbg0),
        .probe1  (dbg1),
        .probe2  (dbg2),
        .probe3  (dbg3)
    );

endmodule
