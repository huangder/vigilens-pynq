`timescale 1ns / 1ps
// =============================================================================
//  axis_tlast_suppress —— 抑制 AXI4-Stream 的行末 TLAST
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//
//  背景：rgb2gray 的输出 TLAST 是「输出行末像素」语义（契约冻结，tb 黄金参考校验，
//        不能改），经 motion_quality 透传后直连 axi_dma 的 S_AXIS_S2MM 时，
//        S2MM 会在**第一行末尾**就遇 TLAST 提前完成传输，只回读第一行（384 字节），
//        导致 dma_test / hw_sw_compare 的灰度比对必然失败。
//
//  修法：本模块纯组合逻辑透传 TDATA/TVALID/TREADY/TKEEP，仅把 m_axis_tlast 强制置 0，
//        让 S2MM 按字节长度（110592 字节整帧）完成回读。不动 HLS 源码与契约。
//
//  端口：8-bit 灰度流（ap_axiu<8,1,1,1> 的 data/keep，strb/user/id/dest 由 BD 自然丢弃，
//        与原先 gray_out→S2MM 直连时的行为一致）。
// =============================================================================
module axis_tlast_suppress (
    input  wire       aclk,
    input  wire       aresetn,
    // slave（输入，8-bit 灰度流）
    input  wire [7:0] s_axis_tdata,
    input  wire       s_axis_tvalid,
    output wire       s_axis_tready,
    input  wire [0:0] s_axis_tkeep,
    input  wire       s_axis_tlast,
    // master（输出）
    output wire [7:0] m_axis_tdata,
    output wire       m_axis_tvalid,
    input  wire       m_axis_tready,
    output wire [0:0] m_axis_tkeep,
    output wire       m_axis_tlast
);
    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tvalid = s_axis_tvalid;
    assign s_axis_tready = m_axis_tready;
    assign m_axis_tkeep  = s_axis_tkeep;
    assign m_axis_tlast  = 1'b0;   // 抑制行末 TLAST，S2MM 按长度完成整帧回读

    // aclk / aresetn 仅用于把 AXI4-Stream 接口关联到时钟域（组合逻辑透传，不使用它们）
endmodule
