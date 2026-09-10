// =============================================================================
//  tb_motion_quality.cpp  ——  motion_quality 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：docs/interface.md 第 3.3 / 4.2 节
//
//  【本 IP 是有状态的】片内保留"上一帧"灰度缓存，因此测试台必须**按帧序喂**。
//  两条语义（契约已冻结）：
//    1) 第 0 帧的输出无意义（上电后 prev_buf 内容未定义）→ 测试台只喂不比；
//       有效比对从第 1 帧开始。因此本测试台的参考实现只需跟踪"上一帧"。
//    2) 阈值是**严格大于**：|cur-prev| > thresh 才算运动像素（等于不算）。
//       第 1 层专门用"恰好等于阈值"的用例把这个边界钉死。
//
//  【第 1 层】自包含边界用例：4 像素小帧的确定性序列，期望值由本文件内朴素实现算出。
//  【第 2 层】跨语言黄金参考：读 fpga/sim/data_motion/{gray.bin, golden_motion.csv, meta.txt}
//             （由 Python/NumPy 独立算出），按帧序比对。
//
//  两层都额外校验：输出流的 TUSER / TLAST 必须与输入一致（透传不损坏数据）。
//
//  用法： tb_motion_quality <data_dir>
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<8, 1, 1, 1> axis_gray_t;

void motion_quality(hls::stream<axis_gray_t> &gray_in,
                    hls::stream<axis_gray_t> &gray_out,
                    ap_uint<16> width, ap_uint<16> height,
                    ap_uint<8> motion_thresh,
                    ap_uint<32> &diff_total, ap_uint<32> &motion_pixels,
                    ap_uint<32> &motion_ratio_q16, ap_uint<32> &count,
                    ap_uint<32> &frame_id);

struct Stats {
    unsigned diff_total, motion_pixels, motion_ratio_q16, count, frame_id;
};

// ---- 朴素参考实现（第 1 层用；故意写得笨，便于人工核对）---------------------
static void ref_motion(const unsigned char *cur, const unsigned char *prev, int n,
                       unsigned thresh,
                       unsigned &diff_total, unsigned &motion_pixels, unsigned &ratio)
{
    diff_total = 0;
    motion_pixels = 0;
    for (int i = 0; i < n; i++) {
        int d = (int)cur[i] - (int)prev[i];
        if (d < 0) d = -d;
        diff_total += (unsigned)d;
        if ((unsigned)d > thresh) motion_pixels++;   // 严格大于
    }
    ratio = (n > 0) ? (unsigned)(((unsigned long long)motion_pixels << 16) / (unsigned)n) : 0u;
}

// ---- 运行一次 IP ----
static void run_ip(const unsigned char *gray, int w, int h, unsigned thresh,
                   Stats &st, std::vector<unsigned char> &gout, long &hdr_mismatch)
{
    hls::stream<axis_gray_t> in, out;
    hdr_mismatch = 0;
    gout.assign((size_t)w * h, 0);

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_gray_t p;
            p.data = (ap_uint<8>)gray[(size_t)y * w + x];
            p.keep = 0x1;
            p.strb = 0x1;
            p.user = (y == 0 && x == 0) ? 1 : 0;
            p.last = (x == w - 1) ? 1 : 0;
            p.id = 0; p.dest = 0;
            in.write(p);
        }
    }

    ap_uint<32> dt, mp, rat, cnt, fid;
    motion_quality(in, out, (ap_uint<16>)w, (ap_uint<16>)h, (ap_uint<8>)thresh,
                   dt, mp, rat, cnt, fid);

    st.diff_total       = (unsigned)dt;
    st.motion_pixels    = (unsigned)mp;
    st.motion_ratio_q16 = (unsigned)rat;
    st.count            = (unsigned)cnt;
    st.frame_id         = (unsigned)fid;

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_gray_t q = out.read();
            gout[(size_t)y * w + x] = (unsigned char)q.data;
            bool ok = ((unsigned)q.data == (unsigned)gray[(size_t)y * w + x]) &&
                      ((unsigned)q.user == ((y == 0 && x == 0) ? 1u : 0u)) &&
                      ((unsigned)q.last == ((x == w - 1) ? 1u : 0u));
            if (!ok) hdr_mismatch++;
        }
    }
}

// ---- meta.txt / csv 解析 ----
// 注意：motion_quality 的工作尺寸是 rgb2gray **缩小后**的尺寸（out_width x out_height），
// 即 gray.bin 每帧的尺寸；in_width/in_height 是原始 RGB 尺寸，本测试台不用。
struct Meta {
    int in_width, in_height, out_width, out_height;
    int frames, rgb_bytes, gray_bytes, motion_thresh;
};

static bool read_meta(const std::string &path, Meta &m)
{
    m.in_width = m.in_height = m.out_width = m.out_height = 0;
    m.frames = m.rgb_bytes = m.gray_bytes = m.motion_thresh = 0;
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        int v = atoi(eq + 1);
        if      (!strcmp(line, "in_width"))      m.in_width = v;
        else if (!strcmp(line, "in_height"))     m.in_height = v;
        else if (!strcmp(line, "out_width"))     m.out_width = v;
        else if (!strcmp(line, "out_height"))    m.out_height = v;
        else if (!strcmp(line, "frames"))        m.frames = v;
        else if (!strcmp(line, "rgb_bytes"))     m.rgb_bytes = v;
        else if (!strcmp(line, "gray_bytes"))    m.gray_bytes = v;
        else if (!strcmp(line, "motion_thresh")) m.motion_thresh = v;
    }
    fclose(f);
    return (m.out_width > 0 && m.out_height > 0 && m.frames > 0);
}

struct GoldRow { int frame; unsigned dt, mp, rat, cnt; };

static bool file_exists(const std::string &p)
{
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return false;
    fclose(f);
    return true;
}

// -----------------------------------------------------------------------------
// 第 1 层：自包含序列（含"恰好等于阈值"的边界）
// -----------------------------------------------------------------------------
static int test_embedded(void)
{
    printf("---- [Layer 1] embedded sequence, thresh=16 (boundary pinned) ----\n");

    const int N = 4;            // 4 像素 = 1 行
    const unsigned TH = 16;

    // 逐帧灰度 + 该帧的期望值（第 0 帧期望为"忽略"）
    unsigned char f0[N] = {0, 0, 0, 0};
    unsigned char f1[N] = {0, 0, 0, 0};
    unsigned char f2[N] = {16, 16, 16, 16};      // 与上一帧差恰为 16 == 阈值 -> 不算运动
    unsigned char f3[N] = {17, 0, 16, 33};
    unsigned char f4[N] = {0, 0, 0, 0};
    unsigned char f5[N] = {255, 255, 255, 255};
    unsigned char f6[N] = {255, 255, 255, 255};  // 与上一帧相同

    struct Step { const char *name; unsigned char *buf; bool check; };
    Step steps[] = {
        {"prime(f0)", f0, false},   // 只喂不比：prev_buf 上电未定义
        {"zero->zero", f1, true},
        {"diff==thresh", f2, true}, // 关键边界
        {"mixed", f3, true},
        {"mixed->zero", f4, true},
        {"zero->full", f5, true},
        {"full->full", f6, true},
    };
    const int NS = (int)(sizeof(steps) / sizeof(steps[0]));

    int pass = 0, fail = 0;
    unsigned char prev[N];
    bool have_prev = false;

    for (int s = 0; s < NS; s++) {
        Stats st;
        std::vector<unsigned char> gout;
        long hm = 0;
        run_ip(steps[s].buf, N, 1, TH, st, gout, hm);

        if (!steps[s].check) {
            memcpy(prev, steps[s].buf, N);
            have_prev = true;
            continue;
        }
        if (!have_prev) { fail++; printf("  FAIL %s: no prime frame\n", steps[s].name); continue; }

        unsigned edt, emp, erat;
        ref_motion(steps[s].buf, prev, N, TH, edt, emp, erat);

        bool ok = (st.diff_total == edt) && (st.motion_pixels == emp) &&
                  (st.motion_ratio_q16 == erat) && (st.count == (unsigned)N) && (hm == 0);
        if (ok) {
            pass++;
        } else {
            fail++;
            printf("  FAIL %-14s got{diff=%u mp=%u ratio=%u cnt=%u} exp{diff=%u mp=%u ratio=%u cnt=%d} hdr=%ld\n",
                   steps[s].name, st.diff_total, st.motion_pixels, st.motion_ratio_q16, st.count,
                   edt, emp, erat, N, hm);
        }
        memcpy(prev, steps[s].buf, N);
    }

    printf("  Layer 1: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 层：跨语言黄金参考
// -----------------------------------------------------------------------------
static int test_golden(const std::string &dir)
{
    printf("---- [Layer 2] cross-language golden reference ----\n");

    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) {
        printf("  ERROR: cannot read %s/meta.txt\n", dir.c_str());
        return -1;
    }

    // ---- 读 golden_motion.csv ----
    FILE *fc = fopen((dir + "/golden_motion.csv").c_str(), "r");
    if (!fc) { printf("  ERROR: cannot open golden_motion.csv\n"); return -1; }
    std::vector<GoldRow> rows;
    char line[512];
    while (fgets(line, sizeof(line), fc)) {
        if (line[0] == '#') continue;
        if (strncmp(line, "frame,", 6) == 0) continue;
        GoldRow r;
        if (sscanf(line, "%d,%u,%u,%u,%u", &r.frame, &r.dt, &r.mp, &r.rat, &r.cnt) == 5)
            rows.push_back(r);
    }
    fclose(fc);
    if (rows.empty()) { printf("  ERROR: no data rows in golden_motion.csv\n"); return -1; }

    printf("  meta     : work %dx%d (RGB in %dx%d), %d frames, gray_bytes=%d, motion_thresh=%d, %d golden rows\n",
           m.out_width, m.out_height, m.in_width, m.in_height,
           m.frames, m.gray_bytes, m.motion_thresh, (int)rows.size());

    FILE *fg = fopen((dir + "/gray.bin").c_str(), "rb");
    if (!fg) { printf("  ERROR: cannot open gray.bin\n"); return -1; }

    std::vector<unsigned char> frame(m.gray_bytes);
    int pass = 0, fail = 0;
    long total_hdr = 0;
    int ri = 0;

    for (int fi = 0; fi < m.frames; fi++) {
        if (fread(frame.data(), 1, m.gray_bytes, fg) != (size_t)m.gray_bytes) {
            printf("  ERROR: short read at frame %d\n", fi);
            fclose(fg);
            return -1;
        }

        Stats st;
        std::vector<unsigned char> gout;
        long hm = 0;
        run_ip(frame.data(), m.out_width, m.out_height, (unsigned)m.motion_thresh, st, gout, hm);
        total_hdr += hm;

        if (fi == 0) continue;   // 第 0 帧输出无意义，只喂不比

        if (ri >= (int)rows.size() || rows[ri].frame != fi) {
            printf("  ERROR: golden row mismatch at frame %d\n", fi);
            fclose(fg);
            return -1;
        }
        const GoldRow &r = rows[ri++];
        bool ok = (st.diff_total == r.dt) && (st.motion_pixels == r.mp) &&
                  (st.motion_ratio_q16 == r.rat) && (st.count == r.cnt) && (hm == 0);
        if (ok) {
            pass++;
        } else {
            fail++;
            printf("  FAIL frame=%d\n", fi);
            printf("       got {diff=%u mp=%u ratio=%u cnt=%u}\n",
                   st.diff_total, st.motion_pixels, st.motion_ratio_q16, st.count);
            printf("       exp {diff=%u mp=%u ratio=%u cnt=%u}  hdr=%ld\n",
                   r.dt, r.mp, r.rat, r.cnt, hm);
        }
    }
    fclose(fg);

    printf("  Layer 2: %d passed, %d failed  (stream header errors: %ld)\n", pass, fail, total_hdr);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_motion_quality: contract docs/interface.md (tolerance = 0) ====\n");
    printf("NOTE: frame 0 output is invalid by design (prev-frame buffer undefined at power-on).\n");

    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);

    if (dir.empty() || !file_exists(dir + "/golden_motion.csv")) {
        if (forced) {
            printf("\n---- [Layer 2] GOLDEN: FAILED (data dir not usable: '%s') ----\n", dir.c_str());
            printf("  hint: run  python fpga/sim/gen_motion_vectors.py  first.\n");
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
