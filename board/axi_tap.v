// ============================================================================
// axi_tap.v —— AXI4 读通道（AR/R）纯组合直通小节 + 4 个单 bit 观测引出
//
// 为什么需要它（2026-10-19，S4 门限5 取证）：
//   axi_dma 的 MM2S 通道在 simple mode 下 SR 全是 0（既不 Idle、也不报错）、
//   而下游 conv_rgb 的 S_AXIS_TREADY 一直是 1（已在 axis_tap + ila 上抓到），
//   说明 DMA 根本没往外吐数据。要继续判断到底是
//     ① DMA 没发 AR（自身/上游问题），还是
//     ② AR 发了但 HP0 不回 R（PS 侧读通路问题），
//   就必须看 AR/R 这两条握手。
//
//   但 AXI 接口的成员脚**不能**用 connect_bd_net 单独去接 ILA —— 那样会把 IPI 的
//   interface net 抢断（实测 `[BD 41-1271] ... will not be connected as a part of the
//   interface connection`，结果是整条数据面被 tie 死）。而 system_ila 的 AXI 槽要 44 个
//   probe，超过本机 BASIC 授权的 5 上限（`[Chipscope 16-620]`）。
//   所以用这个**纯组合直通**小节把读通道夹在 DMA 与 SmartConnect 之间，
//   再拿它的离散观测脚去接普通 ila 的探针。
//
// ⚠️ 取证完必须把这一小节从 BD 里摘掉（它虽然只是连线，但没有功能价值）。
// ============================================================================
`timescale 1ns / 1ps

module axi_tap (
        input  wire        aclk,
        input  wire        aresetn,

    // ---- 从 DMA 的 M_AXI_MM2S 进来（读通道）----
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARADDR" *)
    input  wire [31:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARLEN" *)
    input  wire [7:0]  s_axi_arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARSIZE" *)
    input  wire [2:0]  s_axi_arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARBURST" *)
    input  wire [1:0]  s_axi_arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARCACHE" *)
    input  wire [3:0]  s_axi_arcache,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARPROT" *)
    input  wire [2:0]  s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI RLAST" *)
    output wire        s_axi_rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready,

    // ---- 到 SmartConnect 的 S00_AXI（同一批信号，原样透传）----
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARADDR" *)
    output wire [31:0] m_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARLEN" *)
    output wire [7:0]  m_axi_arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARSIZE" *)
    output wire [2:0]  m_axi_arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARBURST" *)
    output wire [1:0]  m_axi_arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARCACHE" *)
    output wire [3:0]  m_axi_arcache,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARPROT" *)
    output wire [2:0]  m_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARVALID" *)
    output wire        m_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI ARREADY" *)
    input  wire        m_axi_arready,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI RDATA" *)
    input  wire [31:0] m_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI RRESP" *)
    input  wire [1:0]  m_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI RLAST" *)
    input  wire        m_axi_rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI RVALID" *)
    input  wire        m_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm_rtl:1.0 M_AXI RREADY" *)
    output wire        m_axi_rready,

    // ---- 观测脚（接普通 ila 的 probe，名字故意不带 AXI 语义后缀，避免被推断成总线）----
    output wire        obs0,   // ARVALID（DMA 有没有把读请求发出来）
    output wire        obs1,   // ARREADY（SmartConnect 有没有接住）
    output wire        obs2,   // RVALID （HP0 有没有把数据回上来）
    output wire        obs3    // RREADY （DMA 是否在收）
);

    assign m_axi_araddr  = s_axi_araddr;
    assign m_axi_arlen   = s_axi_arlen;
    assign m_axi_arsize  = s_axi_arsize;
    assign m_axi_arburst = s_axi_arburst;
    assign m_axi_arcache = s_axi_arcache;
    assign m_axi_arprot  = s_axi_arprot;
    assign m_axi_arvalid = s_axi_arvalid;
    assign s_axi_arready = m_axi_arready;
    assign m_axi_rdata   = s_axi_rdata;
    assign m_axi_rresp   = s_axi_rresp;
    assign m_axi_rlast   = s_axi_rlast;
    assign m_axi_rvalid  = s_axi_rvalid;
    assign s_axi_rready  = m_axi_rready;

    assign obs0 = s_axi_arvalid;
    assign obs1 = s_axi_arready;
    assign obs2 = s_axi_rvalid;
    assign obs3 = s_axi_rready;

    // aclk/aresetn 只用于接口时钟域关联，不参与功能
    wire unused_ok = aclk & aresetn;

endmodule
