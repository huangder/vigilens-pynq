// =============================================================================
//  tb_rgb2gray.cpp  ——  rgb2gray (v2: 灰度化 + 3/5 抽取) 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：docs/interface.md 第 3.4 / 4.2 节
//        灰度 Y=(77R+150G+29B+128)>>8 ；缩放 = 保留 x%5<3 且 y%5<3
//
//  【第 1 层】自包含边界用例：小尺寸内存构造（输入 10x5 -> 输出 6x3），
//     (a) 系数用例：纯红/纯绿/纯蓝/白/黑/灰/混合（均匀色，输出应全为该灰度）
//     (b) 抽取用例：每个像素颜色都不同，逐点核对"抽对了位置"（不只对数量）
//     期望值全部由本文件内的朴素实现独立算出。
//  【第 2 层】跨语言黄金参考：读 data_motion/{rgb_frames.bin, gray.bin, meta.txt}
//     （gray.bin 由 Python/NumPy 独立算出），**逐像素逐字节**比对整帧。
//
//  两层都额外校验：输出 TUSER(输出帧首) / TLAST(输出行末) 位置正确。
//
//  用法： tb_rgb2gray <data_dir>
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
typedef ap_axiu<8,  1, 1, 1> axis_gray_t;

#define DECIM_NUM 3
#define DECIM_DEN 5

void rgb2gray(hls::stream<axis_pix_t> &rgb_in,
              hls::stream<axis_gray_t> &gray_out,
              ap_uint<16> width, ap_uint<16> height,
              ap_uint<16> &out_width, ap_uint<16> &out_height,
              ap_uint<32> &pixel_count, ap_uint<32> &sum_gray,
              ap_uint<32> &frame_id);

// ---- 与 RTL 无关的朴素参考实现（第 1 层用；故意写得笨，便于人工核对）---------
static unsigned char ref_gray(unsigned char r, unsigned char g, unsigned char b)
{
    return (unsigned char)((77u * r + 150u * g + 29u * b + 128u) >> 8);
}

static bool ref_keep(int i) { return (i % DECIM_DEN) < DECIM_NUM; }

// ---- 运行一次 IP ----
static void run_ip(const unsigned char *rgb, int w, int h,
                   std::vector<unsigned char> &gout,
                   int &ow_out, int &oh_out,
                   unsigned &pixel_count, unsigned &sum_gray, unsigned &frame_id,
                   long &hdr_mismatch)
{
    hls::stream<axis_pix_t>  in;
    hls::stream<axis_gray_t> out;
    hdr_mismatch = 0;

    const int ow = w / DECIM_DEN * DECIM_NUM;
    const int oh = h / DECIM_DEN * DECIM_NUM;
    gout.assign((size_t)ow * oh, 0);

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const unsigned char *px = rgb + 3 * (y * w + x);
            axis_pix_t p;
            p.data = (ap_uint<24>)(((unsigned)px[2] << 16) |
                                   ((unsigned)px[1] << 8) |
                                   ((unsigned)px[0]));
            p.keep = 0x7;
            p.strb = 0x7;
            p.user = (y == 0 && x == 0) ? 1 : 0;
            p.last = (x == w - 1) ? 1 : 0;
            p.id = 0; p.dest = 0;
            in.write(p);
        }
    }

    ap_uint<16> uw, uh;
    ap_uint<32> pc, sg, fid;
    rgb2gray(in, out, (ap_uint<16>)w, (ap_uint<16>)h, uw, uh, pc, sg, fid);

    ow_out = (int)uw;
    oh_out = (int)uh;
    pixel_count = (unsigned)pc;
    sum_gray    = (unsigned)sg;
    frame_id    = (unsigned)fid;

    // 排空并校验输出流的 TUSER / TLAST 与数据
    for (int yy = 0; yy < oh; yy++) {
        for (int xx = 0; xx < ow; xx++) {
            axis_gray_t q = out.read();
            gout[(size_t)yy * ow + xx] = (unsigned char)q.data;

            unsigned exp_user = (yy == 0 && xx == 0) ? 1u : 0u;
            unsigned exp_last = (xx == ow - 1) ? 1u : 0u;
            bool ok = ((unsigned)q.user == exp_user) &&
                      ((unsigned)q.last == exp_last) &&
                      ((unsigned)q.keep == 1u) && ((unsigned)q.strb == 1u);
            if (!ok) hdr_mismatch++;
        }
    }
}

// ---- meta.txt 解析 ----
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
        if      (!strcmp(line, "in_width"))     m.in_width = v;
        else if (!strcmp(line, "in_height"))    m.in_height = v;
        else if (!strcmp(line, "out_width"))    m.out_width = v;
        else if (!strcmp(line, "out_height"))   m.out_height = v;
        else if (!strcmp(line, "frames"))       m.frames = v;
        else if (!strcmp(line, "rgb_bytes"))    m.rgb_bytes = v;
        else if (!strcmp(line, "gray_bytes"))   m.gray_bytes = v;
        else if (!strcmp(line, "motion_thresh"))m.motion_thresh = v;
    }
    fclose(f);
    return (m.in_width > 0 && m.in_height > 0 && m.frames > 0);
}

static bool file_exists(const std::string &p)
{
    FILE *f = fopen(p.c_str(), "rb");
    if (!f) return false;
    fclose(f);
    return true;
}

// -----------------------------------------------------------------------------
// 第 1 层：自包含边界用例
// -----------------------------------------------------------------------------
static int test_embedded(void)
{
    printf("---- [Layer 1] embedded cases (in 10x5 -> out 6x3) ----\n");

    const int W = 10, H = 5;
    const int OW = 6, OH = 3;
    int pass = 0, fail = 0;
    std::vector<unsigned char> rgb((size_t)W * H * 3);

    // (a) 系数用例：均匀色 -> 输出应全为该灰度
    struct C { const char *name; unsigned char r, g, b; int exp; };
    const C cases[] = {
        {"pure_red",   255,   0,   0,  77},
        {"pure_green",   0, 255,   0, 149},
        {"pure_blue",    0,   0, 255,  29},
        {"white",      255, 255, 255, 255},
        {"black",        0,   0,   0,   0},
        {"gray_128",   128, 128, 128, 128},
        {"mixed",       10,  20,  30, ref_gray(10, 20, 30)},
    };
    const int NC = (int)(sizeof(cases) / sizeof(cases[0]));

    for (int c = 0; c < NC; c++) {
        for (int i = 0; i < W * H; i++) {
            rgb[3 * i + 0] = cases[c].r;
            rgb[3 * i + 1] = cases[c].g;
            rgb[3 * i + 2] = cases[c].b;
        }
        std::vector<unsigned char> gout;
        int ow, oh; unsigned pc, sg, fid; long hm = 0;
        run_ip(rgb.data(), W, H, gout, ow, oh, pc, sg, fid, hm);

        bool ok = (hm == 0) && (ow == OW) && (oh == OH) && (pc == (unsigned)(OW * OH));
        unsigned exp_sum = 0;
        for (int i = 0; i < OW * OH && ok; i++) {
            if (gout[i] != (unsigned char)cases[c].exp) ok = false;
            exp_sum += (unsigned char)cases[c].exp;
        }
        if (ok && sg != exp_sum) ok = false;

        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL %-12s out=%dx%d got[0]=%u (exp %d) pc=%u sum=%u (exp %u) hdr=%ld\n",
                   cases[c].name, ow, oh, gout.empty() ? 0u : (unsigned)gout[0],
                   cases[c].exp, pc, sg, exp_sum, hm);
        }
    }

    // (b) 抽取用例：每个像素颜色都不同 -> 逐点核对"抽对了位置"
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            unsigned char *px = &rgb[3 * (y * W + x)];
            px[0] = (unsigned char)((x * 25) & 0xFF);
            px[1] = (unsigned char)((y * 50) & 0xFF);
            px[2] = (unsigned char)(((x + y) * 11) & 0xFF);
        }
    }
    {
        std::vector<unsigned char> gout;
        int ow, oh; unsigned pc, sg, fid; long hm = 0;
        run_ip(rgb.data(), W, H, gout, ow, oh, pc, sg, fid, hm);

        int bad = 0;
        unsigned exp_sum = 0;
        int k = 0;
        for (int y = 0; y < H; y++) {
            if (!ref_keep(y)) continue;
            for (int x = 0; x < W; x++) {
                if (!ref_keep(x)) continue;
                const unsigned char *px = &rgb[3 * (y * W + x)];
                unsigned char e = ref_gray(px[0], px[1], px[2]);
                exp_sum += e;
                if (gout[k] != e) bad++;
                k++;
            }
        }
        bool ok = (bad == 0) && (hm == 0) && (pc == (unsigned)k) && (sg == exp_sum);
        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL decimation_placement bad=%d pc=%u (exp %d) sum=%u (exp %u) hdr=%ld\n",
                   bad, pc, k, sg, exp_sum, hm);
        }
    }

    printf("  Layer 1: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 层：跨语言黄金参考（逐像素）
// -----------------------------------------------------------------------------
static int test_golden(const std::string &dir)
{
    printf("---- [Layer 2] cross-language golden reference (per-pixel) ----\n");

    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) {
        printf("  ERROR: cannot read %s/meta.txt\n", dir.c_str());
        return -1;
    }
    printf("  meta     : in %dx%d -> out %dx%d, %d frames, rgb_bytes=%d, gray_bytes=%d\n",
           m.in_width, m.in_height, m.out_width, m.out_height, m.frames,
           m.rgb_bytes, m.gray_bytes);

    if (m.gray_bytes != m.out_width * m.out_height) {
        printf("  ERROR: gray_bytes (%d) != out_width*out_height (%d)\n",
               m.gray_bytes, m.out_width * m.out_height);
        return -1;
    }

    FILE *fr = fopen((dir + "/rgb_frames.bin").c_str(), "rb");
    FILE *fg = fopen((dir + "/gray.bin").c_str(), "rb");
    if (!fr || !fg) {
        printf("  ERROR: cannot open rgb_frames.bin / gray.bin\n");
        if (fr) fclose(fr);
        if (fg) fclose(fg);
        return -1;
    }

    std::vector<unsigned char> rgb(m.rgb_bytes), gold(m.gray_bytes);
    int pass = 0, fail = 0;
    long total_bad_px = 0, total_hdr = 0;

    for (int fi = 0; fi < m.frames; fi++) {
        if (fread(rgb.data(), 1, m.rgb_bytes, fr) != (size_t)m.rgb_bytes ||
            fread(gold.data(), 1, m.gray_bytes, fg) != (size_t)m.gray_bytes) {
            printf("  ERROR: short read at frame %d\n", fi);
            fclose(fr); fclose(fg);
            return -1;
        }

        std::vector<unsigned char> gout;
        int ow, oh; unsigned pc, sg, fid; long hm = 0;
        run_ip(rgb.data(), m.in_width, m.in_height, gout, ow, oh, pc, sg, fid, hm);

        long bad = 0;
        unsigned long long exp_sum = 0;
        for (int i = 0; i < m.gray_bytes; i++) {
            if (gout[i] != gold[i]) bad++;
            exp_sum += gold[i];
        }
        bool ok = (bad == 0) && (hm == 0) && (ow == m.out_width) && (oh == m.out_height) &&
                  (pc == (unsigned)m.gray_bytes) && (sg == exp_sum);
        total_bad_px += bad;
        total_hdr += hm;

        if (ok) pass++;
        else {
            fail++;
            printf("  FAIL frame=%d bad_px=%ld hdr=%ld out=%dx%d pc=%u (exp %d) sum=%u (exp %llu)\n",
                   fi, bad, hm, ow, oh, pc, m.gray_bytes, sg, exp_sum);
        }
    }
    fclose(fr);
    fclose(fg);

    printf("  Layer 2: %d passed, %d failed  (mismatched pixels: %ld, header errors: %ld)\n",
           pass, fail, total_bad_px, total_hdr);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_rgb2gray: contract docs/interface.md (tolerance = 0) ====\n");

    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);

    if (dir.empty() || !file_exists(dir + "/gray.bin")) {
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
