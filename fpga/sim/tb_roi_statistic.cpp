// =============================================================================
//  tb_roi_statistic.cpp  ——  roi_statistic 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：docs/interface.md 第 4.2 节（比对口径：全整数，容差 0，逐点严格相等）
//
//  本测试台做两件事：
//
//    【第 1 层】自包含边界用例（不依赖任何外部文件）
//      小尺寸帧（8x6）内存构造，覆盖：随机 / 全零 / 全满 / 单点变化，
//      ROI 覆盖：全图 / 中部 / 左上单点 / 右下单点 / 空 ROI(x0==x1) /
//                反向 ROI(x0>x1) / 越界 ROI。
//      → 期望值由本文件内的朴素二重循环独立算出（简单到不用怀疑它对不对）。
//      → 这一层即使没有 Python 产物也能跑，用于快速迭代和"区分是 IP 错还是脚手架错"。
//
//    【第 2 层】跨语言黄金参考比对（需要 Python 产物）
//      读 fpga/sim/data/frames.bin（5 帧 640x480 RGB888）+ golden_roi.csv
//      （由 fpga/sim/gen_frames.py 用 Python/NumPy 独立算出），逐行比对。
//      → 这是真正意义上的"硬件算的 vs 软件算的"，容差 0。
//
//  两层都额外校验：video_out 必须与 video_in 逐字节一致（透传不损坏数据）。
//
//  用法（由 run_hls.tcl 自动传参，也可手动）：
//    tb_roi_statistic <data_dir>
//  其中 <data_dir> 需含 frames.bin 与 golden_roi.csv。
//
//  【待验证假设】C 仿真不建模 hls::stream 的 DEPTH（深度只在 RTL 协同仿真里体现），
//  因此本测试台采用"先灌满、再调用、后排空"的写法。若 csim 报流溢出，改线程式 TB。
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<24, 1, 1, 1> axis_pix_t;

// IP 原型（pragmas 在 .cpp 内，测试台不需要）
void roi_statistic(hls::stream<axis_pix_t> &video_in,
                   hls::stream<axis_pix_t> &video_out,
                   ap_uint<11> roi_x0, ap_uint<11> roi_y0,
                   ap_uint<11> roi_x1, ap_uint<11> roi_y1,
                   ap_uint<16> width, ap_uint<16> height,
                   ap_uint<32> &sum_r, ap_uint<32> &sum_g, ap_uint<32> &sum_b,
                   ap_uint<32> &count, ap_uint<32> &frame_id);

// -----------------------------------------------------------------------------
// 统计结果
// -----------------------------------------------------------------------------
struct Stats {
    unsigned sum_r, sum_g, sum_b, count, frame_id;
};

// -----------------------------------------------------------------------------
// 把一帧喂进 IP，排空输出流并校验透传一致性
// -----------------------------------------------------------------------------
static void run_ip(const unsigned char *frame, int w, int h,
                   int x0, int y0, int x1, int y1,
                   Stats &st, long &pass_mismatch)
{
    hls::stream<axis_pix_t> in, outp;
    pass_mismatch = 0;

    // ---- 灌入像素流（TUSER 标帧首，TLAST 标行末）----
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const unsigned char *px = frame + 3 * (y * w + x);
            axis_pix_t p;
            p.data = (ap_uint<24>)(((unsigned)px[2] << 16) |
                                   ((unsigned)px[1] << 8) |
                                   ((unsigned)px[0]));
            p.keep = 0x7;
            p.strb = 0x7;
            p.user = (y == 0 && x == 0) ? 1 : 0;
            p.last = (x == w - 1) ? 1 : 0;
            p.id   = 0;
            p.dest = 0;
            in.write(p);
        }
    }

    // ---- 调用 IP ----
    ap_uint<32> sr, sg, sb, cnt, fid;
    roi_statistic(in, outp,
                  (ap_uint<11>)x0, (ap_uint<11>)y0,
                  (ap_uint<11>)x1, (ap_uint<11>)y1,
                  (ap_uint<16>)w, (ap_uint<16>)h,
                  sr, sg, sb, cnt, fid);

    st.sum_r = (unsigned)sr;
    st.sum_g = (unsigned)sg;
    st.sum_b = (unsigned)sb;
    st.count = (unsigned)cnt;
    st.frame_id = (unsigned)fid;

    // ---- 排空并校验透传 ----
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_pix_t q = outp.read();
            const unsigned char *px = frame + 3 * (y * w + x);
            unsigned r = (unsigned)q.data(7, 0);
            unsigned g = (unsigned)q.data(15, 8);
            unsigned b = (unsigned)q.data(23, 16);
            bool ok = (r == px[0]) && (g == px[1]) && (b == px[2]) &&
                      ((unsigned)q.user == ((y == 0 && x == 0) ? 1u : 0u)) &&
                      ((unsigned)q.last == ((x == w - 1) ? 1u : 0u)) &&
                      ((unsigned)q.keep == 7u) && ((unsigned)q.strb == 7u);
            if (!ok) pass_mismatch++;
        }
    }
}

// -----------------------------------------------------------------------------
// 朴素参考实现（第 1 层用；故意写得笨，便于人工核对）
// -----------------------------------------------------------------------------
static void brute_force(const unsigned char *frame, int w, int h,
                        int x0, int y0, int x1, int y1,
                        unsigned &sr, unsigned &sg, unsigned &sb, unsigned &cnt)
{
    sr = sg = sb = cnt = 0;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            if (x >= x0 && x < x1 && y >= y0 && y < y1) {
                const unsigned char *px = frame + 3 * (y * w + x);
                sr += px[0];
                sg += px[1];
                sb += px[2];
                cnt++;
            }
        }
    }
}

// -----------------------------------------------------------------------------
// 第 1 层：自包含边界用例
// -----------------------------------------------------------------------------
static int test_embedded(void)
{
    const int W = 8, H = 6;
    const int N = W * H * 3;

    std::vector<unsigned char> f_rand(N), f_zero(N, 0), f_full(N, 255), f_single(N, 0);
    for (int i = 0; i < N; i++) {
        f_rand[i] = (unsigned char)((i * 7 + 3) & 0xFF);
    }
    int cen = 3 * ((H / 2) * W + (W / 2));   // 画面正中那个像素
    f_single[cen + 0] = 255;
    f_single[cen + 1] = 255;
    f_single[cen + 2] = 255;

    struct Case { const char *name; int x0, y0, x1, y1; };
    const Case cases[] = {
        {"full_frame",       0,     0,     W,     H    },
        {"center_roi",       W / 4, H / 4, W * 3 / 4, H * 3 / 4},
        {"top_left_1x1",     0,     0,     1,     1    },
        {"bottom_right_1x1", W - 1, H - 1, W,     H    },
        {"empty_roi",        2,     2,     2,     4    },
        {"inverted_roi",     6,     5,     1,     2    },
        {"oversized_roi",    W - 2, H - 2, W + 5, H + 5},
    };
    const int NC = (int)(sizeof(cases) / sizeof(cases[0]));

    struct Frame { const char *name; const unsigned char *buf; };
    const Frame frames[] = {
        {"rand",   f_rand.data()},
        {"zero",   f_zero.data()},
        {"full",   f_full.data()},
        {"single", f_single.data()},
    };
    const int NF = (int)(sizeof(frames) / sizeof(frames[0]));

    int pass = 0, fail = 0;

    printf("---- [Layer 1] embedded boundary cases (%dx%d) ----\n", W, H);
    for (int fi = 0; fi < NF; fi++) {
        for (int ci = 0; ci < NC; ci++) {
            const Case &c = cases[ci];
            Stats st;
            long pt = 0;
            run_ip(frames[fi].buf, W, H, c.x0, c.y0, c.x1, c.y1, st, pt);

            unsigned er, eg, eb, ec;
            brute_force(frames[fi].buf, W, H, c.x0, c.y0, c.x1, c.y1, er, eg, eb, ec);

            bool ok = (st.sum_r == er) && (st.sum_g == eg) &&
                      (st.sum_b == eb) && (st.count == ec) && (pt == 0);
            if (ok) {
                pass++;
            } else {
                fail++;
                printf("  FAIL %-6s / %-16s got{%u,%u,%u,%u} exp{%u,%u,%u,%u} pass_mismatch=%ld\n",
                       frames[fi].name, c.name, st.sum_r, st.sum_g, st.sum_b, st.count,
                       er, eg, eb, ec, pt);
            }
        }
    }
    printf("  Layer 1: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 层：跨语言黄金参考（frames.bin + golden_roi.csv）
// -----------------------------------------------------------------------------

struct GoldenRow {
    char  case_name[64];
    int   frame, x0, y0, x1, y1;
    unsigned sum_r, sum_g, sum_b, count;
};

static bool file_exists(const std::string &p)
{
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return false;
    fclose(f);
    return true;
}

static std::string find_data_dir(int argc, char **argv, bool &dir_forced)
{
    dir_forced = false;
    if (argc > 1) {
        dir_forced = true;                 // 调用方显式指定 -> 找不到硬失败
        return std::string(argv[1]);
    }
    // 兜底搜索（相对 csim 工作目录）
    const char *cands[] = {"sim/data", "../sim/data", "../../sim/data",
                           "../../../sim/data", "data", "./data"};
    for (int i = 0; i < (int)(sizeof(cands) / sizeof(cands[0])); i++) {
        std::string d = cands[i];
        if (file_exists(d + "/golden_roi.csv")) return d;
    }
    return std::string("");
}

static int test_golden(const std::string &dir)
{
    const std::string csv_path = dir + "/golden_roi.csv";
    const std::string bin_path = dir + "/frames.bin";

    printf("---- [Layer 2] cross-language golden reference ----\n");
    printf("  data dir : %s\n", dir.c_str());

    // ---- 解析 CSV ----
    FILE *fc = fopen(csv_path.c_str(), "r");
    if (!fc) {
        printf("  ERROR: cannot open %s\n", csv_path.c_str());
        return -1;
    }

    int meta_w = 0, meta_h = 0, meta_frames = 0, meta_bytes = 0;
    std::vector<GoldenRow> rows;
    char line[512];

    while (fgets(line, sizeof(line), fc)) {
        if (strncmp(line, "# meta", 6) == 0) {
            const char *p;
            if ((p = strstr(line, "width="))  != NULL) meta_w      = atoi(p + 6);
            if ((p = strstr(line, "height=")) != NULL) meta_h      = atoi(p + 7);
            if ((p = strstr(line, "frames=")) != NULL) meta_frames = atoi(p + 7);
            if ((p = strstr(line, "bytes="))  != NULL) meta_bytes  = atoi(p + 6);
            continue;
        }
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        if (strncmp(line, "case,", 5) == 0) continue;   // 表头

        GoldenRow r;
        memset(&r, 0, sizeof(r));
        int n = sscanf(line, "%63[^,],%d,%d,%d,%d,%d,%u,%u,%u,%u",
                       r.case_name, &r.frame, &r.x0, &r.y0, &r.x1, &r.y1,
                       &r.sum_r, &r.sum_g, &r.sum_b, &r.count);
        if (n == 10) rows.push_back(r);
    }
    fclose(fc);

    if (rows.empty()) {
        printf("  ERROR: %s has no data rows\n", csv_path.c_str());
        return -1;
    }
    if (meta_w <= 0 || meta_h <= 0 || meta_frames <= 0 || meta_bytes <= 0) {
        printf("  ERROR: bad '# meta' header in %s\n", csv_path.c_str());
        return -1;
    }
    printf("  meta     : %dx%d, %d frames, %d bytes/frame, %d golden rows\n",
           meta_w, meta_h, meta_frames, meta_bytes, (int)rows.size());

    // ---- 打开 frames.bin 并校验大小 ----
    FILE *fb = fopen(bin_path.c_str(), "rb");
    if (!fb) {
        printf("  ERROR: cannot open %s\n", bin_path.c_str());
        return -1;
    }
    fseek(fb, 0, SEEK_END);
    long fsz = ftell(fb);
    fseek(fb, 0, SEEK_SET);
    long expect = (long)meta_frames * meta_bytes;
    if (fsz != expect) {
        printf("  ERROR: %s size %ld != expected %ld\n", bin_path.c_str(), fsz, expect);
        fclose(fb);
        return -1;
    }

    // ---- 逐行比对 ----
    std::vector<unsigned char> buf(meta_bytes);
    int pass = 0, fail = 0;
    int last_frame = -1;
    unsigned last_fid = 0;
    long total_pt = 0;

    for (size_t i = 0; i < rows.size(); i++) {
        const GoldenRow &r = rows[i];

        if (r.frame != last_frame) {
            fseek(fb, (long)r.frame * meta_bytes, SEEK_SET);
            size_t got = fread(buf.data(), 1, meta_bytes, fb);
            if (got != (size_t)meta_bytes) {
                printf("  ERROR: short read for frame %d\n", r.frame);
                fclose(fb);
                return -1;
            }
            last_frame = r.frame;
        }

        Stats st;
        long pt = 0;
        run_ip(buf.data(), meta_w, meta_h, r.x0, r.y0, r.x1, r.y1, st, pt);
        total_pt += pt;

        bool ok = (st.sum_r == r.sum_r) && (st.sum_g == r.sum_g) &&
                  (st.sum_b == r.sum_b) && (st.count == r.count) && (pt == 0);

        // frame_id 应逐次自增（内部 static 计数器）
        bool fid_ok = (i == 0) ? true : (st.frame_id > last_fid);
        last_fid = st.frame_id;

        if (ok && fid_ok) {
            pass++;
        } else {
            fail++;
            printf("  FAIL %-16s frame=%d roi=(%d,%d,%d,%d)\n",
                   r.case_name, r.frame, r.x0, r.y0, r.x1, r.y1);
            printf("       got {%u,%u,%u,%u} fid=%u\n",
                   st.sum_r, st.sum_g, st.sum_b, st.count, st.frame_id);
            printf("       exp {%u,%u,%u,%u}\n",
                   r.sum_r, r.sum_g, r.sum_b, r.count);
            if (pt) printf("       pass-through mismatches: %ld\n", pt);
        }
    }

    fclose(fb);
    printf("  Layer 2: %d passed, %d failed  (pass-through mismatched pixels: %ld)\n",
           pass, fail, total_pt);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_roi_statistic: contract docs/interface.md (tolerance = 0) ====\n");

    int fail = test_embedded();

    bool forced = false;
    std::string dir = find_data_dir(argc, argv, forced);

    if (dir.empty() || !file_exists(dir + "/golden_roi.csv")) {
        if (forced) {
            printf("\n---- [Layer 2] GOLDEN: FAILED (data dir not usable) ----\n");
            printf("  specified dir: '%s'\n", dir.c_str());
            printf("  hint: run  python fpga/sim/gen_frames.py  first.\n");
            fail += 1;
        } else {
            printf("\n---- [Layer 2] GOLDEN: SKIPPED (no data dir) ----\n");
            printf("  This run only proves the IP internally; it is NOT the\n");
            printf("  cross-language acceptance. Run gen_frames.py then re-run.\n");
        }
    } else {
        int f2 = test_golden(dir);
        if (f2 < 0) {
            fail += 1;
            printf("  Layer 2: ERROR while running golden comparison\n");
        } else {
            fail += f2;
        }
    }

    printf("\n==== RESULT: %s ====\n", (fail == 0) ? "PASS" : "FAIL");
    return (fail == 0) ? 0 : 1;
}
