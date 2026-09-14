// =============================================================================
//  fir_filter.cpp  ——  时间序列带通 FIR（Q15 定点，v1）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  目标器件：xc7z020clg400-1 (PYNQ-Z2)   目标时钟：10 ns (100 MHz)
//  契约：docs/interface.md 第 3.5 节
//
//  功能：
//    对**时间序列**（如 roi_statistic 的 ROI 累加均值序列）做带通滤波：
//      y[n] = sat16( ( Σ_{k=0}^{N-1} h[k] * x[n-k] ) >> 15 )
//    系数 h 是冻结的 int16 Q15 表（fpga/src/fir_coeffs_q15.h，由
//    fpga/sim/design_fir_coeffs.py 生成），线性相位 I 型、偶对称：
//      h[k] == h[N-1-k]  ->  用"成对预加 + 半量乘法"实现，乘法器数量减半
//      N = FIR_NUM_TAPS = 63，群延迟 = (N-1)/2 = 31 个样本（@45 fps = 689 ms）
//
//  【四条必须与 A 线逐字一致的运算约定】
//    1) 累加用 int32 精确整数，**不做任何中间舍入**；
//    2) 只在最后做**一次算术右移 15 位**（C++ 的 >> 对负数 = 向下取整，
//       与 Python 的 >> 完全一致；**不可**写成 /32768，整数除法是向零取整）；
//    3) 移位结果**饱和**到 int16：> 32767 -> 32767，< -32768 -> -32768；
//    4) 饱和的样本数计入 saturation_count（供 PS 做质量判据）。
//    溢出安全性：|acc| <= 32768 * Σ|h_q15| = 32768 * 55073 = 1,804,632,064 < 2^31-1，
//    已在生成器里断言（见 design_fir_coeffs.py 的溢出界打印），故 int32 不会溢出。
//
//  【段（segment）语义 —— 与图像 IP 的"帧"对应】
//    · 一次函数调用处理 n_samples 个样本，输入输出**一一对应、同序、等长**；
//    · 输出**含启动瞬态**（不丢弃前 N-1 个样本），这样"输入第 i 个样本 <-> 输出第 i 个"
//      的对应关系最简单，黄金参考可以逐样本严格比对；
//    · 延迟线是 static（**段间保持**，即连续流语义）；因此**首次调用前必须 reset**：
//      C++ 语言层面 static 对象是**零初始化**，但 HLS 把它实现为**上电初始化**
//      （综合会警告 `Register '...delay_line...' is power-on initialization`），
//      **复位不会把它清零** —— 即 skill 里记的坑 #17（motion_quality 的 fid 同款）。
//      所以本 IP 用一个显式 `reset` 寄存器来建立确定性起点：测试台与 PS 都必须靠它，
//      不能依赖"上电是 0"这个未在 RTL 上被保证的假设。
//      测试台与 PS 侧统一约定：reset=1 的段之间互不影响，reset=0 的段承接上一段状态。
//    · reset 只清延迟线，**不**影响 seg_id（seg_id 每次调用 +1，从 1 开始）。
//
//  AXI-Stream 语义（本 IP 是时间序列，口径与图像 IP 略有不同，契约里另行写明）：
//    TUSER = 1 标记**段（一次调用）的第一个样本**；TLAST = 1 标记**段的最后一个样本**；
//    TKEEP/TSTRB = 0b11（16 bit）；四个侧信道位**原样透传**到输出。
//
//  【呼吸带说明】本 IP v1 只做心率带（0.7~3.5 Hz @45 fps）。同一阶数（63）
//    在 45 fps 下**做不了** 0.1~0.5 Hz 的呼吸带（过渡带 0.375 Hz 与整个通带同量级），
//    呼吸带需 PS 侧降采样后再用同名 IP 的另一组系数 —— 见契约 3.5 的已知限制。
// =============================================================================

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

#include "fir_coeffs_q15.h"

typedef ap_axiu<16, 1, 1, 1> axis_fir_t;   // TDATA = int16（Q1.15 归一化样本）

// 对称折叠用的常量（N 为奇数 -> 中间那一个抽头单独算）
#define FIR_HALF   ((FIR_NUM_TAPS - 1) / 2)   // 成对抽头数 = 31
#define FIR_CENTER (FIR_NUM_TAPS / 2)         // 中心抽头下标 = 31

void fir_filter(hls::stream<axis_fir_t> &fir_in,
                hls::stream<axis_fir_t> &fir_out,
                ap_uint<16> n_samples,        // 输入：本段样本数（1..65535）
                ap_uint<1>  reset,            // 输入：1 = 读取样本前清空延迟线
                ap_uint<16> &out_count,       // 输出：本段输出样本数（== n_samples）
                ap_uint<16> &saturation_count,// 输出：本段发生饱和的样本数
                ap_uint<32> &seg_id)          // 输出：段序号，每次调用 +1，从 1 开始
{
// ---- 接口绑定（寄存器偏移见 docs/interface.md 第 3.5 节）---------------------
#pragma HLS INTERFACE axis      port=fir_in
#pragma HLS INTERFACE axis      port=fir_out
#pragma HLS INTERFACE s_axilite port=n_samples        bundle=ctrl offset=0x10
#pragma HLS INTERFACE s_axilite port=reset            bundle=ctrl offset=0x18
#pragma HLS INTERFACE s_axilite port=out_count        bundle=ctrl offset=0x20
#pragma HLS INTERFACE s_axilite port=saturation_count bundle=ctrl offset=0x28
#pragma HLS INTERFACE s_axilite port=seg_id           bundle=ctrl offset=0x30
#pragma HLS INTERFACE s_axilite port=return           bundle=ctrl

    // 延迟线：delay_line[k] = x[n-k]（k=0 是最新样本）。
    // ⚠️ BIND_STORAGE / ARRAY_PARTITION 这类 pragma 必须写在变量**声明之后** ——
    //    HLS 按顺序解析 pragma，写在声明前会报 `use of undeclared identifier`（HLS 207-4637）。
    //    这里显式完全分区：63 x 16 bit 变成 63 组寄存器（约 1008 FF），
    //    换来 II=1 的全并行结构（不占 BRAM，BRAM 留给 motion_quality）。
    static int16_t delay_line[FIR_NUM_TAPS];   // 上电初始化（**不是**复位清零）-> 靠 reset 建立起点
#pragma HLS ARRAY_PARTITION variable=delay_line complete dim=1

    // 系数表：`static const` 数组 = 编译期常量，HLS 会**常量折叠**进乘法器，
    // 因此**不需要**（也不该）给它写 ARRAY_PARTITION —— 写了只会被工具标成
    // "Not implemented"（本 IP 首版实测如此），在综合报告里留一条误导性的忽略记录。

    static ap_uint<32> sid = 0;
// P0-2：让段计数随**块复位 ap_rst_n**（PS 复位 / overlay 重新加载）归零，
//   而不是仅靠"上电初始化"。默认行为下 static 变量不被复位清零，
//   PS 若按"seg_id 从 1 开始"做同步，复位后会失配。
//   ⚠️ pragma 必须写在变量声明之后（同坑 #18）。
#pragma HLS RESET variable=sid

    // ---- 可选复位：清空延迟线（首次调用前必须做，理由见文件头）--------------
    if (reset) {
        for (int k = 0; k < FIR_NUM_TAPS; k++) {
#pragma HLS UNROLL
            delay_line[k] = 0;
        }
    }

    ap_uint<16> sat_cnt = 0;

    for (int i = 0; i < (int)n_samples; i++) {
#pragma HLS PIPELINE II=1

        axis_fir_t p = fir_in.read();

        // 16 bit 位级重解释为有符号 Q1.15 样本（AXI-Stream 的 TDATA 是无符号容器）
        int16_t x = (int16_t)(ap_uint<16>)p.data;

        // 1) 移位：delay_line[k] = x[n-k]
        for (int k = FIR_NUM_TAPS - 1; k > 0; k--) {
#pragma HLS UNROLL
            delay_line[k] = delay_line[k - 1];
        }
        delay_line[0] = x;

        // 2) 对称折叠 MAC：h[k] * (delay[k] + delay[N-1-k])，乘法器 63 -> 32
        //    预加是精确整数运算（|和| <= 65536），不改变最终结果（无中间舍入）。
        int32_t acc = 0;
        for (int k = 0; k < FIR_HALF; k++) {
#pragma HLS UNROLL
            int32_t pair = (int32_t)delay_line[k] + (int32_t)delay_line[FIR_NUM_TAPS - 1 - k];
            acc += (int32_t)FIR_COEFF_Q15[k] * pair;
        }
        acc += (int32_t)FIR_COEFF_Q15[FIR_CENTER] * (int32_t)delay_line[FIR_CENTER];

        // 3) 一次算术右移 + 饱和（**唯一**的定点运算点，A 线必须照此实现）
        int32_t shifted = acc >> FIR_COEFF_SHIFT;
        int16_t y;
        if (shifted > 32767) {
            y = 32767;
            sat_cnt++;
        } else if (shifted < -32768) {
            y = (int16_t)(-32768);
            sat_cnt++;
        } else {
            y = (int16_t)shifted;
        }

        // 4) 输出（侧信道原样透传：TUSER=段首、TLAST=段末）
        axis_fir_t q;
        q.data = (ap_uint<16>)(ap_int<16>)y;
        q.keep = 0x3;          // 16 bit -> 2 个字节通道
        q.strb = 0x3;
        q.user = p.user;
        q.last = p.last;
        q.id   = p.id;
        q.dest = p.dest;
        fir_out.write(q);
    }

    out_count        = (ap_uint<16>)n_samples;
    saturation_count = sat_cnt;

    sid++;
    seg_id = sid;
}
