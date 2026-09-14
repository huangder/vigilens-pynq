// =============================================================================
//  tb_fir_filter.cpp  ——  fir_filter 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：docs/interface.md 第 3.5 / 4.5 节
//
//  【本 IP 是有状态的】片内保留 N-1 个样本的延迟线，所以测试台必须**按段序喂**，
//  并且段与段之间的 reset 语义必须与黄金参考完全一致：
//    reset=1 -> 该段从零状态开始（段间互不影响）
//    reset=0 -> 该段承接上一段末尾的状态（连续流）
//  首次调用前必须 reset=1（延迟线是 static：HLS 把它实现为**上电初始化**，
//  复位并不清零 —— 见 skill 坑 #17，因此不能依赖"上电是 0"）。
//
//  【第 1 层】自包含边界用例：脉冲 / 全零 / 全幅直流 / 奈奎斯特 / 单样本(n=1) /
//             复位语义（reset 是否真的清空、reset=0 是否真的保持）/ 饱和计数。
//             期望值由本文件内的**朴素参考**（long long 精确累加 + 算术右移 + 饱和）算出。
//  【第 2 层】跨语言黄金参考：读 data_fir/{series.bin, golden_fir.csv,
//             golden_fir_out.csv, meta.txt}（由 Python 独立算出），按段逐样本比对。
//
//  两层都额外校验：
//    · TUSER = 段首样本、TLAST = 段末样本，必须原样透传到输出；
//    · out_count == n_samples、seg_id 是**自 IP 上电以来的调用序号**（每次调用 +1）；
//    · 【跨段一致性】同一串数据"分段调用(reset=1,0,0)"必须与"一次调用(reset=1)"
//      逐样本相同 —— 这条不依赖黄金参考，是状态机语义的独立证据。
//
//  用法： tb_fir_filter <data_dir>
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

#include "../src/fir_coeffs_q15.h"   // 系数表（唯一来源，与 IP 同一份）

typedef ap_axiu<16, 1, 1, 1> axis_fir_t;

void fir_filter(hls::stream<axis_fir_t> &fir_in,
                hls::stream<axis_fir_t> &fir_out,
                ap_uint<16> n_samples,
                ap_uint<1>  reset,
                ap_uint<16> &out_count,
                ap_uint<16> &saturation_count,
                ap_uint<32> &seg_id);

struct Stats {
    unsigned out_count, sat_count, seg_id;
};

// ---- 朴素参考实现（第 1 层用；故意写得笨，便于人工核对）---------------------
// 与 fir_filter.cpp 的约定逐字一致：精确整数累加 -> 一次算术右移 -> 饱和到 int16。
// 注意 acc 用 long long 而不是 int32：这里刻意不复用"int32 不溢出"的推导，
// 让第 1 层独立于"溢出界"这条论证（溢出界本身由 Python 生成器断言）。
struct FirRef {
    std::vector<int> hist;                       // hist[0] = x[n-1]

    FirRef() : hist(FIR_NUM_TAPS - 1, 0) {}
    void reset() { hist.assign(FIR_NUM_TAPS - 1, 0); }

    void run(const short *x, int n, std::vector<short> &y, unsigned &sat)
    {
        y.assign(n, 0);
        sat = 0;
        for (int i = 0; i < n; i++) {
            long long acc = 0;
            for (int k = 0; k < FIR_NUM_TAPS; k++) {
                int d = (k == 0) ? (int)x[i] : hist[k - 1];
                acc += (long long)FIR_COEFF_Q15[k] * (long long)d;
            }
            long long sh = acc >> FIR_COEFF_SHIFT;   // 算术右移（对负数向下取整）
            int v;
            if (sh > 32767)        { v = 32767;  sat++; }
            else if (sh < -32768)  { v = -32768; sat++; }
            else                   { v = (int)sh; }
            y[i] = (short)v;

            for (int k = FIR_NUM_TAPS - 2; k > 0; k--) hist[k] = hist[k - 1];
            hist[0] = (int)x[i];
        }
    }
};

// ---- 运行一次 IP（一段）-----------------------------------------------------
static void run_ip(const short *x, int n, int reset_flag,
                   Stats &st, std::vector<short> &y, long &hdr_bad)
{
    hls::stream<axis_fir_t> in, out;
    y.assign((size_t)n, 0);
    hdr_bad = 0;

    for (int i = 0; i < n; i++) {
        axis_fir_t p;
        p.data = (ap_uint<16>)(ap_int<16>)x[i];
        p.keep = 0x3;                       // 16 bit -> 2 字节通道
        p.strb = 0x3;
        p.user = (i == 0)     ? 1 : 0;      // TUSER = 段首样本
        p.last = (i == n - 1) ? 1 : 0;      // TLAST = 段末样本
        p.id   = 0;
        p.dest = 0;
        in.write(p);
    }

    ap_uint<16> oc, sc;
    ap_uint<32> sid;
    fir_filter(in, out, (ap_uint<16>)n, (ap_uint<1>)reset_flag, oc, sc, sid);

    st.out_count = (unsigned)oc;
    st.sat_count = (unsigned)sc;
    st.seg_id    = (unsigned)sid;

    for (int i = 0; i < n; i++) {
        axis_fir_t q = out.read();
        y[i] = (short)(ap_int<16>)q.data;
        bool ok = ((unsigned)q.user == ((i == 0)     ? 1u : 0u)) &&
                  ((unsigned)q.last == ((i == n - 1) ? 1u : 0u)) &&
                  ((unsigned)q.keep == 3u);
        if (!ok) hdr_bad++;
    }
}

// =============================================================================
//  第 1 层：自包含边界用例
// =============================================================================
static int test_embedded(void)
{
    printf("---- [Layer 1] embedded boundary cases (N=%d, shift=%d) ----\n",
           FIR_NUM_TAPS, FIR_COEFF_SHIFT);

    int pass = 0, fail = 0;

    // --- 用例 1：单位脉冲 -> 输出即冲激响应（检验系数顺序 / 群延迟 / 偶对称）---
    {
        const int n = 96;
        std::vector<short> x(n, 0), y;
        x[0] = 32767;
        Stats st; long hdr = 0;
        FirRef ref; ref.reset();
        std::vector<short> ey; unsigned esat = 0;
        ref.run(x.data(), n, ey, esat);
        run_ip(x.data(), n, 1, st, y, hdr);

        int argmax = 0;
        for (int i = 1; i < n; i++) if (y[i] > y[argmax]) argmax = i;
        bool sym = true;
        for (int k = 0; k < FIR_NUM_TAPS; k++)
            if (y[k] != y[FIR_NUM_TAPS - 1 - k]) sym = false;
        bool vals = (y == ey);
        bool ok = (argmax == (FIR_NUM_TAPS - 1) / 2) && sym && vals &&
                  (hdr == 0) && (st.out_count == (unsigned)n) && (st.sat_count == esat);
        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL impulse: argmax=%d(exp %d) sym=%d vals=%d hdr=%ld sat=%u/%u\n",
                   argmax, (FIR_NUM_TAPS - 1) / 2, (int)sym, (int)vals, hdr,
                   st.sat_count, esat);
        }
    }

    // --- 用例 2：全零输入 -> 输出必须全零（复位后状态干净）---
    {
        const int n = 64;
        std::vector<short> x(n, 0), y;
        Stats st; long hdr = 0;
        run_ip(x.data(), n, 1, st, y, hdr);
        bool ok = true;
        for (int i = 0; i < n; i++) if (y[i] != 0) ok = false;
        ok = ok && (hdr == 0) && (st.sat_count == 0);
        if (ok) pass++;
        else { fail++; printf("  FAIL all_zero: 输出非全零或 hdr/sat 异常\n"); }
    }

    // --- 用例 3：全幅直流（+ / -）-> DC 被抑制 + 数值正确 ---
    // DC 抑制的稳态判据阈值 = |Σh| + 余量，由系数表当场算出（不再硬编码某个
    // 采样率时代的数字），换系数表后判据自动跟随：
    //   45 fps 口径 Σh=5710 → DC −15.18 dB；30 fps 口径 Σh=897 → DC −31.25 dB。
    // 余量 +64 覆盖"满幅输入 32767/32768 的归一化差异 + 一次算术右移的舍入"。
    {
        long long s = 0;
        for (int k = 0; k < FIR_NUM_TAPS; k++) s += FIR_COEFF_Q15[k];
        int dc_bound = (int)((s < 0 ? -s : s) + 64);

        for (int sgn = 0; sgn < 2; sgn++) {
            const int n = 200;
            short v = sgn ? (short)-32768 : (short)32767;
            std::vector<short> x(n, v), y;
            Stats st; long hdr = 0;
            FirRef ref; ref.reset();
            std::vector<short> ey; unsigned esat = 0;
            ref.run(x.data(), n, ey, esat);
            run_ip(x.data(), n, 1, st, y, hdr);

            // 结构性判据：稳态输出幅值应等于 |Σh|（带通抑制 DC，数值由系数表决定）
            int peak = 0;
            for (int i = FIR_NUM_TAPS; i < n; i++) {
                int a = y[i] < 0 ? -y[i] : y[i];
                if (a > peak) peak = a;
            }
            bool ok = (y == ey) && (peak <= dc_bound) && (hdr == 0);
            if (ok) pass++;
            else {
                fail++;
                printf("  FAIL dc_%s: vals=%d 稳态峰值=%d(<=%d?) hdr=%ld\n",
                       sgn ? "neg" : "pos", (int)(y == ey), peak, dc_bound, hdr);
            }
        }
    }

    // --- 用例 4：奈奎斯特（逐样本交替满幅）-> 强抑制 ---
    {
        const int n = 200;
        std::vector<short> x(n), y;
        for (int i = 0; i < n; i++) x[i] = (i % 2 == 0) ? (short)32767 : (short)-32768;
        Stats st; long hdr = 0;
        FirRef ref; ref.reset();
        std::vector<short> ey; unsigned esat = 0;
        ref.run(x.data(), n, ey, esat);
        run_ip(x.data(), n, 1, st, y, hdr);
        bool ok = (y == ey) && (hdr == 0);
        if (ok) pass++;
        else { fail++; printf("  FAIL nyquist_alt: vals=%d hdr=%ld\n", (int)(y == ey), hdr); }
    }

    // --- 用例 5：n_samples = 1（最小段；TUSER 与 TLAST 同时为 1）---
    {
        const int n = 1;
        short xv = 12345;
        std::vector<short> y;
        Stats st; long hdr = 0;
        run_ip(&xv, n, 1, st, y, hdr);
        long long acc = (long long)FIR_COEFF_Q15[0] * (long long)xv;
        int exp = (int)(acc >> FIR_COEFF_SHIFT);
        bool ok = (y.size() == 1) && (y[0] == (short)exp) &&
                  (st.out_count == 1) && (hdr == 0);
        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL n=1: y=%d(exp %d) out_count=%u hdr=%ld\n",
                   (int)y[0], exp, st.out_count, hdr);
        }
    }

    // --- 用例 6：复位语义（不依赖黄金参考的独立证据）---
    //   6a) 同一段数据连跑两次（都 reset=1）-> 结果必须逐样本相同
    //   6b) 同一段数据 reset=0 承接上一段 -> 必须与"一次调用处理两段拼接"完全相同
    //   6c) 6b 的结果必须**不同于** reset=1 的那次（证明状态确实被继承了）
    {
        const int n = 128;
        std::vector<short> a(n), b(n);
        for (int i = 0; i < n; i++) {
            a[i] = (short)((i * 1301 + 7919) % 65536 - 32768);
            b[i] = (short)((i * 4099 + 104729) % 65536 - 32768);
        }
        Stats st; long hdr = 0;
        std::vector<short> y1, y2, yA, yB0, yB1, yAB;

        run_ip(a.data(), n, 1, st, y1, hdr);
        run_ip(a.data(), n, 1, st, y2, hdr);
        bool c6a = (y1 == y2);

        run_ip(a.data(), n, 1, st, yA, hdr);          // 段 A，reset=1
        run_ip(b.data(), n, 0, st, yB0, hdr);         // 段 B，承接 A
        run_ip(b.data(), n, 1, st, yB1, hdr);         // 段 B，独立

        std::vector<short> ab(a);
        ab.insert(ab.end(), b.begin(), b.end());
        run_ip(ab.data(), 2 * n, 1, st, yAB, hdr);    // 一次调用处理 A+B

        bool c6b = true;
        for (int i = 0; i < n; i++) if (yA[i] != yAB[i]) c6b = false;
        for (int i = 0; i < n; i++) if (yB0[i] != yAB[n + i]) c6b = false;
        bool c6c = (yB0 != yB1);

        if (c6a && c6b && c6c) pass++;
        else {
            fail++;
            printf("  FAIL reset semantics: 6a(重复同段)=%d 6b(分段==一次)=%d 6c(状态被继承)=%d\n",
                   (int)c6a, (int)c6b, (int)c6c);
        }
    }

    // --- 用例 7：饱和路径（满幅 1 Hz 方波应力 -> sat_count>0 且输出被夹住）---
    {
        const int n = 300;
        std::vector<short> x(n), y;
        for (int i = 0; i < n; i++)
            x[i] = (((i * 2) / 30) % 2 == 0) ? (short)32767 : (short)-32768;
        Stats st; long hdr = 0;
        FirRef ref; ref.reset();
        std::vector<short> ey; unsigned esat = 0;
        ref.run(x.data(), n, ey, esat);
        run_ip(x.data(), n, 1, st, y, hdr);

        bool clipped = false;
        for (int i = 0; i < n; i++)
            if (y[i] == 32767 || y[i] == -32768) clipped = true;
        bool ok = (y == ey) && (st.sat_count == esat) && (st.sat_count > 0) && clipped && (hdr == 0);
        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL saturation: vals=%d sat=%u(exp %u) clipped=%d hdr=%ld\n",
                   (int)(y == ey), st.sat_count, esat, (int)clipped, hdr);
        }
    }

    printf("  Layer 1: %d passed, %d failed\n", pass, fail);
    return fail;
}

// =============================================================================
//  第 2 层：跨语言黄金参考
// =============================================================================
struct Meta {
    int taps, shift, segments, total_samples;
    double fs;
};

static bool read_meta(const std::string &path, Meta &m)
{
    m.taps = m.shift = m.segments = m.total_samples = 0;
    m.fs = 0.0;
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        if      (!strcmp(line, "taps"))          m.taps = atoi(eq + 1);
        else if (!strcmp(line, "coeff_shift"))   m.shift = atoi(eq + 1);
        else if (!strcmp(line, "segments"))      m.segments = atoi(eq + 1);
        else if (!strcmp(line, "total_samples")) m.total_samples = atoi(eq + 1);
        else if (!strcmp(line, "fs"))            m.fs = atof(eq + 1);
    }
    fclose(f);
    return (m.taps > 0 && m.segments > 0);
}

struct SegRow {
    std::string name;
    int seg_id, n_samples, reset, sat_count, out_count;
};

static bool read_seg_csv(const std::string &path, std::vector<SegRow> &rows)
{
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#') continue;
        if (strncmp(line, "case,", 5) == 0) continue;
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

static bool read_golden_out(const std::string &path, int segments,
                            std::vector<std::vector<int> > &gold)
{
    gold.assign(segments + 1, std::vector<int>());
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[256];
    long rows = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#') continue;
        if (strncmp(line, "seg_id,", 7) == 0) continue;
        int sid, idx, y;
        if (sscanf(line, "%d,%d,%d", &sid, &idx, &y) == 3) {
            if (sid >= 1 && sid <= segments) {
                if ((int)gold[sid].size() <= idx) gold[sid].resize(idx + 1, 0);
                gold[sid][idx] = y;
                rows++;
            }
        }
    }
    fclose(f);
    return rows > 0;
}

static bool file_exists(const std::string &p)
{
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return false;
    fclose(f);
    return true;
}

static int test_golden(const std::string &dir)
{
    printf("---- [Layer 2] cross-language golden reference ----\n");

    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) {
        printf("  ERROR: cannot read %s/meta.txt\n", dir.c_str());
        return -1;
    }
    std::vector<SegRow> rows;
    if (!read_seg_csv(dir + "/golden_fir.csv", rows)) {
        printf("  ERROR: cannot read golden_fir.csv\n");
        return -1;
    }
    std::vector<std::vector<int> > gold;
    if (!read_golden_out(dir + "/golden_fir_out.csv", m.segments, gold)) {
        printf("  ERROR: cannot read golden_fir_out.csv\n");
        return -1;
    }
    if (m.taps != FIR_NUM_TAPS || m.shift != FIR_COEFF_SHIFT) {
        printf("  ERROR: 契约/系数不一致 —— meta taps=%d shift=%d，本工程 taps=%d shift=%d\n",
               m.taps, m.shift, FIR_NUM_TAPS, FIR_COEFF_SHIFT);
        printf("         （改过系数就要重新跑 gen_fir_vectors.py）\n");
        return -1;
    }

    // ---- 读输入样本 ----
    FILE *fs_bin = fopen((dir + "/series.bin").c_str(), "rb");
    if (!fs_bin) { printf("  ERROR: cannot open series.bin\n"); return -1; }
    std::vector<short> all;
    {
        unsigned char buf[2];
        while (fread(buf, 1, 2, fs_bin) == 2) {
            short v = (short)((unsigned)buf[0] | ((unsigned)buf[1] << 8));
            all.push_back(v);
        }
    }
    fclose(fs_bin);

    printf("  meta     : fs=%g Hz, N=%d, shift=%d, %d segments, %d samples\n",
           m.fs, m.taps, m.shift, m.segments, m.total_samples);

    if ((int)all.size() != m.total_samples) {
        printf("  ERROR: series.bin 样本数 %d != meta.total_samples %d\n",
               (int)all.size(), m.total_samples);
        return -1;
    }

    int pass = 0, fail = 0;
    long hdr_bad_total = 0;
    long sample_mismatch = 0;
    int offset = 0;
    unsigned prev_sid = 0;
    bool have_prev_sid = false;

    // 保存各段输出，供"分段 == 一次调用"的跨段一致性检查
    std::vector<std::vector<short> > outs(rows.size());
    std::vector<std::string> names(rows.size());

    for (size_t si = 0; si < rows.size(); si++) {
        const SegRow &r = rows[si];
        if (offset + r.n_samples > (int)all.size()) {
            printf("  ERROR: series.bin 提前结束（段 %d 需要 %d 个样本）\n", r.seg_id, r.n_samples);
            return -1;
        }
        Stats st;
        std::vector<short> y;
        long hdr = 0;
        run_ip(&all[offset], r.n_samples, r.reset, st, y, hdr);
        offset += r.n_samples;
        hdr_bad_total += hdr;
        outs[si] = y;
        names[si] = r.name;

        // seg_id 是**自 IP 上电以来的调用序号**（static 计数器，从 1 开始），
        // 所以这里校验的是"每次调用 +1"，而**不是**等于 CSV 里的段号 ——
        // Layer 1 已经调用过若干次，Layer 2 的段号自然不从 1 开始。
        bool sid_ok;
        unsigned exp_sid = 0;
        if (!have_prev_sid) {
            sid_ok = (st.seg_id > 0);
        } else {
            exp_sid = prev_sid + 1;
            sid_ok = (st.seg_id == exp_sid);
        }
        prev_sid = st.seg_id;
        have_prev_sid = true;

        bool ok = (st.out_count == (unsigned)r.out_count) &&
                  (st.sat_count == (unsigned)r.sat_count) && sid_ok && (hdr == 0);
        int bad = 0;
        if (r.seg_id >= 1 && r.seg_id <= m.segments) {
            const std::vector<int> &g = gold[r.seg_id];
            if ((int)g.size() != r.out_count) {
                printf("  ERROR: 黄金参考第 %d 段的样本数 %d != %d\n",
                       r.seg_id, (int)g.size(), r.out_count);
                return -1;
            }
            for (int i = 0; i < r.n_samples; i++) {
                if ((int)y[i] != g[i]) {
                    if (bad < 3)
                        printf("     seg %-16s i=%-5d got=%6d exp=%6d\n",
                               r.name.c_str(), i, (int)y[i], g[i]);
                    bad++;
                }
            }
        }
        sample_mismatch += bad;
        if (ok && bad == 0) pass++;
        else {
            fail++;
            if (ok == false)
                printf("  FAIL %-16s out_count=%u/%d sat=%u/%d seg_id=%u(期望 %u) hdr=%ld\n",
                       r.name.c_str(), st.out_count, r.out_count, st.sat_count,
                       r.sat_count, st.seg_id, exp_sid, hdr);
            if (bad > 0) printf("  FAIL %-16s 样本不一致 %d 个\n", r.name.c_str(), bad);
        }
    }

    // ---- 跨段一致性：同一串数据"分段调用"必须等于"一次调用" ----
    //   split_part1/2/3 用的正是 random_full 的前 512 个样本，
    //   且 reset 序列为 1,0,0 -> 三者拼接必须与 random_full 的逐样本输出完全相同。
    {
        int i_full = -1, i1 = -1, i2 = -1, i3 = -1;
        for (size_t i = 0; i < names.size(); i++) {
            if (names[i] == "random_full")  i_full = (int)i;
            if (names[i] == "split_part1")  i1 = (int)i;
            if (names[i] == "split_part2")  i2 = (int)i;
            if (names[i] == "split_part3")  i3 = (int)i;
        }
        if (i_full >= 0 && i1 >= 0 && i2 >= 0 && i3 >= 0) {
            std::vector<short> cat;
            cat.insert(cat.end(), outs[i1].begin(), outs[i1].end());
            cat.insert(cat.end(), outs[i2].begin(), outs[i2].end());
            cat.insert(cat.end(), outs[i3].begin(), outs[i3].end());
            const std::vector<short> &full = outs[i_full];
            bool same = (cat.size() == full.size());
            int bad = 0;
            if (same) {
                for (size_t i = 0; i < cat.size(); i++)
                    if (cat[i] != full[i]) bad++;
            }
            if (same && bad == 0) {
                printf("  [跨段一致性] 分段(reset=1,0,0) == 一次调用 ✅ (%d 样本)\n",
                       (int)cat.size());
                pass++;
            } else {
                printf("  FAIL [跨段一致性] 分段与一次调用不一致：%d 个样本不同\n", bad);
                fail++;
            }
        } else {
            printf("  [跨段一致性] SKIPPED —— 段名不齐（run gen_fir_vectors.py 重新生成）\n");
            fail++;
        }
    }

    printf("  Layer 2: %d passed, %d failed  (样本不一致 %ld, 流头错误 %ld)\n",
           pass, fail, sample_mismatch, hdr_bad_total);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_fir_filter: contract docs/interface.md (tolerance = 0) ====\n");
    printf("NOTE: 延迟线是 static（上电初始化、复位不清零）-> 每段序列的第一次调用必须 reset=1。\n");

    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);

    if (dir.empty() || !file_exists(dir + "/golden_fir.csv")) {
        if (forced) {
            printf("\n---- [Layer 2] GOLDEN: FAILED (data dir not usable: '%s') ----\n", dir.c_str());
            printf("  hint: run  python fpga/sim/gen_fir_vectors.py  first.\n");
            fail += 1;
        } else {
            printf("\n---- [Layer 2] GOLDEN: SKIPPED (no data dir) ----\n");
        }
    } else {
        int f2 = test_golden(dir);
        if (f2 < 0) { fail += 1; printf("  Layer 2: ERROR\n"); }
        else fail += f2;
    }

    printf("\n==== RESULT: %s ====\n", (fail == 0) ? "PASS" : "FAIL");
    return (fail == 0) ? 0 : 1;
}
