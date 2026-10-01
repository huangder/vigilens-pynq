// =============================================================================
//  bayer_demosaic.cpp  ——  Bayer(RAW10) -> RGB888 去马赛克 IP（整数双线性 + 边界钳位）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1（Mizar-Z7020 / PYNQ-Z2 同一颗）  目标时钟：10 ns
//  契约：**尚未进入 docs/interface.md** —— 接口提案见 docs/19 §6（待 A/B 会签）。
//
//  为什么需要这个 IP：
//    树莓派相机（IMX219/IMX708）经 MIPI CSI-2 出的是 **Bayer 马赛克**，每个像素只有
//    一个颜色分量；而契约 §0 与既有四个 IP 要的是 **RGB888 逐像素三通道**。
//    中间必须有人把马赛克补成三通道 —— 这就是 demosaic。
//    放在 PL 做符合"PL 不是转发器"：这是定义明确、纯整数、可逐点黄金参考比对的
//    确定性图像处理任务，正是 HLS 该做的事。
//
//  【冻结口径一：Bayer 相位（RGGB）】
//    以 (x,y) 的奇偶定色，**首像素 (0,0) 为 R**：
//        (偶 x, 偶 y) = R      (奇 x, 偶 y) = Gr
//        (偶 x, 奇 y) = Gb     (奇 x, 奇 y) = B
//    ⇒ (x%2, y%2) = (0,0)->R  (1,0)->G  (0,1)->G  (1,1)->B
//
//  【冻结口径二：双线性插值 + 四舍五入】
//    R 位置：G = (G上+G下+G左+G右 + 2) >> 2 ；B = (B四对角 + 2) >> 2
//    B 位置：G 同上                  ；R = (R四对角 + 2) >> 2
//    G 位置（奇 x 偶 y）：R = (R左+R右 + 1) >> 1 ；B = (B上+B下 + 1) >> 1
//    G 位置（偶 x 奇 y）：R = (R上+R下 + 1) >> 1 ；B = (B左+B右 + 1) >> 1
//    —— 全部整数、无浮点、无查表；"+2 >>2" / "+1 >>1" 即**四舍五入**（半值向上）。
//
//  【冻结口径三：边界用坐标钳位（replicate）】
//    取邻居时把下标钳到 [0, W-1] / [0, H-1]，而不是补零、也不是镜像。
//    ⇒ 角上的四对角退化为同一个像素被取 4 次（结果确定、可逐点复算）。
//    ⚠️ 这条是"口径选择"不是"最优选择"：镜像边界边缘更干净，但钳位最容易被
//       两种语言独立实现成逐位相同，故本项目选钳位。改它 = 换黄金参考。
//
//  【冻结口径四：10 bit -> 8 bit】
//    v8 = saturate8((v10 + 2) >> 2)
//    —— 10 bit 满量程 1023 -> (1023+2)>>2 = 256 -> 饱和到 255；
//       只有 1022/1023 两个输入值需要饱和，其余为无损右移。
//    先插值（12 bit 累加器）再一次性降到 8 bit，避免"先降位再平均"多丢一次精度。
//
//  实现结构（3 行滚动缓冲）：
//    读满一行 y 之后，行 y-1 的上/中/下三行就都到齐了 -> 立刻发一行输出。
//    缓冲是 `static`，但每个输出像素只依赖"刚读进来的 3 行"，与调用次数无关；
//    ⚠️ 已知性质：中断一次调用再继续（分段调用）会丢行 —— 本 IP 按"一次调用处理整帧"设计，
//       与其他四个 IP 相同（分段等价性由 tb 的 Layer 1 覆盖）。
//
//  AXI-Stream 语义：输入 TUSER/TLAST 不参与控制（按 width/height 计数读完一帧）；
//                   输出 TUSER = 输出帧首像素、TLAST = **输出行**末像素。
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<16, 1, 1, 1> axis_pix16_t;   // 输入：1 个 Bayer 像素/beat（低 10 bit 有效）
typedef ap_axiu<24, 1, 1, 1> axis_rgb_t;     // 输出：RGB888 逐像素

// 行缓冲最大宽度：1920（1080p）+ 复用余量 -> 2048（2 的幂，BRAM 友好）
// HLS 的片内数组按 2 的幂地址空间分配 BRAM（见 fpga/report/c4 第 5 节的设计规律）：
//   3 行 x 2048 x 16 bit = 3 x 32768 bit -> 约 3 个 BRAM18（远低于 motion_quality 的 64 个）
#define BAYER_MAX_W 2048

static ap_uint<8> to8(ap_uint<12> v)
{
// 10 bit -> 8 bit：四舍五入右移 2 位，并饱和（仅 1022/1023 会触发）
#pragma HLS INLINE
    ap_uint<9> t = (ap_uint<9>)((v + 2) >> 2);
    return (t > (ap_uint<9>)255) ? (ap_uint<8>)255 : (ap_uint<8>)t;
}

void bayer_demosaic(hls::stream<axis_pix16_t> &bayer_in,
                    hls::stream<axis_rgb_t> &rgb_out,
                    ap_uint<16> width,        // 输入：行像素数（>=1）
                    ap_uint<16> height,       // 输入：行数（**>=2**，否则无上下邻域可钳位）
                    ap_uint<32> &pixel_count, // 输出：本帧输出像素数（= width*height）
                    ap_uint<32> &frame_id)    // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（偏移见 docs/19 §6 的接口提案）-------------------------------
#pragma HLS INTERFACE axis      port=bayer_in
#pragma HLS INTERFACE axis      port=rgb_out
#pragma HLS INTERFACE s_axilite port=width       bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=height      bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=pixel_count bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=frame_id    bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=return      bundle=ctrl

    static ap_uint<32> fid = 0;
#pragma HLS RESET variable=fid

    const int W = (int)width;
    const int H = (int)height;

    // 3 行滚动缓冲（up / mid / down 由旋转下标选择）
    static ap_uint<16> rb[3][BAYER_MAX_W];

    int cur = 0, prev = 2, prev2 = 1;   // 初始旋转相位
    ap_uint<32> ocnt = 0;

    // y 多跑一轮（y == H）用于把最后一行输出掉；该轮不读输入
    for (int y = 0; y <= H; y++) {

        if (y < H) {
            for (int x = 0; x < W; x++) {
#pragma HLS PIPELINE II=1
                axis_pix16_t p = bayer_in.read();
                rb[cur][x] = p.data(9, 0);        // 只取低 10 bit
            }
        }

        if (y >= 1) {
            const int ym   = y - 1;                          // 本行要输出的 y
            const int upi  = (y >= 2) ? prev2 : prev;         // y=1 时上邻钳到行 0
            const int midi = prev;                            // 行 y-1 恒定在 prev
            const int dni  = (y < H) ? cur : prev;            // 最后一轮下邻钳到行 H-1

            const bool odd_y = (ym & 1) != 0;

            for (int x = 0; x < W; x++) {
#pragma HLS PIPELINE
                const int xm1 = (x == 0)     ? 0     : x - 1;   // 坐标钳位（replicate）
                const int xp1 = (x == W - 1) ? W - 1 : x + 1;
                const bool odd_x = (x & 1) != 0;

                const ap_uint<12> c   = rb[midi][x];
                const ap_uint<12> up  = rb[upi][x];
                const ap_uint<12> dn  = rb[dni][x];
                const ap_uint<12> lf  = rb[midi][xm1];
                const ap_uint<12> rt  = rb[midi][xp1];
                const ap_uint<12> ul  = rb[upi][xm1];
                const ap_uint<12> ur  = rb[upi][xp1];
                const ap_uint<12> dl  = rb[dni][xm1];
                const ap_uint<12> dr  = rb[dni][xp1];

                ap_uint<12> R, G, B;

                if (!odd_x && !odd_y) {
                    // R 位置：G 取上下左右四点，B 取四对角
                    R = c;
                    G = (ap_uint<12>)((up + dn + lf + rt + 2) >> 2);
                    B = (ap_uint<12>)((ul + ur + dl + dr + 2) >> 2);
                } else if (odd_x && odd_y) {
                    // B 位置：G 同上，R 取四对角
                    B = c;
                    G = (ap_uint<12>)((up + dn + lf + rt + 2) >> 2);
                    R = (ap_uint<12>)((ul + ur + dl + dr + 2) >> 2);
                } else if (odd_x && !odd_y) {
                    // G 位置（Gr）：左右是 R、上下是 B
                    G = c;
                    R = (ap_uint<12>)((lf + rt + 1) >> 1);
                    B = (ap_uint<12>)((up + dn + 1) >> 1);
                } else {
                    // G 位置（Gb）：左右是 B、上下是 R
                    G = c;
                    R = (ap_uint<12>)((up + dn + 1) >> 1);
                    B = (ap_uint<12>)((lf + rt + 1) >> 1);
                }

                axis_rgb_t q;
                // ⚠️ 通道顺序 R-G-B：**R=[7:0]、G=[15:8]、B=[23:16]**（byte0=R），不是 OpenCV 默认的 BGR。
                //    2026-09-30 修正：原来写的是 (R<<16)|(G<<8)|B —— 那是**BGR**（R 跑到 bits[23:16]），
                //    与本注释、（tb:97-99 的解码）、docs/19 §6 的"byte0=R"、
                //    以及下游消费方 rgb2gray.cpp:100（R=[7:0]）/ roi_statistic.cpp:87（p.data(7,0)）
                //    全都相反 ⇒ csim 的 corner clamp 实测 (64,128,255)，正是 (255,128,64) 的 R/B 互换。
                //    这类错误**不会编译报错**，只会让灰度式 Y=(77R+150G+29B)>>8 悄悄按 77B+29R 算。
                q.data = ((ap_uint<24>)to8(B) << 16) |
                         ((ap_uint<24>)to8(G) << 8)  |
                         ((ap_uint<24>)to8(R));
                q.keep = 0x7;
                q.strb = 0x7;
                q.user = (ym == 0 && x == 0) ? 1 : 0;      // 输出帧首像素
                q.last = (x == W - 1) ? 1 : 0;             // 输出行末像素
                q.id   = 0;
                q.dest = 0;
                rgb_out.write(q);

                ocnt++;
            }
        }

        // 旋转：prev2 <- prev <- cur <- 旧 prev2（该缓冲已不再被引用）
        const int t = prev2;
        prev2 = prev;
        prev  = cur;
        cur   = t;
    }

    pixel_count = ocnt;

    fid++;
    frame_id = fid;
}
