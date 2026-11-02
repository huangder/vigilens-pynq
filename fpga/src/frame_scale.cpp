// =============================================================================
//  frame_scale.cpp  ——  RGB888 行/列 3:2 抽取缩放（720p 采集 -> 640x480 测量）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1（Mizar-Z7020 / PYNQ-Z2 同一颗）  目标时钟：10 ns
//  契约：**尚未进入 docs/interface.md** —— 提案见 docs/20 §5（待 A/B 会签）。
//
//  为什么需要这个 IP：
//    60fps 主档的传感器模式定为 1280x720 RAW10（1080p@60 在 2-lane 上不可行，
//    判定过程与证据见 docs/20 §1 与可复现脚本 fpga/sim/link_budget.py）。
//    但契约 §0 的测量流水线是 640x480，四个既有 IP 的工作尺寸写死、黄金参考冻结。
//    ⇒ 必须在 PL 内把 1280x720 缩到 640x480。**四条理由让这一步不能省**：
//      1) 若把流水线整体抬到 720p，motion_quality 存"上一帧"需要 256 个 BRAM18
//         = 器件的 91%，放不下其余逻辑（同一个 2 的幂台阶规律，见 fpga/report/c4 第 5 节）；
//      2) 由 PS 缩会变成"PS 做图像处理"，削弱 PL 的价值；
//      3) 缩放是定义明确、纯整数、可逐点黄金参考比对的确定性任务；
//      4) 缩放后契约不动，四个 IP 与全部黄金参考**一行不改**。
//
//  【冻结口径：双轴 3:2 抽取（点采样）+ 横向中心裁剪】
//    先横向裁出 4:3 窗口，再在两个方向都按"每 3 取 2"抽取：
//      横向：只取 x ∈ [crop_x0, crop_x0 + crop_w)，且 (x - crop_x0) % 3 < 2
//      纵向：只取 y，且 y % 3 < 2
//    1280x720 取 crop_x0=160、crop_w=960 时：
//      横向 960 / 3 * 2 = 640 ；纵向 720 / 3 * 2 = 480
//    ⚠️ **两个方向的比例相同（都是 3:2），所以几何不失真** —— 这一点很关键：
//       若简单地"横向 2:1 平均 + 纵向 2/3 抽取"，会把 16:9 硬拉成 4:3，
//       纵向被拉伸 1.333 倍（EAR 等比例指标虽仍可比，但头部姿态/几何一律失真）。
//    裁剪窗口 960x720 是 4:3，与输出 640x480 同比 ⇒ 无畸变。
//    代价：横向只覆盖传感器 75% 的视场（中心裁切），纵向覆盖 100%。
//    好处：剩下那 320 列**留着没用**，将来做"跟随人脸的 ROI 平移"时可以直接移动
//         crop_x0（云台搁置后的软件替代路径）。
//    ⚠️ **点采样会保留混叠**（与既有 rgb2gray 的 3/5 抽取同一个已知性质）：
//       高频内容可能被折叠进来。运动/光照统计影响不大；若将来发现对 rPPG 有影响，
//       可改为"先 [1,2,1] 低通再抽取"（要换黄金参考，属契约级改动）。
//
//  资源：本 IP **不需要任何 BRAM**（纯逐像素判定，无行缓冲、无帧缓冲）。
//
//  AXI-Stream 语义：输入 TUSER/TLAST 不参与控制（按 width/height 计数读完一帧）；
//                   输出 TUSER = 输出帧首像素、TLAST = **输出行**末像素。
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<24, 1, 1, 1> axis_pix_t;   // 输入/输出都是 RGB888（byte0=R）

#define FRAME_SCALE_NUM 2   // 每 DEN 个采样保留前 NUM 个
#define FRAME_SCALE_DEN 3

void frame_scale(hls::stream<axis_pix_t> &rgb_in,
                 hls::stream<axis_pix_t> &rgb_out,
                 ap_uint<16> width,        // 输入：行像素数（1280）
                 ap_uint<16> height,       // 输入：行数（720，必须是 3 的整数倍）
                 ap_uint<16> crop_x0,      // 输入：横向裁剪起点（160）
                 ap_uint<16> crop_w,       // 输入：横向裁剪宽度（960，必须是 3 的整数倍）
                 ap_uint<16> &out_width,   // 输出：crop_w/3*2（640）
                 ap_uint<16> &out_height,  // 输出：height/3*2（480）
                 ap_uint<32> &pixel_count, // 输出：本帧输出像素数（= out_width*out_height）
                 ap_uint<32> &frame_id)    // 输出：已处理帧计数，从 1 开始
{
// ---- 接口绑定（偏移见 docs/20 §5 的接口提案；契约冻结后以 docs/interface.md 为准）----
#pragma HLS INTERFACE axis      port=rgb_in
#pragma HLS INTERFACE axis      port=rgb_out
#pragma HLS INTERFACE s_axilite port=width       bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=height      bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=crop_x0     bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=crop_w      bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=out_width   bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=out_height  bundle=ctrl offset=0x38
#pragma HLS INTERFACE s_axilite port=pixel_count bundle=ctrl offset=0x40
#pragma HLS INTERFACE s_axilite port=frame_id    bundle=ctrl offset=0x48
#pragma HLS INTERFACE s_axilite port=return      bundle=ctrl

    static ap_uint<32> fid = 0;
// 与其余 IP 一致：帧计数随 ap_rst_n 归零（PS 若按"frame_id 从 1 开始"做首帧同步，
// 复位后不能失配）。⚠️ pragma 必须写在变量声明之后。
#pragma HLS RESET variable=fid

    const int W = (int)width;
    const int H = (int)height;
    const int x0 = (int)crop_x0;
    const int cw = (int)crop_w;

    ap_uint<32> ocnt = 0;

    for (int y = 0; y < H; y++) {
        // 纵向：每 3 行取前 2 行
        const bool keep_row =
            ((y % FRAME_SCALE_DEN) < FRAME_SCALE_NUM);

        ap_uint<16> ox = 0;   // 本输出行已发出的像素数

        for (int x = 0; x < W; x++) {
#pragma HLS PIPELINE II=1
            axis_pix_t p = rgb_in.read();

            bool emit = false;
            if (keep_row && x >= x0) {
                const int k = x - x0;
                // 横向：每 3 列取前 2 列（只在这个裁剪窗口内）
                if (k < cw && ((k % FRAME_SCALE_DEN) < FRAME_SCALE_NUM)) {
                    emit = true;
                }
            }

            if (emit) {
                axis_pix_t q;
                q.data = p.data;                 // RGB888 直通，不做任何颜色变换
                q.keep = 0x7;
                q.strb = 0x7;
                q.user = (y == 0 && ox == 0) ? 1 : 0;   // 输出帧首像素
                q.last = (ox == (ap_uint<16>)(cw / FRAME_SCALE_DEN * FRAME_SCALE_NUM - 1))
                         ? 1 : 0;                        // 输出行末像素
                q.id   = p.id;
                q.dest = p.dest;
                rgb_out.write(q);
                ox++;
                ocnt++;
            }
        }
    }

    out_width   = (ap_uint<16>)(cw / FRAME_SCALE_DEN * FRAME_SCALE_NUM);
    out_height  = (ap_uint<16>)(H  / FRAME_SCALE_DEN * FRAME_SCALE_NUM);
    pixel_count = ocnt;

    fid++;
    frame_id = fid;
}
