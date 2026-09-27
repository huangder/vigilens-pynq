// =============================================================================
//  tb_frame_scale.cpp —— frame_scale 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：**尚未进入 docs/interface.md**（提案见 docs/20 §5，待 A/B 会签）
//
//  【第 1 层】自包含边界用例（期望值手工可核）：
//     (a) 反向映射序列：ox=0..7 必须映射到组内 0,1,3,4,6,7,9,10
//     (b) **被丢弃的行/列绝不许出现**：把 k%3==2 的列与 y%3==2 的行全染白，
//         输出里必须一个白点都没有（这是"抽错相位"最直接的探测器）
//     (c) 冲激落在保留位必须原样出现在正确的输出坐标
//     (d) 两轴同为 3:2 ⇒ 无几何失真（1280x720 -> 640x480）
//     (e) 输出流 TUSER/TLAST：帧首像素 / **输出行**末像素
//  【第 2 层】跨语言黄金参考：读 data_scale/{rgb_in.bin, golden_scale.bin, meta.txt}
//     （黄金参考由 Python/NumPy 独立算出），**逐字节**比对整帧。
//
//  用法： tb_frame_scale <data_dir>
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

#define SCALE_NUM 2
#define SCALE_DEN 3

void frame_scale(hls::stream<axis_pix_t> &rgb_in,
                 hls::stream<axis_pix_t> &rgb_out,
                 ap_uint<16> width, ap_uint<16> height,
                 ap_uint<16> crop_x0, ap_uint<16> crop_w,
                 ap_uint<16> &out_width, ap_uint<16> &out_height,
                 ap_uint<32> &pixel_count, ap_uint<32> &frame_id);

// ---- 朴素参考（第 1 层用；正向扫描，故意写得笨）-----------------------------
static bool ref_keep(int x, int y, int x0, int cw)
{
    if ((y % SCALE_DEN) >= SCALE_NUM) return false;
    if (x < x0) return false;
    const int k = x - x0;
    if (k >= cw) return false;
    return ((k % SCALE_DEN) < SCALE_NUM);
}

// ---- 运行一次 IP ------------------------------------------------------------
static void run_ip(const unsigned char *rgb, int w, int h, int x0, int cw,
                   std::vector<unsigned char> &out,
                   unsigned &ow_out, unsigned &oh_out,
                   unsigned &pixel_count, unsigned &frame_id,
                   long &hdr_mismatch)
{
    hls::stream<axis_pix_t> in, os;
    hdr_mismatch = 0;
    const int ow = cw / SCALE_DEN * SCALE_NUM;
    const int oh = h  / SCALE_DEN * SCALE_NUM;
    out.assign((size_t)ow * oh * 3, 0);

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const unsigned char *p = rgb + 3 * (y * w + x);
            axis_pix_t b;
            b.data = (ap_uint<24>)(((unsigned)p[2] << 16) | ((unsigned)p[1] << 8) | p[0]);
            b.keep = 0x7; b.strb = 0x7;
            b.user = (y == 0 && x == 0) ? 1 : 0;
            b.last = (x == w - 1) ? 1 : 0;
            b.id = 0; b.dest = 0;
            in.write(b);
        }
    }

    ap_uint<16> uw, uh;
    ap_uint<32> pc, fid;
    frame_scale(in, os, (ap_uint<16>)w, (ap_uint<16>)h,
                (ap_uint<16>)x0, (ap_uint<16>)cw, uw, uh, pc, fid);

    ow_out = (unsigned)uw;
    oh_out = (unsigned)uh;
    pixel_count = (unsigned)pc;
    frame_id = (unsigned)fid;

    for (int oy = 0; oy < oh; oy++) {
        for (int ox = 0; ox < ow; ox++) {
            axis_pix_t q = os.read();
            const size_t o = 3u * (size_t)(oy * ow + ox);
            out[o + 0] = (unsigned char)(q.data(7, 0));
            out[o + 1] = (unsigned char)(q.data(15, 8));
            out[o + 2] = (unsigned char)(q.data(23, 16));

            unsigned exp_user = (oy == 0 && ox == 0) ? 1u : 0u;
            unsigned exp_last = (ox == ow - 1) ? 1u : 0u;
            bool ok = ((unsigned)q.user == exp_user) && ((unsigned)q.last == exp_last) &&
                      ((unsigned)q.keep == 7u) && ((unsigned)q.strb == 7u);
            if (!ok) hdr_mismatch++;
        }
    }
}

// ---- meta.txt ---------------------------------------------------------------
struct Meta { int w, h, x0, cw, ow, oh, frames, in_bytes, out_bytes; };

static bool read_meta(const std::string &path, Meta &m)
{
    m.w = m.h = m.x0 = m.cw = m.ow = m.oh = m.frames = m.in_bytes = m.out_bytes = 0;
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        int v = atoi(eq + 1);
        if      (!strcmp(line, "in_width"))     m.w = v;
        else if (!strcmp(line, "in_height"))    m.h = v;
        else if (!strcmp(line, "crop_x0"))      m.x0 = v;
        else if (!strcmp(line, "crop_w"))       m.cw = v;
        else if (!strcmp(line, "out_width"))    m.ow = v;
        else if (!strcmp(line, "out_height"))   m.oh = v;
        else if (!strcmp(line, "frames"))       m.frames = v;
        else if (!strcmp(line, "rgb_in_bytes")) m.in_bytes = v;
        else if (!strcmp(line, "rgb_out_bytes"))m.out_bytes = v;
    }
    fclose(f);
    return (m.w > 0 && m.h > 0 && m.frames > 0);
}

static bool file_exists(const std::string &p) { FILE *f = fopen(p.c_str(), "rb"); if (!f) return false; fclose(f); return true; }

// -----------------------------------------------------------------------------
// 第 1 层
// -----------------------------------------------------------------------------
static int test_embedded(void)
{
    printf("---- [Layer 1] embedded cases ----\n");
    int pass = 0, fail = 0;

    // (a)+(c)+(e) 用 12x9 输入、crop_x0=3、crop_w=6 -> 输出 4x6
    {
        const int W = 12, H = 9, X0 = 3, CW = 6;
        const int OW = 4, OH = 6;
        std::vector<unsigned char> in((size_t)W * H * 3, 0);
        int k = 0;
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                unsigned char *p = &in[3 * (y * W + x)];
                p[0] = (unsigned char)(++k & 0xFF);
                p[1] = (unsigned char)((k * 7) & 0xFF);
                p[2] = (unsigned char)((k * 13) & 0xFF);
            }
        }
        std::vector<unsigned char> out;
        unsigned ow, oh, pc, fid; long hm = 0;
        run_ip(in.data(), W, H, X0, CW, out, ow, oh, pc, fid, hm);

        int bad = 0;
        int exp_k = 0;
        // 与朴素参考（正向）逐点比
        std::vector<unsigned char> ref;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++)
                if (ref_keep(x, y, X0, CW))
                    for (int c = 0; c < 3; c++) ref.push_back(in[3 * (y * W + x) + c]);

        for (size_t i = 0; i < out.size(); i++) if (out[i] != ref[i]) bad++;
        bool ok = (bad == 0) && (hm == 0) && (ow == (unsigned)OW) && (oh == (unsigned)OH) &&
                  (pc == (unsigned)(OW * OH)) && (fid == 1u) && (ref.size() == out.size());
        printf("  %s 12x9 crop(3,6) -> %ux%u : bad=%d hdr=%ld pc=%u exp_size=%zu\n",
               ok ? "OK  " : "FAIL", ow, oh, bad, hm, pc, ref.size());
        (void)exp_k;
        ok ? pass++ : fail++;
    }

    // (b) 被丢弃的行/列绝不许出现
    {
        const int W = 12, H = 9, X0 = 3, CW = 6;
        std::vector<unsigned char> in((size_t)W * H * 3, 0);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                const bool dropped = ((x >= X0) && ((x - X0) % SCALE_DEN == 2)) ||
                                     ((y % SCALE_DEN) == 2);
                if (dropped) {
                    unsigned char *p = &in[3 * (y * W + x)];
                    p[0] = p[1] = p[2] = 255;
                }
            }
        }
        std::vector<unsigned char> out;
        unsigned ow, oh, pc, fid; long hm = 0;
        run_ip(in.data(), W, H, X0, CW, out, ow, oh, pc, fid, hm);
        int whites = 0;
        for (size_t i = 0; i < out.size(); i += 3)
            if (out[i] == 255 && out[i + 1] == 255 && out[i + 2] == 255) whites++;
        bool ok = (whites == 0) && (hm == 0);
        printf("  %s dropped rows/cols never appear : whites=%d (expect 0)\n",
               ok ? "OK  " : "FAIL", whites);
        ok ? pass++ : fail++;
    }

    // (d) 无几何失真
    {
        std::vector<unsigned char> in((size_t)1280 * 720 * 3, 0);
        std::vector<unsigned char> out;
        unsigned ow, oh, pc, fid; long hm = 0;
        run_ip(in.data(), 1280, 720, 160, 960, out, ow, oh, pc, fid, hm);
        bool ok = (ow == 640u) && (oh == 480u) && (pc == 640u * 480u) &&
                  (960u * 3u == 640u * 2u * 2u) && (720u * 3u == 480u * 2u * 2u) && (hm == 0);
        printf("  %s aspect check 1280x720 crop(160,960) -> %ux%u (pc=%u) hdr=%ld\n",
               ok ? "OK  " : "FAIL", ow, oh, pc, hm);
        ok ? pass++ : fail++;
    }

    printf("  Layer 1: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 层
// -----------------------------------------------------------------------------
static int test_golden(const std::string &dir)
{
    printf("---- [Layer 2] cross-language golden reference (per-byte) ----\n");
    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) { printf("  ERROR: cannot read meta.txt\n"); return -1; }
    if (m.in_bytes != m.w * m.h * 3 || m.out_bytes != m.ow * m.oh * 3) {
        printf("  ERROR: meta sizes inconsistent (in=%d out=%d for %dx%d -> %dx%d)\n",
               m.in_bytes, m.out_bytes, m.w, m.h, m.ow, m.oh);
        return -1;
    }
    printf("  meta: in %dx%d crop(%d,%d) -> out %dx%d, %d frames\n",
           m.w, m.h, m.x0, m.cw, m.ow, m.oh, m.frames);

    FILE *fi = fopen((dir + "/rgb_in.bin").c_str(), "rb");
    FILE *fg = fopen((dir + "/golden_scale.bin").c_str(), "rb");
    if (!fi || !fg) {
        printf("  ERROR: cannot open rgb_in.bin / golden_scale.bin\n");
        if (fi) fclose(fi);
        if (fg) fclose(fg);
        return -1;
    }

    std::vector<unsigned char> in(m.in_bytes), gold(m.out_bytes);
    int pass = 0, fail = 0;
    long total_bad = 0, total_hdr = 0;

    for (int fi2 = 0; fi2 < m.frames; fi2++) {
        if (fread(in.data(), 1, m.in_bytes, fi) != (size_t)m.in_bytes ||
            fread(gold.data(), 1, m.out_bytes, fg) != (size_t)m.out_bytes) {
            printf("  ERROR: short read at frame %d\n", fi2);
            fclose(fi); fclose(fg);
            return -1;
        }
        std::vector<unsigned char> out;
        unsigned ow, oh, pc, fid; long hm = 0;
        run_ip(in.data(), m.w, m.h, m.x0, m.cw, out, ow, oh, pc, fid, hm);

        long bad = 0;
        for (size_t i = 0; i < out.size(); i++) if (out[i] != gold[i]) bad++;
        total_bad += bad;
        total_hdr += hm;
        bool ok = (bad == 0) && (hm == 0) && (ow == (unsigned)m.ow) && (oh == (unsigned)m.oh) &&
                  (pc == (unsigned)(m.ow * m.oh));
        if (ok) pass++; else { fail++; printf("  FAIL frame=%d bad_bytes=%ld hdr=%ld out=%ux%u pc=%u\n", fi2, bad, hm, ow, oh, pc); }
    }
    fclose(fi); fclose(fg);
    printf("  Layer 2: %d passed, %d failed (mismatched bytes: %ld, header errors: %ld)\n",
           pass, fail, total_bad, total_hdr);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_frame_scale: contract proposal docs/20 §5 (tolerance = 0) ====\n");
    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);
    if (dir.empty() || !file_exists(dir + "/golden_scale.bin")) {
        if (forced) {
            printf("\n---- [Layer 2] GOLDEN: FAILED (data dir not usable: '%s') ----\n", dir.c_str());
            printf("  hint: run  python fpga/sim/gen_scale_vectors.py  first.\n");
            fail += 1;
        } else {
            printf("\n---- [Layer 2] GOLDEN: SKIPPED (no data dir) ----\n");
        }
    } else {
        int f2 = test_golden(dir);
        if (f2 < 0) { fail += 1; printf("  Layer 2: ERROR\n"); } else fail += f2;
    }

    printf("\n==== RESULT: %s ====\n", (fail == 0) ? "PASS" : "FAIL");
    return (fail == 0) ? 0 : 1;
}
