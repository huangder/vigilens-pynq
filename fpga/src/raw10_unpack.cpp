// =============================================================================
//  raw10_unpack.cpp  ——  MIPI CSI-2 RAW10 解包 IP（5 字节 -> 4 个 10 bit 像素）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1（Mizar-Z7020 / PYNQ-Z2 同一颗）  目标时钟：10 ns
//  契约：**尚未进入 docs/interface.md** —— 接口提案见 docs/19 §6（待 A/B 会签）。
//        ⚠️ 本文件是 C 线按 docs/19 方案先行开发的产物；契约未冻结前，
//           偏移量/字段名都可能变，**不得被 A/B 线当作已冻结接口引用**。
//
//  为什么需要这个 IP：
//    新像素源是 Raspberry Pi Camera Module 2（IMX219），它经 MIPI CSI-2 输出的是
//    **RAW10 打包流**——MIPI 规定每 4 个 10 bit 像素压进 5 个字节（省 20% 带宽）。
//    现有四个 IP 的输入口径全是"每 beat 一个像素"（RGB888 或 gray8），
//    所以打包 -> 逐像素 这一步必须有人做。放在 PL 做，PL 就不只是"转发"。
//
//  【冻结口径一：RAW10 打包格式】（MIPI CSI-2 规范；A 线必须用同一式）
//    每 5 字节一组，含 4 个像素 P0..P3，字节 b0..b4：
//        P0 = (b0 << 2) | ((b4 >> 0) & 0x3)
//        P1 = (b1 << 2) | ((b4 >> 2) & 0x3)
//        P2 = (b2 << 2) | ((b4 >> 4) & 0x3)
//        P3 = (b3 << 2) | ((b4 >> 6) & 0x3)
//    即：先 4 个高 8 位，第 5 字节装 4 个低 2 位（P0 在最低位）。
//
//  【冻结口径二：行内独立打包】
//    每行恰好 width/4 个 5 字节组，**不跨行共享字节**（要求 width 是 4 的整数倍）。
//    640 / 4 = 160 组/行；1920 / 4 = 480 组/行。都不需要跨行拼接。
//
//  【输入 AXI-Stream 口径与一处未验证的集成假设】
//    输入一拍（beat）= 一个 5 字节组，装在 64 bit TData 的低 40 位，`tkeep = 0b11111`。
//    选 64 bit 而不是 40 bit 作为 TData 宽度，是因为 AXI4-Stream / MIPI CSI-2 RX 子系统的
//    常用宽度是 8/16/32/64；40 bit 虽然字节对齐但不属于常见档位。
//    ⚠️【未验证】Xilinx MIPI CSI-2 RX 子系统在 RAW10 下**实际**吐出的打包形式与 beat 宽度，
//       必须上板用 ILA 抓一次才能确认；若它本身就按"每 beat 一个像素"输出（某些配置下如此），
//       则本 IP 退化为直通，改用 `pix_out` 侧对齐即可。**这是本 IP 唯一的集成假设。**
//
//  AXI-Stream 语义（沿用项目约定）：
//    输入 TUSER = 一帧的第一个**组**、TLAST = 一行的最后一个**组**；
//    输出 TUSER = 一帧的第一个**像素**、TLAST = 一行的最后一个**像素**。
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<64, 1, 1, 1> axis_raw10_t;   // 输入：5 有效字节/beat（tkeep=0b11111）
typedef ap_axiu<16, 1, 1, 1> axis_pix10_t;   // 输出：1 像素/beat（10 bit 有效，高位补 0）

void raw10_unpack(hls::stream<axis_raw10_t> &raw_in,
                  hls::stream<axis_pix10_t> &pix_out,
                  ap_uint<16> width,          // 输入：行像素数（必须是 4 的整数倍）
                  ap_uint<16> height,         // 输入：行数
                  ap_uint<16> &words_per_line,// 输出：每行的 5 字节组数（= width/4）
                  ap_uint<32> &pixel_count,   // 输出：本帧解出的像素数（= width*height）
                  ap_uint<32> &frame_id)      // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（偏移见 docs/19 §6 的接口提案；契约冻结后以 docs/interface.md 为准）----
#pragma HLS INTERFACE axis      port=raw_in
#pragma HLS INTERFACE axis      port=pix_out
#pragma HLS INTERFACE s_axilite port=width          bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=height         bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=words_per_line bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=pixel_count    bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=frame_id       bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=return         bundle=ctrl

    static ap_uint<32> fid = 0;
// 与四个既有 IP 一致：帧计数随 ap_rst_n 归零，而不是只靠上电初始化
//（PS 若按"frame_id 从 1 开始"做首帧同步，复位后不能失配）
// ⚠️ pragma 必须写在变量声明之后
#pragma HLS RESET variable=fid

    const int wpl = (int)(width >> 2);          // 每行的 5 字节组数
    ap_uint<32> pcnt = 0;

    // -------------------------------------------------------------------------
    //  流水线结构（2026-10-01 改）
    // -------------------------------------------------------------------------
    //  原来是**两层循环**：外层 y、内层 g，`#pragma HLS PIPELINE II=1` 写在内层。
    //  HLS 于是把"内层循环体"当一个 pipeline：它读 1 组、吐 4 个像素。
    //  实测 csynth 报 `Target II = 1, Final II = 4` —— 这里的 II 是**每组**的：
    //  1 组 / 4 拍 ⇒ **每拍只有 1 个像素**（不是 II=1 应有的 1 组/拍 = 4 像素/拍）。
    //  1280×720@60 = 55.3 M 像素/s；每拍 1 像素就要 55.3 MHz 才够，看似够，
    //  但 1080p@45 = 93.3 M 像素/s 就**超了**，且这个数还没算行消隐与 CSI-2 开销 ——
    //  所以它是 docs/20「720p60」与 v1.5 两档的**共同吞吐瓶颈**。
    //
    //  改法：把 y、g **合并成一条扁平循环**，一次迭代处理**一整组**（读 1 拍、吐 4 拍）。
    //  这样 pipeline 的 II 单位变成"组"，II=1 即 **4 像素/拍**（快 4 倍），
    //  与文件头注释里"一拍进、四拍出"的原意一致。
    //
    //  ⚠️ 像素顺序必须与既有黄金参考一致：Python 侧是行内按组顺序、组内 k=0..3，
    //     故扁平化只能合并成 `idx = y*wpl + g`，**不能**改成按列或按 k 优先。
    // -------------------------------------------------------------------------
    const int total_groups = (int)height * wpl;
    // 行内组号用**自由计数器**推进，不用 `idx / wpl` 这种运行时除法 ——
    // 除法器会在流水线里占一拍甚至多拍，把刚拿回来的 II=1 又赔掉。
    int g = 0;
    for (int idx = 0; idx < total_groups; idx++) {
#pragma HLS PIPELINE II=1
        axis_raw10_t w = raw_in.read();

        // 取 40 位（低 5 字节）；高位 3 字节按 tkeep 语义不应被使用
        ap_uint<8> b0 = w.data(7, 0);
        ap_uint<8> b1 = w.data(15, 8);
        ap_uint<8> b2 = w.data(23, 16);
        ap_uint<8> b3 = w.data(31, 24);
        ap_uint<8> b4 = w.data(39, 32);

        ap_uint<10> p[4];
#pragma HLS ARRAY_PARTITION variable=p complete
        p[0] = ((ap_uint<10>)b0 << 2) | (ap_uint<10>)(b4 & 0x3);
        p[1] = ((ap_uint<10>)b1 << 2) | (ap_uint<10>)((b4 >> 2) & 0x3);
        p[2] = ((ap_uint<10>)b2 << 2) | (ap_uint<10>)((b4 >> 4) & 0x3);
        p[3] = ((ap_uint<10>)b3 << 2) | (ap_uint<10>)((b4 >> 6) & 0x3);

        for (int k = 0; k < 4; k++) {
#pragma HLS UNROLL
            axis_pix10_t q;
            q.data = (ap_uint<16>)p[k];           // 10 bit 有效，高 6 位为 0
            q.keep = 0x3;                          // 16 bit -> 2 字节
            q.strb = 0x3;
            q.user = (idx == 0 && k == 0) ? 1 : 0;              // 输出帧首像素
            q.last = (g == wpl - 1 && k == 3) ? 1 : 0;          // 输出行末像素
            q.id   = w.id;
            q.dest = w.dest;
            pix_out.write(q);
        }

        pcnt += 4;

        // 行内组号推进：到行末回到 0（与原来内层 g 的语义一致）
        if (g == wpl - 1) {
            g = 0;
        } else {
            g++;
        }
    }

    words_per_line = (ap_uint<16>)wpl;
    pixel_count    = pcnt;

    fid++;
    frame_id = fid;
}
