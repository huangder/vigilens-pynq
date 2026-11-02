`timescale 1ns / 1ps
// =============================================================================
//  axis_32to24_gearbox —— 把 32 bit 的 AXI4-Stream 重新对齐成 24 bit/像素
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//
//  为什么需要它（2026-10-24，见 fpga/report/t11_streaming_720p_20261024.md）：
//    PL 数据通路的吞吐天花板是 **MM2S 的流宽**。axi_dma 的 `c_m_axis_mm2s_tdata_width`
//    只允许 8/16/32/64/...（**24 直接被 IP 拒绝**），而 8 bit 意味着一个 RGB 像素要 3 拍：
//        1 B/时钟 × 100 MHz = 100 MB/s ⇒ 3 时钟/像素
//        720p 一帧 2,764,800 B ÷ 100 MB/s = 27.65 ms（实测 27.68 ms，≈36 fps）
//    改成 32 bit 后一拍 4 字节，但 DDR 里是**紧密 RGB888**（3 B/像素），
//    4 和 3 不是整数倍 ⇒ 拍边界会跨像素（Xilinx 的 axis_dwidth_converter 4→3 非整数比，
//    实测"永远不给 TREADY"，见 docs/16 BUG-035）。所以必须自己写这个"字节重整器"。
//
//  做法：一个 16 字节的小 FIFO。上游每拍推进 4 字节，下游每拍弹出 3 字节。
//        - 入口 4 B/时钟（比出口快），FIFO 满了就反压上游；
//        - 出口 3 B/时钟 = 1 像素/时钟，这正是三个 HLS IP 的处理能力；
//        - 字节序与原来的 conv_rgb(8→24) 完全一致：先出的字节放低 8 位
//          （TDATA[7:0]=R、[15:8]=G、[23:16]=B），所以下游 IP 一行都不用改。
//
//  语义边界（与 axis_tap / axis_tlast_suppress 同一套路，纯 AXI4-Stream）：
//    - TUSER：在本段第一个输出拍上拉一次（帧首像素）；
//    - TLAST：在上游 tlast 那一拍**最后一个字节被弹出**时拉（帧末像素）；
//    - 前提：一段传输的字节数是 12 的整数倍（4×3 的最小公倍数），否则最后一拍可能
//      凑不满 3 字节。本项目两档都满足（720p: 2,764,800 = 12×230,400；
//      640×480: 921,600 = 12×76,800），ROI/rgb2gray 本来也**不用** TLAST 判行。
//    - TKEEP/TSTRB 没有显式端口：与本目录既有的 axis_tap 一样，BD 里靠接口自动匹配
//      （下游 HLS IP 按 width/height 计数，不看 TKEEP）。
// =============================================================================

module axis_32to24_gearbox #(
    // 字节 FIFO 深度；4 的倍数。16 是"入口 4 B/拍、出口 3 B/拍"不产生空转的最小值附近
    parameter integer DEPTH = 16
) (
    input  wire        aclk,
    input  wire        aresetn,

    // 上游（slave，来自 axi_dma 的 M_AXIS_MM2S，32 bit）
    input  wire [31:0] s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tuser,

    // 下游（master，去 roi_statistic 的 video_in，24 bit）
    output wire [23:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser
);

    localparam integer PTRW = 4;   // DEPTH = 16

    reg  [7:0]        mem [0:DEPTH-1];
    reg  [PTRW-1:0]   head;        // 读指针（每次 +3）
    reg  [PTRW-1:0]   tail;        // 写指针（每次 +4）
    reg  [4:0]        count;       // 已存字节数 0..DEPTH

    reg               last_seen;   // 上游 tlast 已经推进 FIFO，等最后一个字节被弹出
    reg               user_seen;   // 上游 tuser 还没被本次输出用掉

    // 入口：count+4 <= DEPTH 才收；出口：凑够 3 字节才发
    assign s_axis_tready = (count <= (DEPTH - 4));
    assign m_axis_tvalid = (count >= 3);

    wire push = s_axis_tvalid && s_axis_tready;
    wire pop  = m_axis_tvalid && m_axis_tready;

    // 3 字节输出的组合读口（先入的字节在低 8 位，与 conv_rgb(8→24) 的字节序一致）
    wire [PTRW-1:0] h0 = head;
    wire [PTRW-1:0] h1 = head + 1'b1;
    wire [PTRW-1:0] h2 = head + 2'd2;
    assign m_axis_tdata = {mem[h2], mem[h1], mem[h0]};

    // 最后一个字节被弹出的那一拍拉 TLAST（本设计里一段传输的字节数是 12 的倍数）
    assign m_axis_tlast = m_axis_tvalid && last_seen && (count == 5'd3);
    assign m_axis_tuser = m_axis_tvalid && user_seen;

    always @(posedge aclk) begin
        if (!aresetn) begin
            head      <= {PTRW{1'b0}};
            tail      <= {PTRW{1'b0}};
            count     <= 5'd0;
            last_seen <= 1'b0;
            user_seen <= 1'b0;
        end else begin
            // 写：一次 4 字节，指针回绕靠位宽自然取模
            if (push) begin
                mem[tail]              <= s_axis_tdata[7:0];
                mem[tail + 1'b1]       <= s_axis_tdata[15:8];
                mem[tail + 2'd2]       <= s_axis_tdata[23:16];
                mem[tail + 2'd3]       <= s_axis_tdata[31:24];
                tail                   <= tail + 2'd2 + 2'd2;   // +4
            end

            // 读：一次 3 字节 = 1 个像素
            if (pop) begin
                head <= head + 2'd2 + 1'b1;                     // +3
            end

            count <= count + (push ? 5'd4 : 5'd0) - (pop ? 5'd3 : 5'd0);

            if (push && s_axis_tlast)
                last_seen <= 1'b1;
            else if (pop && m_axis_tlast)
                last_seen <= 1'b0;

            if (push && s_axis_tuser)
                user_seen <= 1'b1;
            else if (pop)
                user_seen <= 1'b0;
        end
    end

endmodule
