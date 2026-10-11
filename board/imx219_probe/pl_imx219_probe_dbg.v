//=============================================================================
// pl_imx219_probe_dbg.v -- 探针 + ILA 的顶层封装
//-----------------------------------------------------------------------------
// 目的：不用 USB-TTL 转接板，直接经板载 JTAG 把探针的结论读回来。
//   探针在 SQ_HOLD 之后 hit_n / hit_a[0] / regv16[0..1] 就冻结了，
//   所以 ILA 用 run_hw_ila -trigger_now 抓【当前值】即可，不需要配触发条件、
//   也不需要大深度（状态是静态的）。
//
// 探针端口与 pl_imx219_probe 一一对应，只是多了 ILA 需要的那几个 debug 口。
//=============================================================================
`timescale 1ns / 1ps

module pl_imx219_probe_dbg (
    input  wire       clk,
    input  wire       rst_n,
    output wire       mclk,
    inout  wire       sccb_scl,
    inout  wire       sccb_sda,
    output wire       uart_tx,
    output wire [3:0] led
);

    wire [2:0] dbg_sq;
    wire [3:0] dbg_hit_n;
    wire [7:0] dbg_hit0;
    wire [7:0] dbg_hit1;
    wire [7:0] dbg_id0;
    wire [7:0] dbg_id1;
    wire [7:0] dbg_id80;
    wire [7:0] dbg_id81;

    pl_imx219_probe #(
        .CLK_HZ     (50_000_000),
        .BAUD       (115200),
        .SCL_HZ     (100_000),
        .PROBE_ADDR (7'h10),
        .PWRUP_MS   (200),
        .DRIVE_MCLK (0)
    ) u_probe (
        .clk       (clk),
        .rst_n     (rst_n),
        .mclk      (mclk),
        .sccb_scl  (sccb_scl),
        .sccb_sda  (sccb_sda),
        .uart_tx   (uart_tx),
        .led       (led),
        .dbg_sq    (dbg_sq),
        .dbg_hit_n (dbg_hit_n),
        .dbg_hit0  (dbg_hit0),
        .dbg_hit1  (dbg_hit1),
        .dbg_id0   (dbg_id0),
        .dbg_id1   (dbg_id1),
        .dbg_id80  (dbg_id80),
        .dbg_id81  (dbg_id81)
    );

    // ⚠️ Vivado BASIC 授权的 ILA 最多 5 个 probe，超过会报
    //    "ERROR: [Chipscope 16-620] ... more than 5 probes enabled"（实测踩过）。
    //    所以把 dbg_sq 与 dbg_hit_n 拼成一个 7-bit probe，只占 4 个 probe。
    //    probe 宽度必须与 build_dbg.tcl 里的 CONFIG.C_PROBEn_WIDTH 逐条一致。
    ila_0 u_ila (
        .clk    (clk),
        .probe0 ({dbg_sq, dbg_hit_n}),    // [0] 7 bits  — {sq[2:0], hit_n[3:0]}；sq=6 表示测完
        .probe1 ({dbg_hit1, dbg_hit0}),   // [1] 16 bits — 两个命中地址（0x20=IMX219, 0xA0=24C32）
        .probe2 ({dbg_id1, dbg_id0}),     // [2] 16 bits — 16bit 地址方式读到的 MODEL_ID_H/L
        .probe3 ({dbg_id81, dbg_id80})    // [3] 16 bits — 8bit 地址方式读到的 MODEL_ID_H/L
    );

endmodule
