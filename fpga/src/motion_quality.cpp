// =============================================================================
//  motion_quality.cpp  ——  帧差运动量 IP（v1）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1 (PYNQ-Z2)   目标时钟：10 ns (100 MHz)
//  契约：docs/interface.md 第 3.3 节
//
//  功能：
//    1) 灰度流透传：gray_in -> gray_out（保留 TUSER / TLAST / TKEEP / TSTRB）
//    2) 用**上一帧**灰度（片内 BRAM 缓存）逐像素求绝对差，输出：
//         diff_total        = Σ|cur - prev|
//         motion_pixels     = 满足 |cur - prev| > motion_thresh 的像素个数
//         motion_ratio_q16  = (motion_pixels << 16) / count   （整除向下取整）
//         count             = 本帧像素数
//
//  【重要语义：第 0 帧的输出无效】
//    prev_buf 是片内 BRAM，**上电后内容未定义**（C 代码未给初值，HLS 也不初始化 BRAM）。
//    因此第一帧"与上一帧的差"没有意义，其 diff_total/motion_pixels 必须丢弃；
//    从第 1 帧（frame_id == 2）起结果才有效。PS 侧可用 frame_id 判断。
//    ⚠️ 这也是为了避开一个隐患：若给 prev_buf 写 `= {0}`，C 仿真会按 C++ 语义清零，
//       而 RTL 侧要额外生成巨大的 BRAM 初值；不给初值则两侧行为一致地"未定义"，
//       测试台与黄金参考统一从帧 1 开始比对（见 docs/interface.md 3.3 / 4.2）。
//
//  【阈值语义】严格大于：|cur - prev| > motion_thresh 才算运动像素（等于不算）。
//    该比较必须与 A 线 OpenCV 口径一致，故在契约里写死，并有边界用例覆盖。
//
//  AXI-Stream 语义：TUSER = 帧首像素；TLAST = 行末像素；TKEEP/TSTRB = 0b1（8 bit）
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<8, 1, 1, 1> axis_gray_t;

// 片内"上一帧"缓存尺寸（**编译期常量**，决定 BRAM 占用，与运行时的 width/height 无关）
//   输入是 rgb2gray 缩小后的灰度：384x288 = 110592 像素。
//   ⚠️ HLS 的片内数组按 **2 的幂地址空间**分配 BRAM，所以尺寸要**贴着 2 的幂台阶挑**：
//     110592 -> 2^17 -> 64 个 BRAM18 = 器件的 23%
//     （若用 640x480 = 307200 -> 2^19 -> 256 个 = 91%，放不下 DMA+互连）
//   三组对照实验（320x240→64、512x512→128、640x480→256）已证实该规律。
//   见 fpga/report/c4_rgb2gray_motion_quality_v1.md 第 5 节。
#define MOTION_MAX_W 384
#define MOTION_MAX_H 288

void motion_quality(hls::stream<axis_gray_t> &gray_in,
                    hls::stream<axis_gray_t> &gray_out,
                    ap_uint<16> width,           // 图像宽（640）
                    ap_uint<16> height,          // 图像高（480）
                    ap_uint<8>  motion_thresh,   // 运动判定阈值（灰度差严格大于它才算运动）
                    ap_uint<32> &diff_total,     // 输出：帧差总量
                    ap_uint<32> &motion_pixels,  // 输出：运动像素个数
                    ap_uint<32> &motion_ratio_q16, // 输出：运动比例 Q16
                    ap_uint<32> &count,          // 输出：本帧像素数
                    ap_uint<32> &frame_id)       // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（寄存器偏移见 docs/interface.md 第 3.3 节）---------------------
#pragma HLS INTERFACE axis      port=gray_in
#pragma HLS INTERFACE axis      port=gray_out
#pragma HLS INTERFACE s_axilite port=width            bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=height           bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=motion_thresh    bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=diff_total       bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=motion_pixels    bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=motion_ratio_q16 bundle=ctrl offset=0x38
#pragma HLS INTERFACE s_axilite port=count            bundle=ctrl offset=0x40
#pragma HLS INTERFACE s_axilite port=frame_id         bundle=ctrl offset=0x48
#pragma HLS INTERFACE s_axilite port=return           bundle=ctrl

    // 上一帧灰度缓存：307200 x 8bit。
    // ⚠️ BIND_STORAGE 这个 pragma 必须写在变量**声明之后** —— HLS 按顺序解析 pragma，
    //    写在声明前会报 `use of undeclared identifier 'prev_buf'`（HLS 207-4637）。
    //    （默认就会被推断成 BRAM；显式写是为了让资源意图一目了然。注意 csim 不检查这条 pragma，
    //     所以这个错误只在 csynth 阶段暴露。）
    static ap_uint<8>  prev_buf[MOTION_MAX_W * MOTION_MAX_H];  // 上电后内容未定义
#pragma HLS BIND_STORAGE variable=prev_buf type=RAM_2P impl=BRAM

    static ap_uint<32> fid = 0;

    ap_uint<32> diff_acc = 0;
    ap_uint<32> mp_acc   = 0;

    const int total = (int)width * (int)height;

    for (int i = 0; i < total; i++) {
#pragma HLS PIPELINE II=1

        axis_gray_t p = gray_in.read();
        ap_uint<8> cur  = p.data;
        ap_uint<8> prev = prev_buf[i];

        // 绝对差：8 bit 相减若下溢会回绕，故先比大小再减
        ap_uint<8> d = (cur >= prev) ? (ap_uint<8>)(cur - prev)
                                     : (ap_uint<8>)(prev - cur);

        diff_acc += d;
        if (d > motion_thresh) {     // 严格大于
            mp_acc += 1;
        }

        prev_buf[i] = cur;           // 写回：本帧成为下一帧的"上一帧"
        gray_out.write(p);           // 透传
    }

    diff_total    = diff_acc;
    motion_pixels = mp_acc;
    count         = (ap_uint<32>)total;

    // motion_ratio_q16 = (motion_pixels << 16) / count，整除向下取整
    // 注意：motion_pixels <= 307200（19 bit），左移 16 位后需 35 bit，故用 64 bit 中间量。
    // 这段在逐像素循环之外，不影响 II；代价是一个除法器（见 fpga/report/ 的资源记录）。
    if (total > 0) {
        ap_uint<64> num = ((ap_uint<64>)mp_acc) << 16;
        motion_ratio_q16 = (ap_uint<32>)(num / (ap_uint<64>)(ap_uint<32>)total);
    } else {
        motion_ratio_q16 = 0;
    }

    fid++;
    frame_id = fid;
}
