//=============================================================================
// pl_eeprom_dump_dbg.v —— EEPROM dump 探针的 ILA 包装（只用于上板取数）
//-----------------------------------------------------------------------------
// 把 `pl_eeprom_dump` 的四条 dbg 总线接到 4 探针 ILA 上，由 PC 侧
// `board/check_eeprom.py` 读回 CSV 并打印两台器件前 16 字节的内容。
//
// 探针映射（与 `pl_eeprom_dump.v` 的 dbg 赋值逐位一致；改一处必须改三处：
//   本文件、`pl_eeprom_dump.v` 的 dbg 赋值、`board/check_eeprom.py` 的解码表）
//   probe0[31:0] = dbg0 = { 16'hCAFE, last_qi[7:0], done, a_ok, b_ok,
//                           mclk_seen, fail_seen, 2'b00, devi }
//   probe1[63:0] = dbg1 = mem_a[0..7]   （byte0 在 [7:0]）
//   probe2[63:0] = dbg2 = mem_a[8..15]
//   probe3[63:0] = dbg3 = mem_b[0..7]
//
// 构建：`board/cam_detect/build_dump.tcl`（create_ip ila_0 + 综合实现）
// 读回：`board/cam_detect/read_dump.tcl`
// ⚠️ 单独用 xvlog 做语法检查时 ila_0 是黑盒，能过；不能在 xsim 里 elaborate。
//=============================================================================

`timescale 1ns / 1ps

module pl_eeprom_dump_dbg (
    input  wire        clk,         // H16，50 MHz
    input  wire        rst_n,       // R19，PL_KEY1
    inout  wire        mclk,        // U9  CAM_CLK  = pin12（本探针会驱动 25 MHz）
    inout  wire        sccb_scl,    // A20 CAM_SCL  = pin13
    inout  wire        sccb_sda,    // E17 CAM_SDA  = pin14
    output wire [3:0]  led          // G14/C20/B20/H17（低电平点亮）
);

    localparam CLK_HZ     = 50_000_000;
    localparam SCL_HZ     = 100_000;
    localparam PWRUP_MS   = 200;
    localparam DEV_A      = 7'h50;   // 板上 24C32(U18)
    localparam DEV_B      = 7'h58;   // 待判定归属
    localparam DRIVE_MCLK = 1;       // 驱动 CAM_CLK（板上无第二驱动源，见 docs/34 §2）
    localparam MCLK_DIV   = 2;       // 50/2 = 25 MHz
    localparam MEAS_N     = 4096;
    localparam NBYTES     = 16;

    wire [31:0] dbg0;
    wire [63:0] dbg1;
    wire [63:0] dbg2;
    wire [63:0] dbg3;

    pl_eeprom_dump #(
        .CLK_HZ     (CLK_HZ),
        .SCL_HZ     (SCL_HZ),
        .PWRUP_MS   (PWRUP_MS),
        .DEV_A      (DEV_A),
        .DEV_B      (DEV_B),
        .DRIVE_MCLK (DRIVE_MCLK),
        .MCLK_DIV   (MCLK_DIV),
        .MEAS_N     (MEAS_N),
        .NBYTES     (NBYTES)
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

    ila_0 u_ila (
        .clk     (clk),
        .probe0  (dbg0),
        .probe1  (dbg1),
        .probe2  (dbg2),
        .probe3  (dbg3)
    );

endmodule
