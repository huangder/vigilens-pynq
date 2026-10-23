// ============================================================================
// axis_tap.v —— AXI4-Stream 纯组合旁路 + 单 bit 观测引出（只用于 S4 上板取证）
//
// 背景：2026-10-19 S4 门限 5（DMA 回环）在板上"零进展且零错误"——MM2S/S2MM 都 RS=1、
//   SR=0（无 Halted、无 SLVErr/DecErr/DMAIntErr），但一个字节都不搬。寄存器层面已排除
//   SG 漏配（BUG-035）、长度位宽（BUG-036）、软复位、地址映射、HP0 地址段、ps7_init。
//   剩下的可能性只有"拍"上的：DMA 到底有没有拉 TVALID？下游到底有没有给 TREADY？
//
// 为什么要这个模块：Vivado 的 BD 里用 connect_bd_net 直接把探针接到某个 interface 的
//   成员 pin（如 axi_dma_0/m_axis_mm2s_tvalid）会**抢断 interface net**（实测报
//   BD 41-1271 + BD 41-759，下游输入被 tie 成 0）——那等于自己把数据面掐死。
//   而 system_ila 的 SLOT 监视器虽然不抢接口，但一个 AXIS 槽要占 9 个 probe，
//   超过本机 ILA 的 BASIC 授权（probe 数 >5 直接综合失败：
//   `[Chipscope 16-620] Detected an ILA instance ... with more than 5 probes enabled`）。
//
// 所以做法是：把这个**纯组合直通**小节插在数据通路上，用它的离散输出脚接普通 ila 的
//   5 个单 bit 探针。组合直通不改时序语义（与 board/axis_tlast_suppress.v 同理），
//   取证完成后应当从 BD 里摘掉。
//
// 探针取值（5 个）：
//   tap_s_tvalid —— 上游（DMA MM2S）是否拉起了 TVALID
//   tap_s_tready —— 下游（conv_rgb）是否给得出 TREADY
//   tap_m_tvalid —— 经过本模块后的 TVALID（应恒等于 tap_s_tvalid，用于确认旁路正常）
//   tap_m_tready —— 本模块看到的 TREADY（应恒等于 tap_s_tready）
//   tap_s_tlast —— 是否出现 TLAST（行/帧边界）
// ============================================================================

module axis_tap #(
    parameter integer WIDTH = 8
) (
    input  wire             aclk,
    input  wire             aresetn,

    // 上游（slave 侧）
    input  wire [WIDTH-1:0] s_axis_tdata,
    input  wire             s_axis_tvalid,
    output wire             s_axis_tready,
    input  wire             s_axis_tlast,
    input  wire             s_axis_tuser,

    // 下游（master 侧）
    output wire [WIDTH-1:0] m_axis_tdata,
    output wire             m_axis_tvalid,
    input  wire             m_axis_tready,
    output wire             m_axis_tlast,
    output wire             m_axis_tuser,

    // 观测引出（接 ila 的单 bit 探针）
    output wire             obs0,
    output wire             obs1,
    output wire             obs2,
    output wire             obs3,
    output wire             obs4
);

    // 纯组合直通：不插寄存器、不改协议、不动数据
    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tvalid = s_axis_tvalid;
    assign s_axis_tready = m_axis_tready;
    assign m_axis_tlast  = s_axis_tlast;
    assign m_axis_tuser  = s_axis_tuser;

    // 观测点
    assign obs0 = s_axis_tvalid;   // DMA MM2S 是否拉 TVALID
    assign obs1 = s_axis_tready;   // 下游是否给 TREADY
    assign obs2 = m_axis_tvalid;   // 直通后 TVALID（应等于 obs0）
    assign obs3 = m_axis_tready;   // 直通后 TREADY（应等于 obs1）
    assign obs4 = s_axis_tlast;    // TLAST 是否出现

    // aclk / aresetn 本模块不用，但保留端口以便 BD 内与其他 IP 同簇；
    // 悬空端口会让 Vivado 报 critical warning，故引出到一条 dummy 网（综合会被优化掉）。
    wire unused_ok = aclk & aresetn;

endmodule
