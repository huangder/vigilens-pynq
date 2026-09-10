// =============================================================================
//  roi_statistic.cpp  ——  ROI 像素统计 IP（v1，C2 冻结接口）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）
//  目标器件：xc7z020clg400-1 (PYNQ-Z2)   目标时钟：10 ns (100 MHz)
//  契约：docs/interface.md 第 3.2 节（寄存器映射与精度已冻结，改接口须先改契约）
//
//  功能：
//    1) 像素流透传：video_in -> video_out，原样保留 TUSER / TLAST / TKEEP / TSTRB
//    2) 统计 ROI（半开区间 [x0,x1) × [y0,y1)）内像素的 R/G/B 累加和与像素个数
//
//  AXI-Stream 语义（务必记住，踩坑重灾区）：
//    TUSER = 1 -> 一帧的第一个像素
//    TLAST = 1 -> 一行的最后一个像素（不是一帧！）
//    TKEEP / TSTRB = 0b111（24 bit = 3 byte）
//
//  通道顺序：TDATA[7:0]=R, TDATA[15:8]=G, TDATA[23:16]=B   <-- RGB，不是 BGR
//
//  数值精度：全整数运算，无定点截断。
//    sum_r/g/b 上限 = 640*480*255 = 78,336,000 < 2^32 -> 整帧满幅不溢出
//    count     上限 = 640*480     = 307,200     < 2^19
//    因此与 Python/NumPy 黄金参考的比对口径是【逐点严格相等，容差 0】
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

// 24 bit RGB888 像素流：data=24, user=1, id=1, dest=1
typedef ap_axiu<24, 1, 1, 1> axis_pix_t;

void roi_statistic(hls::stream<axis_pix_t> &video_in,
                   hls::stream<axis_pix_t> &video_out,
                   ap_uint<11> roi_x0,      // ROI 左边界（含）
                   ap_uint<11> roi_y0,      // ROI 上边界（含）
                   ap_uint<11> roi_x1,      // ROI 右边界（不含）
                   ap_uint<11> roi_y1,      // ROI 下边界（不含）
                   ap_uint<16> width,       // 图像宽（640）
                   ap_uint<16> height,      // 图像高（480）
                   ap_uint<32> &sum_r,      // 输出：ROI 内 R 累加和
                   ap_uint<32> &sum_g,      // 输出：ROI 内 G 累加和
                   ap_uint<32> &sum_b,      // 输出：ROI 内 B 累加和
                   ap_uint<32> &count,      // 输出：ROI 内像素个数
                   ap_uint<32> &frame_id)   // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（寄存器偏移在 docs/interface.md 冻结，勿随意改）----------------
#pragma HLS INTERFACE axis      port=video_in
#pragma HLS INTERFACE axis      port=video_out
#pragma HLS INTERFACE s_axilite port=roi_x0   bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=roi_y0   bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=roi_x1   bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=roi_y1   bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=width    bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=height   bundle=ctrl offset=0x38
#pragma HLS INTERFACE s_axilite port=sum_r    bundle=ctrl offset=0x40
#pragma HLS INTERFACE s_axilite port=sum_g    bundle=ctrl offset=0x48
#pragma HLS INTERFACE s_axilite port=sum_b    bundle=ctrl offset=0x50
#pragma HLS INTERFACE s_axilite port=count    bundle=ctrl offset=0x58
#pragma HLS INTERFACE s_axilite port=frame_id bundle=ctrl offset=0x60
#pragma HLS INTERFACE s_axilite port=return   bundle=ctrl

    // 跨帧保持：帧计数（RTL 里体现为寄存器）
    static ap_uint<32> fid = 0;

    // 每帧清零的累加器
    ap_uint<32> r_acc = 0;
    ap_uint<32> g_acc = 0;
    ap_uint<32> b_acc = 0;
    ap_uint<32> cnt   = 0;

    ap_uint<16> x = 0;   // 当前列（0 .. width-1）
    ap_uint<16> y = 0;   // 当前行（0 .. height-1）

    const int total = (int)width * (int)height;   // 一帧像素数

    for (int i = 0; i < total; i++) {
#pragma HLS PIPELINE II=1

        axis_pix_t p = video_in.read();

        // 拆通道：R=[7:0], G=[15:8], B=[23:16]
        ap_uint<8> r = p.data(7, 0);
        ap_uint<8> g = p.data(15, 8);
        ap_uint<8> b = p.data(23, 16);

        // 半开区间判定：x0 <= x < x1 且 y0 <= y < y1
        // （与 NumPy 的 img[y0:y1, x0:x1] 完全等价，无需 ±1 心算）
        bool in_roi = (x >= roi_x0) && (x < roi_x1) &&
                      (y >= roi_y0) && (y < roi_y1);

        if (in_roi) {
            r_acc += r;
            g_acc += g;
            b_acc += b;
            cnt   += 1;
        }

        // 透传（user / last / keep / strb 全部原样保留，供 C9 回环测试）
        video_out.write(p);

        // 行列推进：TLAST 表示一行结束
        if (p.last) {
            x = 0;
            y++;
        } else {
            x++;
        }
    }

    sum_r = r_acc;
    sum_g = g_acc;
    sum_b = b_acc;
    count = cnt;

    fid++;
    frame_id = fid;
}
