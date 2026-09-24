// =============================================================================
//  pl_uart_echo.v —— Mizar-Z7 (7020) PL 侧最小「首次点灯 + UART 字节回环」
//  项目：知倦 / VigiLens
//
//  这是**第一次上板**用的最小设计，刻意不依赖任何 HLS IP / Block Design / DMA。
//  它要证明的事情只有 4 件（每件都能单独定位故障）：
//     1. bitstream 能下载进去、PL 配置成功
//     2. 50 MHz 时钟是对的（用 clk_test 分频方波，让外面测频反证）
//     3. 扩展口 IO 电平/引脚约束是对的（OpenMV 的字节能进得来）
//     4. 组合逻辑→引脚→外部器件的往返通路是通的（字节能原样回去）
//
//  ⚠️【未验证】本文件**没有综合过、没有上板过**。它按标准 8N1 UART 写法编写，
//     但**任何"能跑"的结论都必须以 Vivado 综合 + 上板实测为准**（见 AGENTS.md 铁律 2）。
//
//  ⚠️ 已知取舍（诚实标注，不要当成 bug）：
//     本设计只有 **1 字节的暂存深度，没有 FIFO**。若 OpenMV 连续满速发字节，
//     回环会**丢字节**。这是刻意的：第一次上板要的是一个"能看懂全流程"的设计，
//     不是能扛满速的 IP。丢字节正好用来验证上位机协议里的丢帧统计是否真的在工作。
//     （要抗满速，需要加 FIFO 或改用 AXI-Stream —— 那是 M3 的事。）
//
//  端口与 mizar_z7_openmv_uart.xdc 一一对应，改名请同步改约束。
// =============================================================================

`timescale 1ns / 1ps

module pl_uart_echo #(
    parameter integer CLK_HZ  = 50_000_000,  // 板上 PL_CLK_50M（H16）
    parameter integer BAUD    = 115200,      // 与 OpenMV 侧 UART_BAUD 一致
    parameter integer TEST_HZ = 1000         // clk_test 输出频率（方波）
) (
    input  wire       clk,
    input  wire       rst_n,      // 低有效（PL_KEY1，按下为低）
    input  wire       uart_rx,    // ← OpenMV P4 (TX)
    output wire       uart_tx,    // → OpenMV P5 (RX)
    output wire       clk_test,   // → 供外部测频，反证 PL 时钟
    output wire [3:0] led         // 低电平点亮
);

    localparam integer DIV      = CLK_HZ / BAUD;        // 50e6/115200 = 434
    localparam integer HALF     = DIV / 2;
    localparam integer TEST_DIV = CLK_HZ / (2 * TEST_HZ); // 半周期计数

    // ---------------------------------------------------------------------
    // 1) uart_rx 打三拍：跨时钟域 + 边沿检测（飞线进来的信号必须同步）
    // ---------------------------------------------------------------------
    reg rx_s0, rx_s1, rx_s2;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_s0 <= 1'b1; rx_s1 <= 1'b1; rx_s2 <= 1'b1;
        end else begin
            rx_s0 <= uart_rx;
            rx_s1 <= rx_s0;
            rx_s2 <= rx_s1;
        end
    end
    wire rx_fall = rx_s2 & ~rx_s1;   // 起始位下降沿（空闲为高）

    // ---------------------------------------------------------------------
    // 2) UART 接收：8N1，LSB first，在位中点采样
    // ---------------------------------------------------------------------
    localparam S_IDLE = 2'd0, S_START = 2'd1, S_DATA = 2'd2, S_STOP = 2'd3;

    reg [1:0]  rx_state;
    reg [15:0] rx_cnt;
    reg [2:0]  rx_nbit;
    reg [7:0]  rx_sh;
    reg        rx_valid;
    reg [7:0]  rx_data;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_state <= S_IDLE; rx_cnt <= 16'd0; rx_nbit <= 3'd0;
            rx_sh <= 8'd0; rx_valid <= 1'b0; rx_data <= 8'd0;
        end else begin
            rx_valid <= 1'b0;                      // 默认：单周期脉冲
            case (rx_state)
                S_IDLE: begin
                    if (rx_fall) begin
                        rx_cnt   <= 16'd0;
                        rx_state <= S_START;
                    end
                end
                S_START: begin
                    if (rx_cnt == HALF[15:0] - 16'd1) begin   // 起始位中点
                        rx_cnt <= 16'd0;
                        rx_state <= rx_s2 ? S_IDLE : S_DATA;  // 中点仍为高 = 毛刺，丢弃
                        rx_nbit  <= 3'd0;
                    end else begin
                        rx_cnt <= rx_cnt + 16'd1;
                    end
                end
                S_DATA: begin
                    if (rx_cnt == DIV[15:0] - 16'd1) begin
                        rx_cnt <= 16'd0;
                        rx_sh  <= {rx_s2, rx_sh[7:1]};        // LSB first
                        if (rx_nbit == 3'd7) rx_state <= S_STOP;
                        else                 rx_nbit  <= rx_nbit + 3'd1;
                    end else begin
                        rx_cnt <= rx_cnt + 16'd1;
                    end
                end
                S_STOP: begin
                    if (rx_cnt == DIV[15:0] - 16'd1) begin
                        rx_cnt   <= 16'd0;
                        rx_data  <= rx_sh;
                        rx_valid <= 1'b1;                     // 一个字节收齐
                        rx_state <= S_IDLE;
                    end else begin
                        rx_cnt <= rx_cnt + 16'd1;
                    end
                end
                default: rx_state <= S_IDLE;
            endcase
        end
    end

    // ---------------------------------------------------------------------
    // 3) UART 发送：收到就回发（1 字节浅暂存，见文件头"已知取舍"）
    // ---------------------------------------------------------------------
    reg        tx_busy;
    reg [9:0]  tx_sh;      // {stop, D7..D0, start}
    reg [15:0] tx_cnt;
    reg [3:0]  tx_nbit;
    reg        pend_valid;
    reg [7:0]  pend;

    assign uart_tx = tx_busy ? tx_sh[0] : 1'b1;   // 空闲为高

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_busy <= 1'b0; tx_sh <= 10'h3FF; tx_cnt <= 16'd0; tx_nbit <= 4'd0;
            pend_valid <= 1'b0; pend <= 8'd0;
        end else begin
            if (rx_valid && !pend_valid && !tx_busy) begin
                pend       <= rx_data;
                pend_valid <= 1'b1;
            end

            if (!tx_busy) begin
                if (pend_valid) begin
                    // bit0 = 起始位(0)，bit1..8 = D0..D7，bit9 = 停止位(1)
                    tx_sh      <= {1'b1, pend, 1'b0};
                    tx_busy    <= 1'b1;
                    tx_cnt     <= 16'd0;
                    tx_nbit    <= 4'd0;
                    pend_valid <= 1'b0;
                end
            end else begin
                if (tx_cnt == DIV[15:0] - 16'd1) begin
                    tx_cnt <= 16'd0;
                    tx_sh  <= {1'b1, tx_sh[9:1]};
                    if (tx_nbit == 4'd9) tx_busy <= 1'b0;
                    else                 tx_nbit <= tx_nbit + 4'd1;
                end else begin
                    tx_cnt <= tx_cnt + 16'd1;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // 4) clk_test：把 50 MHz 分频成 1 kHz 方波输出到扩展口
    //    外面（OpenMV 或示波器）测到 1.000 kHz ⇒ PL 时钟确实是 50 MHz。
    //    这条比"LED 亮了"强得多：它验证的是**频率**，不是"有没有电平"。
    // ---------------------------------------------------------------------
    reg [31:0] test_cnt;
    reg        test_tgl;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            test_cnt <= 32'd0;
            test_tgl <= 1'b0;
        end else if (test_cnt == TEST_DIV[31:0] - 32'd1) begin
            test_cnt <= 32'd0;
            test_tgl <= ~test_tgl;
        end else begin
            test_cnt <= test_cnt + 32'd1;
        end
    end
    assign clk_test = test_tgl;

    // ---------------------------------------------------------------------
    // 5) LED：心跳 + 收发指示（低电平点亮，所以取反）
    // ---------------------------------------------------------------------
    reg [23:0] hb_cnt;
    reg        hb;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hb_cnt <= 24'd0;
            hb     <= 1'b0;
        end else if (hb_cnt == 24'd12_500_000) begin   // ~0.5 s @50MHz
            hb_cnt <= 24'd0;
            hb     <= ~hb;
        end else begin
            hb_cnt <= hb_cnt + 24'd1;
        end
    end

    // 用一个"有活动"的短脉冲拉伸，否则 LED 闪一下人眼看不见
    reg [19:0] act_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)                     act_cnt <= 20'd0;
        else if (rx_valid || tx_busy)   act_cnt <= 20'd500_000;
        else if (act_cnt != 20'd0)      act_cnt <= act_cnt - 20'd1;
    end

    assign led[0] = ~hb;                       // 心跳
    assign led[1] = ~(act_cnt != 20'd0);       // UART 有收发活动
    assign led[2] = ~tx_busy;                  // 正在发送
    assign led[3] = ~test_tgl;                 // 跟 clk_test 同步的 1 kHz 指示（肉眼是常亮）

endmodule
