// =============================================================================
//  host_model_fir.cpp —— fir_filter 的**主机端算术模型**（秒级自检，不需要 Vitis）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：docs/interface.md 第 3.5 / 4.5 节
//
//  【这个文件是什么 / 不是什么】
//    ✅ 是：把 fpga/src/fir_filter.cpp **内层算术**原样转写成主机可执行程序，
//           用来在跑 csim 之前（秒级）验证三件事：
//             1) 用 int32 + 对称折叠 的算式，与 Python 黄金参考**逐样本相等（容差 0）**；
//             2) 对称折叠（63 -> 32 个乘法器）**不改变任何一位结果**；
//             3) "int32 不会溢出"这条论证在真实向量上成立（同时断言上界）。
//    ❌ 不是：不是 HLS 仿真、不是综合结果、不是 RTL 证据。
//           **权威证据只能是 `vitis-run` 的 csim / csynth / cosim 输出。**
//           本文件只用标准 C++（不含 hls::stream），因此本机 MinGW 可以直接运行
//           （HLS 的 hls::stream 仿真模型在 win32 线程模型下无法链接运行，见 fpga/README.md）。
//
//  用法（在仓库根）：
//    g++ -O2 -std=c++17 -I fpga/src fpga/sim/host_model_fir.cpp -o host_model_fir.exe
//    ./host_model_fir.exe fpga/sim/data_fir
//    退出码 0 = 全部通过；非 0 = 有失败（会打印前几处不一致）
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../src/fir_coeffs_q15.h"

static const int N_TAPS = FIR_NUM_TAPS;
static const int N_HALF = (FIR_NUM_TAPS - 1) / 2;   // 成对抽头数
static const int N_CENTER = FIR_NUM_TAPS / 2;       // 中心抽头

typedef std::vector<short> Series;

// ---- 与 fir_filter.cpp 内层**逐字对应**的算术 ---------------------------------
//   delay[k] = x[n-k]（k=0 最新）。对称折叠：acc += h[k]*(delay[k]+delay[N-1-k])。
//   ⚠️ 改 fir_filter.cpp 的算式时，这里必须同步改（否则本自检就失去意义）。
struct Folded {
    std::vector<short> delay;

    Folded() : delay(N_TAPS, 0) {}
    void reset() { delay.assign(N_TAPS, 0); }

    void run(const short *x, int n, Series &y, unsigned &sat)
    {
        y.assign(n, 0);
        sat = 0;
        for (int i = 0; i < n; i++) {
            // 1) 移位
            for (int k = N_TAPS - 1; k > 0; k--) delay[k] = delay[k - 1];
            delay[0] = x[i];

            // 2) 对称折叠 MAC（int32）
            int acc = 0;
            for (int k = 0; k < N_HALF; k++) {
                int pair = (int)delay[k] + (int)delay[N_TAPS - 1 - k];
                acc += (int)FIR_COEFF_Q15[k] * pair;
            }
            acc += (int)FIR_COEFF_Q15[N_CENTER] * (int)delay[N_CENTER];

            // 3) 一次算术右移 + 饱和
            int sh = acc >> FIR_COEFF_SHIFT;
            if (sh > 32767)       { y[i] = 32767;  sat++; }
            else if (sh < -32768) { y[i] = -32768; sat++; }
            else                  { y[i] = (short)sh; }
        }
    }
};

// ---- 朴素参考（long long 精确累加，不做折叠）---------------------------------
struct Naive {
    std::vector<int> hist;

    Naive() : hist(N_TAPS - 1, 0) {}
    void reset() { hist.assign(N_TAPS - 1, 0); }

    void run(const short *x, int n, Series &y, unsigned &sat)
    {
        y.assign(n, 0);
        sat = 0;
        for (int i = 0; i < n; i++) {
            long long acc = 0;
            for (int k = 0; k < N_TAPS; k++) {
                int d = (k == 0) ? (int)x[i] : hist[k - 1];
                acc += (long long)FIR_COEFF_Q15[k] * (long long)d;
            }
            long long sh = acc >> FIR_COEFF_SHIFT;
            if (sh > 32767)       { y[i] = 32767;  sat++; }
            else if (sh < -32768) { y[i] = -32768; sat++; }
            else                  { y[i] = (short)sh; }
            for (int k = N_TAPS - 2; k > 0; k--) hist[k] = hist[k - 1];
            hist[0] = (int)x[i];
        }
    }
};

struct SegRow { std::string name; int seg_id, n_samples, reset, sat_count, out_count; };

static bool read_seg_csv(const std::string &path, std::vector<SegRow> &rows)
{
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || strncmp(line, "case,", 5) == 0) continue;
        char name[128];
        SegRow r;
        if (sscanf(line, "%127[^,],%d,%d,%d,%d,%d", name, &r.seg_id, &r.n_samples,
                   &r.reset, &r.sat_count, &r.out_count) == 6) {
            r.name = name;
            rows.push_back(r);
        }
    }
    fclose(f);
    return !rows.empty();
}

static bool read_golden(const std::string &path, int segments,
                        std::vector<Series> &gold)
{
    std::vector<std::vector<int> > g(segments + 1);
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || strncmp(line, "seg_id,", 7) == 0) continue;
        int sid, idx, y;
        if (sscanf(line, "%d,%d,%d", &sid, &idx, &y) == 3 &&
            sid >= 1 && sid <= segments) {
            if ((int)g[sid].size() <= idx) g[sid].resize(idx + 1, 0);
            g[sid][idx] = y;
        }
    }
    fclose(f);
    gold.assign(segments + 1, Series());
    for (int s = 1; s <= segments; s++) {
        gold[s].resize(g[s].size());
        for (size_t i = 0; i < g[s].size(); i++) gold[s][i] = (short)g[s][i];
    }
    return true;
}

int main(int argc, char **argv)
{
    std::string dir = (argc > 1) ? argv[1] : "fpga/sim/data_fir";

    // ---- 溢出界（契约 3.5 的核心论证）----
    long long abs_sum = 0;
    int coeff_sum = 0;
    for (int k = 0; k < N_TAPS; k++) {
        abs_sum += (FIR_COEFF_Q15[k] < 0) ? -(long long)FIR_COEFF_Q15[k]
                                         : (long long)FIR_COEFF_Q15[k];
        coeff_sum += FIR_COEFF_Q15[k];
    }
    long long bound = 32768LL * abs_sum;
    printf("== host_model_fir: N=%d, shift=%d, Σh=%d, Σ|h|=%lld ==\n",
           N_TAPS, FIR_COEFF_SHIFT, coeff_sum, abs_sum);
    printf("   |acc| 上界 = 32768 * Σ|h| = %lld  (int32 上限 2147483647) -> %s\n",
           bound, (bound < 2147483647LL) ? "不溢出 ✅" : "可能溢出 ❌");
    if (bound >= 2147483647LL) return 2;

    // ---- 读段结构与黄金参考 ----
    std::vector<SegRow> rows;
    if (!read_seg_csv(dir + "/golden_fir.csv", rows)) {
        printf("ERROR: 读不到 %s/golden_fir.csv（先跑 python fpga/sim/gen_fir_vectors.py）\n",
               dir.c_str());
        return 2;
    }
    int segments = (int)rows.size();
    std::vector<Series> gold;
    if (!read_golden(dir + "/golden_fir_out.csv", segments, gold)) {
        printf("ERROR: 读不到 golden_fir_out.csv\n");
        return 2;
    }
    FILE *fb = fopen((dir + "/series.bin").c_str(), "rb");
    if (!fb) { printf("ERROR: 读不到 series.bin\n"); return 2; }
    Series all;
    {
        unsigned char b[2];
        while (fread(b, 1, 2, fb) == 2)
            all.push_back((short)((unsigned)b[0] | ((unsigned)b[1] << 8)));
    }
    fclose(fb);

    // ---- 逐段跑两种算式并三方比对 ----
    Folded folded;
    Naive naive;
    int offset = 0, pass = 0, fail = 0;
    long mismatch_gold = 0, mismatch_naive = 0;
    unsigned total_sat = 0;
    bool saw_saturation = false, saw_no_saturation = false;

    for (int si = 0; si < segments; si++) {
        const SegRow &r = rows[si];
        if (r.reset) { folded.reset(); naive.reset(); }
        if (offset + r.n_samples > (int)all.size()) {
            printf("ERROR: series.bin 提前结束\n"); return 2;
        }
        Series yf, yn;
        unsigned sf = 0, sn = 0;
        folded.run(&all[offset], r.n_samples, yf, sf);
        naive.run(&all[offset], r.n_samples, yn, sn);
        offset += r.n_samples;
        total_sat += sf;
        if (sf > 0) saw_saturation = true; else saw_no_saturation = true;

        int badg = 0, badn = 0;
        for (int i = 0; i < r.n_samples; i++) {
            if (yf[i] != gold[r.seg_id][i]) {
                if (badg < 3)
                    printf("   [golden] seg %-16s i=%-5d folded=%6d exp=%6d\n",
                           r.name.c_str(), i, (int)yf[i], (int)gold[r.seg_id][i]);
                badg++;
            }
            if (yf[i] != yn[i]) {
                if (badn < 3)
                    printf("   [folded!=naive] seg %-16s i=%-5d folded=%6d naive=%6d\n",
                           r.name.c_str(), i, (int)yf[i], (int)yn[i]);
                badn++;
            }
        }
        mismatch_gold += badg;
        mismatch_naive += badn;
        bool ok = (badg == 0) && (badn == 0) && (sf == (unsigned)r.sat_count) && (sn == sf);
        if (ok) pass++;
        else {
            fail++;
            printf("FAIL %-16s golden_mismatch=%d folded_vs_naive=%d sat=%u(exp %d)\n",
                   r.name.c_str(), badg, badn, sf, r.sat_count);
        }
    }

    printf("-- 结果 --\n");
    printf("   int32+对称折叠 vs Python 黄金参考：%s（不一致 %ld 个样本）\n",
           mismatch_gold == 0 ? "逐样本相等 ✅" : "不一致 ❌", mismatch_gold);
    printf("   对称折叠 vs 朴素长整型累加   ：%s（不一致 %ld 个样本）\n",
           mismatch_naive == 0 ? "逐位相同 ✅" : "不一致 ❌", mismatch_naive);
    printf("   段：%d passed, %d failed；饱和样本合计 %u\n", pass, fail, total_sat);
    printf("   饱和路径覆盖：有饱和=%d 无饱和=%d（两种情况都必须出现）\n",
           (int)saw_saturation, (int)saw_no_saturation);
    if (!saw_saturation || !saw_no_saturation) {
        printf("   警告：饱和/非饱和覆盖不完整\n");
        fail++;
    }
    printf("== %s ==\n", fail == 0 ? "PASS" : "FAIL");
    printf("NOTE: 这是主机端算术模型，**不是** HLS 仿真/综合证据；权威证据见 fpga/report/。\n");
    return fail == 0 ? 0 : 1;
}
