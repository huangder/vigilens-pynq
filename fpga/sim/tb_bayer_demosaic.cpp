// =============================================================================
//  tb_bayer_demosaic.cpp —— bayer_demosaic 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：**尚未进入 docs/interface.md**（接口提案见 docs/19 §6，待 A/B 会签）
//
//  【第 1 层】自包含边界用例（不依赖任何外部文件，期望值手工可核）：
//     (a) 平坦场：R=G=B 常数场 -> 输出恒为 to8(常数)
//     (b) 四角钳位：只有 (0,0) 为 1023 的 4x4 -> (R,G,B) = (255,128,64)
//     (c) G 相位方向探针：Gr 的 R 取同一行左右、Gb 的 R 取同一列上下；
//         写反则 Gr.R 会从 38 变成 25 —— 平坦图**测不出**这个错误，故必须专测
//     (d) 输出流 TUSER/TLAST：帧首像素 / **行末**像素（沿用项目约定）
//  【第 2 层】跨语言黄金参考：读 data_mipi/{golden_bayer16.bin, golden_rgb.bin, meta.txt}
//     （黄金参考由 Python/NumPy 独立算出），**逐像素逐通道**比对整帧。
//
//  用法： tb_bayer_demosaic <data_dir>
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<16, 1, 1, 1> axis_pix16_t;
typedef ap_axiu<24, 1, 1, 1> axis_rgb_t;

void bayer_demosaic(hls::stream<axis_pix16_t> &bayer_in,
                    hls::stream<axis_rgb_t> &rgb_out,
                    ap_uint<16> width, ap_uint<16> height,
                    ap_uint<32> &pixel_count, ap_uint<32> &frame_id);

// ---- 与 RTL 无关的朴素参考（第 1 层用；故意写得笨，便于人工核对）------------
static unsigned char ref_to8(unsigned v10)
{
    unsigned t = (v10 + 2u) >> 2;
    return (unsigned char)(t > 255u ? 255u : t);
}

static void ref_demosaic_pixel(const unsigned short *b, int w, int h, int x, int y,
                               unsigned char out[3])
{
    const int ym = (y > 0) ? y - 1 : 0;
    const int yp = (y < h - 1) ? y + 1 : h - 1;
    const int xm = (x > 0) ? x - 1 : 0;
    const int xp = (x < w - 1) ? x + 1 : w - 1;
    const unsigned c  = b[y * w + x];
    const unsigned up = b[ym * w + x], dn = b[yp * w + x];
    const unsigned lf = b[y * w + xm],  rt = b[y * w + xp];
    const unsigned ul = b[ym * w + xm], ur = b[ym * w + xp];
    const unsigned dl = b[yp * w + xm], dr = b[yp * w + xp];

    unsigned R, G, B;
    if (!(x & 1) && !(y & 1))      { R = c; G = (up + dn + lf + rt + 2) >> 2; B = (ul + ur + dl + dr + 2) >> 2; }
    else if ((x & 1) && (y & 1))   { B = c; G = (up + dn + lf + rt + 2) >> 2; R = (ul + ur + dl + dr + 2) >> 2; }
    else if ((x & 1) && !(y & 1))  { G = c; R = (lf + rt + 1) >> 1;           B = (up + dn + 1) >> 1; }
    else                           { G = c; R = (up + dn + 1) >> 1;           B = (lf + rt + 1) >> 1; }
    out[0] = ref_to8(R); out[1] = ref_to8(G); out[2] = ref_to8(B);
}

// ---- 运行一次 IP ------------------------------------------------------------
static void run_ip(const unsigned short *bayer, int w, int h,
                   std::vector<unsigned char> &rgb_out,
                   unsigned &pixel_count, unsigned &frame_id,
                   long &hdr_mismatch)
{
    hls::stream<axis_pix16_t> in;
    hls::stream<axis_rgb_t>   out;
    hdr_mismatch = 0;
    rgb_out.assign((size_t)w * h * 3, 0);

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_pix16_t p;
            p.data = (ap_uint<16>)bayer[(size_t)y * w + x];
            p.keep = 0x3; p.strb = 0x3;
            p.user = (y == 0 && x == 0) ? 1 : 0;
            p.last = (x == w - 1) ? 1 : 0;
            p.id = 0; p.dest = 0;
            in.write(p);
        }
    }

    ap_uint<32> pc, fid;
    bayer_demosaic(in, out, (ap_uint<16>)w, (ap_uint<16>)h, pc, fid);
    pixel_count = (unsigned)pc;
    frame_id = (unsigned)fid;

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_rgb_t q = out.read();
            const size_t o = 3u * (size_t)(y * w + x);
            rgb_out[o + 0] = (unsigned char)(q.data(7, 0));    // R 在 byte0
            rgb_out[o + 1] = (unsigned char)(q.data(15, 8));   // G
            rgb_out[o + 2] = (unsigned char)(q.data(23, 16));  // B

            unsigned exp_user = (y == 0 && x == 0) ? 1u : 0u;
            unsigned exp_last = (x == w - 1) ? 1u : 0u;
            bool ok = ((unsigned)q.user == exp_user) && ((unsigned)q.last == exp_last) &&
                      ((unsigned)q.keep == 7u) && ((unsigned)q.strb == 7u);
            if (!ok) hdr_mismatch++;
        }
    }
}

// ---- meta.txt ---------------------------------------------------------------
struct Meta { int w, h, frames, bayer16_bytes, rgb_bytes; };

static bool read_meta(const std::string &path, Meta &m)
{
    m.w = m.h = m.frames = m.bayer16_bytes = m.rgb_bytes = 0;
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        int v = atoi(eq + 1);
        if      (!strcmp(line, "in_width"))      m.w = v;
        else if (!strcmp(line, "in_height"))     m.h = v;
        else if (!strcmp(line, "frames"))        m.frames = v;
        else if (!strcmp(line, "bayer16_bytes")) m.bayer16_bytes = v;
        else if (!strcmp(line, "rgb_bytes"))     m.rgb_bytes = v;
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

    // (a) 平坦场：R=G=B 常数 -> 三通道全等于 to8(常数)
    {
        int bad = 0;
        const unsigned vals[3] = {0u, 511u, 1023u};
        for (int i = 0; i < 3; i++) {
            std::vector<unsigned short> f(2 * 2, (unsigned short)vals[i]);
            std::vector<unsigned char> o;
            unsigned pc, fid; long hm = 0;
            run_ip(f.data(), 2, 2, o, pc, fid, hm);
            const unsigned char e = ref_to8(vals[i]);
            for (int k = 0; k < 12; k++) if (o[k] != e) bad++;
            if (hm != 0) bad++;
        }
        printf("  %s flat fields (0/511/1023) all channels == to8(v), bad=%d\n", bad ? "FAIL" : "OK  ", bad);
        bad ? fail++ : pass++;
    }

    // (b) 四角钳位：4x4，只有 (0,0)=1023
    //     R = 1023 -> 255
    //     G = (上 1023 + 下 0 + 左 1023 + 右 0 + 2)>>2 = 512 -> 128
    //     B = (左上 1023 + 右上 0 + 左下 0 + 右下 0 + 2)>>2 = 256 -> 64
    //     ⚠️ 只钳位**四个对角里真正落在边界上的那一个**，别把四个都当成自身。
    {
        std::vector<unsigned short> f(4 * 4, 0);
        f[0] = 1023;
        std::vector<unsigned char> o;
        unsigned pc, fid; long hm = 0;
        run_ip(f.data(), 4, 4, o, pc, fid, hm);
        bool ok = (o[0] == ref_to8(1023) && o[1] == ref_to8(512) && o[2] == ref_to8(256) && hm == 0);
        printf("  %s corner clamp: (0,0) -> R=%u G=%u B=%u (expect %u,%u,%u) hdr=%ld\n",
               ok ? "OK  " : "FAIL", o[0], o[1], o[2], ref_to8(1023), ref_to8(512), ref_to8(256), hm);
        ok ? pass++ : fail++;
    }

    // (c) G 相位方向探针（内部像素，无边界干扰）
    //     R 位置 = 100*(x/2)（只随列变化）；B 位置 = 100*((y-1)/2)（只随行变化）；G 位置 = 0
    //     (3,2) Gr：R = (行2x2 100 + 行2x4 200 +1)>>1 = 150 -> 38 ; B = (行1x3 0 + 行3x3 100 +1)>>1 = 50 -> 13
    //     (2,3) Gb：R = (行2x2 100 + 行4x2 100 +1)>>1 = 100 -> 25 ; B = (行3x1 100 + 行3x3 100 +1)>>1 = 100 -> 25
    {
        const int W = 6, H = 6;
        std::vector<unsigned short> f((size_t)W * H, 0);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                if (!(x & 1) && !(y & 1))    f[y * W + x] = (unsigned short)(100 * (x / 2));
                else if ((x & 1) && (y & 1)) f[y * W + x] = (unsigned short)(100 * ((y - 1) / 2));
            }
        }
        std::vector<unsigned char> o;
        unsigned pc, fid; long hm = 0;
        run_ip(f.data(), W, H, o, pc, fid, hm);

        // 同时与朴素参考全帧对拍（第 1 层内的自洽检查）
        int ref_bad = 0;
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                unsigned char e[3];
                ref_demosaic_pixel(f.data(), W, H, x, y, e);
                const size_t off = 3u * (size_t)(y * W + x);
                for (int k = 0; k < 3; k++) if (o[off + k] != e[k]) ref_bad++;
            }
        }

        const size_t gr = 3u * (size_t)(2 * W + 3);
        const size_t gb = 3u * (size_t)(3 * W + 2);
        bool ok = (o[gr + 0] == ref_to8(150) && o[gr + 2] == ref_to8(50) &&
                   o[gb + 0] == ref_to8(100) && o[gb + 2] == ref_to8(100)) &&
                  (ref_bad == 0) && (hm == 0) && (pc == (unsigned)(W * H));
        printf("  %s G direction probe: Gr(3,2).R=%u(exp %u) .B=%u(exp %u) | Gb(2,3).R=%u(exp %u) .B=%u(exp %u) ref_bad=%d\n",
               ok ? "OK  " : "FAIL",
               o[gr + 0], ref_to8(150), o[gr + 2], ref_to8(50),
               o[gb + 0], ref_to8(100), o[gb + 2], ref_to8(100), ref_bad);
        printf("       (if the two G phases were swapped, Gr.R would read %u instead of %u)\n",
               ref_to8(100), ref_to8(150));
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
    printf("---- [Layer 2] cross-language golden reference (per-pixel) ----\n");
    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) { printf("  ERROR: cannot read meta.txt\n"); return -1; }
    if (m.bayer16_bytes != m.w * m.h * 2 || m.rgb_bytes != m.w * m.h * 3) {
        printf("  ERROR: meta sizes inconsistent (bayer16=%d rgb=%d for %dx%d)\n",
               m.bayer16_bytes, m.rgb_bytes, m.w, m.h);
        return -1;
    }
    printf("  meta: %dx%d, %d frames, bayer16=%d B, rgb=%d B\n",
           m.w, m.h, m.frames, m.bayer16_bytes, m.rgb_bytes);

    FILE *fb = fopen((dir + "/golden_bayer16.bin").c_str(), "rb");
    FILE *fg = fopen((dir + "/golden_rgb.bin").c_str(), "rb");
    if (!fb || !fg) {
        printf("  ERROR: cannot open golden_bayer16.bin / golden_rgb.bin\n");
        if (fb) fclose(fb);
        if (fg) fclose(fg);
        return -1;
    }

    std::vector<unsigned char> bb(m.bayer16_bytes), gold(m.rgb_bytes);
    std::vector<unsigned short> bayer((size_t)m.w * m.h);
    int pass = 0, fail = 0;
    long total_bad = 0, total_hdr = 0;

    for (int fi = 0; fi < m.frames; fi++) {
        if (fread(bb.data(), 1, m.bayer16_bytes, fb) != (size_t)m.bayer16_bytes ||
            fread(gold.data(), 1, m.rgb_bytes, fg) != (size_t)m.rgb_bytes) {
            printf("  ERROR: short read at frame %d\n", fi);
            fclose(fb); fclose(fg);
            return -1;
        }
        for (size_t i = 0; i < bayer.size(); i++)
            bayer[i] = (unsigned short)(bb[2 * i] | (bb[2 * i + 1] << 8));

        std::vector<unsigned char> got;
        unsigned pc, fid; long hm = 0;
        run_ip(bayer.data(), m.w, m.h, got, pc, fid, hm);

        long bad = 0;
        for (size_t i = 0; i < got.size(); i++) if (got[i] != gold[i]) bad++;
        total_bad += bad;
        total_hdr += hm;
        bool ok = (bad == 0) && (hm == 0) && (pc == (unsigned)(m.w * m.h));
        if (ok) pass++; else { fail++; printf("  FAIL frame=%d bad_bytes=%ld hdr=%ld pc=%u\n", fi, bad, hm, pc); }
    }
    fclose(fb); fclose(fg);
    printf("  Layer 2: %d passed, %d failed (mismatched bytes: %ld, header errors: %ld)\n",
           pass, fail, total_bad, total_hdr);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_bayer_demosaic: contract proposal docs/19 §6 (tolerance = 0) ====\n");
    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);
    if (dir.empty() || !file_exists(dir + "/golden_rgb.bin")) {
        if (forced) {
            printf("\n---- [Layer 2] GOLDEN: FAILED (data dir not usable: '%s') ----\n", dir.c_str());
            printf("  hint: run  python fpga/sim/gen_mipi_vectors.py  first.\n");
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
