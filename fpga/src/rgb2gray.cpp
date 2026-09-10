// =============================================================================
//  rgb2gray.cpp  ——  RGB888 -> 缩小灰度流式 IP（v2：灰度化 + 3/5 相位抽取）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1 (PYNQ-Z2)   目标时钟：10 ns (100 MHz)
//  契约：docs/interface.md 第 3.4 节
//
//  为什么需要这个 IP：
//    原契约规定 motion_quality 的输入是 gray_in，但设计中没有任何环节产生灰度；
//    而 docs/00 §3.4 明确要求"PL 端完成 RGB/YUV 转换、**缩放**灰度化"。
//    若改由 PS 转灰度再 DMA，PL 就只剩帧差，会被看作"转发器"（《01》4.2 点名的致命短板）。
//    故在 PL 内新增本 IP，并顺带做缩放。
//
//  为什么顺带缩放（v2 的关键决定）：
//    motion_quality 要在片内保存"上一帧"，而 HLS 的片内数组按 **2 的幂地址空间**分配 BRAM：
//      640x480 = 307200 -> 2^19 -> 256 个 BRAM18 = 器件的 91%（放不下 DMA+互连）
//      384x288 = 110592 -> 2^17 ->  64 个 BRAM18 = 23%
//    三组对照实验（320x240→64、512x512→128、640x480→256）已证实该规律。
//    见 fpga/report/c4_rgb2gray_motion_quality_v1.md 第 5 节。
//
//  【冻结口径一：灰度公式】（整数，无浮点，A 线必须用同一式）
//      Y = (77*R + 150*G + 29*B + 128) >> 8
//    系数即 BT.601 的 0.299/0.587/0.114 按 x256 四舍五入：
//      0.299*256 = 76.5 -> 77 ; 0.587*256 = 150.3 -> 150 ; 0.114*256 = 29.2 -> 29
//    77+150+29 = 256 恰好 -> 纯白 255、纯黑 0（无需裁剪）；16 bit 内最大 65408（不溢出）
//    ⚠️ 本式与"按浮点系数四舍五入"（0.299*255 = 76.245 -> 76）差 1 LSB（本式给 77）。
//       这是口径选择，不是 bug。A 线若坚持用 cv2.cvtColor，须先按契约 4.4 节对拍。
//
//  【冻结口径二：缩放（3/5 相位抽取）】
//      保留 x%5 < 3 且 y%5 < 3 的像素（每 5 列取 3 列、每 5 行取 3 行）
//      640/5*3 = 384 列 ；480/5*3 = 288 行
//    * 是**点采样**（保留原始灰度值），不是均值 -> A 线用 np.ix_ 可逐位镜像
//    * 要求输入宽高都是 5 的整数倍（640、480 满足）
//    ⚠️ 点采样会保留混叠：运动检测可能因此更"敏感"（噪声被判为运动）。
//       若实测发现运动量偏噪，可改为块均值（黄金参考需同步改），或退回 320x240 的 2x2 均值。
//
//  AXI-Stream 语义：输入 TUSER = 输入帧首像素、TLAST = 输入行末像素；
//                   输出 TUSER = 输出帧首像素、TLAST = **输出行**末像素。
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<24, 1, 1, 1> axis_pix_t;    // 输入：RGB888
typedef ap_axiu<8,  1, 1, 1> axis_gray_t;   // 输出：8 bit 缩小灰度

#define RGB2GRAY_DECIM_NUM 3    // 每 DECIM_DEN 个像素保留前 NUM 个
#define RGB2GRAY_DECIM_DEN 5

void rgb2gray(hls::stream<axis_pix_t> &rgb_in,
              hls::stream<axis_gray_t> &gray_out,
              ap_uint<16> width,           // 输入宽（640，必须是 5 的整数倍）
              ap_uint<16> height,          // 输入高（480，必须是 5 的整数倍）
              ap_uint<16> &out_width,      // 输出：缩小后宽度（384）
              ap_uint<16> &out_height,     // 输出：缩小后高度（288）
              ap_uint<32> &pixel_count,    // 输出：本帧输出像素数（110592）
              ap_uint<32> &sum_gray,       // 输出：本帧灰度累加和（供光照质量评分）
              ap_uint<32> &frame_id)       // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（寄存器偏移见 docs/interface.md 第 3.4 节）---------------------
#pragma HLS INTERFACE axis      port=rgb_in
#pragma HLS INTERFACE axis      port=gray_out
#pragma HLS INTERFACE s_axilite port=width       bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=height      bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=out_width   bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=out_height  bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=pixel_count bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=sum_gray    bundle=ctrl offset=0x38
#pragma HLS INTERFACE s_axilite port=frame_id    bundle=ctrl offset=0x40
#pragma HLS INTERFACE s_axilite port=return      bundle=ctrl

    static ap_uint<32> fid = 0;

    const int total_in = (int)width * (int)height;
    const ap_uint<16> ow = (ap_uint<16>)((int)width  / RGB2GRAY_DECIM_DEN * RGB2GRAY_DECIM_NUM);
    const ap_uint<16> oh = (ap_uint<16>)((int)height / RGB2GRAY_DECIM_DEN * RGB2GRAY_DECIM_NUM);

    ap_uint<16> px = 0;      // 输入列相位 0..4
    ap_uint<16> py = 0;      // 输入行相位 0..4
    ap_uint<16> ox = 0;      // 输出列计数
    ap_uint<32> ocnt = 0;    // 输出像素总数
    ap_uint<32> acc  = 0;

    for (int i = 0; i < total_in; i++) {
#pragma HLS PIPELINE II=1

        axis_pix_t p = rgb_in.read();

        // 相位抽取：每 5 列取 3、每 5 行取 3（对应 x%5<3 && y%5<3）
        bool sel = (px < (ap_uint<16>)RGB2GRAY_DECIM_NUM) &&
                   (py < (ap_uint<16>)RGB2GRAY_DECIM_NUM);

        if (sel) {
            // 拆通道：R=[7:0], G=[15:8], B=[23:16]（RGB，不是 BGR）
            ap_uint<8> r = p.data(7, 0);
            ap_uint<8> g = p.data(15, 8);
            ap_uint<8> b = p.data(23, 16);

            // Y = (77R + 150G + 29B + 128) >> 8，16 bit 足够（最大 65408）
            ap_uint<16> y16 = (ap_uint<16>)((ap_uint<16>)77  * (ap_uint<16>)r +
                                            (ap_uint<16>)150 * (ap_uint<16>)g +
                                            (ap_uint<16>)29  * (ap_uint<16>)b +
                                            (ap_uint<16>)128);
            ap_uint<8> y = y16 >> 8;

            axis_gray_t q;
            q.data = y;
            q.keep = 0x1;                                  // 8 bit -> 1 byte
            q.strb = 0x1;
            q.user = (ocnt == 0) ? 1 : 0;                  // 输出帧首
            q.last = (ox == (ap_uint<16>)(ow - 1)) ? 1 : 0; // 输出行末
            q.id   = p.id;
            q.dest = p.dest;
            gray_out.write(q);

            acc  += y;
            ocnt += 1;

            if (ox == (ap_uint<16>)(ow - 1)) {
                ox = 0;
            } else {
                ox++;
            }
        }

        // 列相位推进
        if (px == (ap_uint<16>)(RGB2GRAY_DECIM_DEN - 1)) {
            px = 0;
        } else {
            px++;
        }

        // 输入行结束 -> 复位列相关状态，推进行相位
        if (p.last) {
            px = 0;
            ox = 0;
            if (py == (ap_uint<16>)(RGB2GRAY_DECIM_DEN - 1)) {
                py = 0;
            } else {
                py++;
            }
        }
    }

    out_width   = ow;
    out_height  = oh;
    pixel_count = ocnt;
    sum_gray    = acc;

    fid++;
    frame_id = fid;
}
