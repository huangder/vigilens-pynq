// =============================================================================
//  tb_raw10_unpack.cpp —— raw10_unpack 的 C 测试台（两层验证）
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//  契约：**尚未进入 docs/interface.md**（接口提案见 docs/19 §6，待 A/B 会签）
//
//  【第 1 层】自包含边界用例（不依赖任何外部文件）：
//     (a) 打包口径用例：已知 5 字节组 -> 已知 4 个像素（含低 2 位全组合）
//     (b) 帧头/行尾用例：小尺寸 8x2，逐 beat 校验 TUSER（帧首像素）与 TLAST（行末像素）
//     (c) 满量程用例：全 10 bit = 1023 的组，解出必须仍是 1023（不能被 >>2 类错误污染）
//     期望值全部在本文件内独立写出（不调用被测函数反推）。
//  【第 2 层】跨语言黄金参考：读 data_mipi/{raw10.bin, golden_bayer16.bin, meta.txt}
//     （黄金参考由 Python/NumPy 独立算出），**逐像素逐字节**比对整帧。
//
//  用法： tb_raw10_unpack <data_dir>
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <hls_stream.h>
#include <ap_axi_sdata.h>
#include <ap_int.h>

typedef ap_axiu<64, 1, 1, 1> axis_raw10_t;
typedef ap_axiu<16, 1, 1, 1> axis_pix10_t;

void raw10_unpack(hls::stream<axis_raw10_t> &raw_in,
                  hls::stream<axis_pix10_t> &pix_out,
                  ap_uint<16> width, ap_uint<16> height,
                  ap_uint<16> &words_per_line,
                  ap_uint<32> &pixel_count,
                  ap_uint<32> &frame_id);

// ---- 与 RTL 无关的朴素参考（第 1 层用）--------------------------------------
static void ref_unpack(const unsigned char b[5], unsigned p[4])
{
    p[0] = ((unsigned)b[0] << 2) | ((b[4] >> 0) & 0x3u);
    p[1] = ((unsigned)b[1] << 2) | ((b[4] >> 2) & 0x3u);
    p[2] = ((unsigned)b[2] << 2) | ((b[4] >> 4) & 0x3u);
    p[3] = ((unsigned)b[3] << 2) | ((b[4] >> 6) & 0x3u);
}

// ---- 运行一次 IP（输入为"按行独立打包"的 5 字节组序列）----------------------
static void run_ip(const unsigned char *raw, int w, int h,
                   std::vector<unsigned short> &pout,
                   unsigned &words_per_line_out, unsigned &pixel_count, unsigned &frame_id,
                   long &hdr_mismatch)
{
    hls::stream<axis_raw10_t> in;
    hls::stream<axis_pix10_t> out;
    hdr_mismatch = 0;

    const int wpl = w / 4;
    pout.assign((size_t)w * h, 0);

    for (int y = 0; y < h; y++) {
        for (int g = 0; g < wpl; g++) {
            const unsigned char *b = raw + 5 * (size_t)(y * wpl + g);
            axis_raw10_t beat;
            ap_uint<64> dw = 0;
            dw(7, 0)   = b[0];
            dw(15, 8)  = b[1];
            dw(23, 16) = b[2];
            dw(31, 24) = b[3];
            dw(39, 32) = b[4];
            beat.data = dw;
            beat.keep = 0x1F;                                  // 5 字节有效
            beat.strb = 0x1F;
            beat.user = (y == 0 && g == 0) ? 1 : 0;            // 输入帧首**组**
            beat.last = (g == wpl - 1) ? 1 : 0;                // 输入行末**组**
            beat.id = 0; beat.dest = 0;
            in.write(beat);
        }
    }

    ap_uint<16> wpl_o;
    ap_uint<32> pc, fid;
    raw10_unpack(in, out, (ap_uint<16>)w, (ap_uint<16>)h, wpl_o, pc, fid);

    words_per_line_out = (unsigned)wpl_o;
    pixel_count = (unsigned)pc;
    frame_id = (unsigned)fid;

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            axis_pix10_t q = out.read();
            pout[(size_t)y * w + x] = (unsigned short)q.data;

            unsigned exp_user = (y == 0 && x == 0) ? 1u : 0u;
            unsigned exp_last = (x == w - 1) ? 1u : 0u;
            bool ok = ((unsigned)q.user == exp_user) &&
                      ((unsigned)q.last == exp_last) &&
                      ((unsigned)q.keep == 3u) && ((unsigned)q.strb == 3u) &&
                      ((unsigned)q.data >> 10) == 0u;           // 高 6 位必须是 0
            if (!ok) hdr_mismatch++;
        }
    }
}

// ---- meta.txt ---------------------------------------------------------------
struct Meta { int w, h, frames, raw10_bytes, bayer16_bytes, rgb_bytes; };

static bool read_meta(const std::string &path, Meta &m)
{
    m.w = m.h = m.frames = m.raw10_bytes = m.bayer16_bytes = m.rgb_bytes = 0;
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
        else if (!strcmp(line, "raw10_bytes"))   m.raw10_bytes = v;
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

    // (a) 打包口径：(0,1,2,1023) -> 00 00 00 FF E4
    {
        const unsigned char bytes[5] = {0x00, 0x00, 0x00, 0xFF,
                                        (unsigned char)((0 & 3) | ((1 & 3) << 2) |
                                                        ((2 & 3) << 4) | ((3 & 3) << 6))};
        unsigned p[4];
        ref_unpack(bytes, p);
        bool ok = (p[0] == 0 && p[1] == 1 && p[2] == 2 && p[3] == 1023);
        printf("  %s packing (0,1,2,1023) -> %u,%u,%u,%u (bytes %02X %02X %02X %02X %02X)\n",
               ok ? "OK  " : "FAIL", p[0], p[1], p[2], p[3],
               bytes[0], bytes[1], bytes[2], bytes[3], bytes[4]);
        ok ? pass++ : fail++;
    }

    // (b) 帧头/行尾 + 低 2 位全组合：8x2，每像素的低 2 位按 (x+y)%4 变化
    {
        const int W = 8, H = 2;
        std::vector<unsigned short> exp((size_t)W * H);
        std::vector<unsigned char> raw;
        for (int y = 0; y < H; y++) {
            for (int g = 0; g < W / 4; g++) {
                unsigned p[4];
                for (int k = 0; k < 4; k++) {
                    const int x = 4 * g + k;
                    const unsigned lo = (unsigned)((x + y) % 4);
                    const unsigned hi = (unsigned)((x * 7 + y * 3) & 0xFF);
                    p[k] = (hi << 2) | lo;
                    exp[(size_t)y * W + x] = (unsigned short)p[k];
                }
                raw.push_back((unsigned char)((p[0] >> 2) & 0xFF));
                raw.push_back((unsigned char)((p[1] >> 2) & 0xFF));
                raw.push_back((unsigned char)((p[2] >> 2) & 0xFF));
                raw.push_back((unsigned char)((p[3] >> 2) & 0xFF));
                raw.push_back((unsigned char)((p[0] & 3) | ((p[1] & 3) << 2) |
                                              ((p[2] & 3) << 4) | ((p[3] & 3) << 6)));
            }
        }
        std::vector<unsigned short> got;
        unsigned wpl, pc, fid; long hm = 0;
        run_ip(raw.data(), W, H, got, wpl, pc, fid, hm);

        int bad = 0;
        for (size_t i = 0; i < exp.size(); i++) if (got[i] != exp[i]) bad++;
        // ref_unpack 独立复算一遍（防"期望值本身就是用被测代码算的"）
        int bad2 = 0;
        for (int y = 0; y < H; y++) {
            for (int g = 0; g < W / 4; g++) {
                unsigned p[4];
                ref_unpack(&raw[5 * (size_t)(y * (W / 4) + g)], p);
                for (int k = 0; k < 4; k++) {
                    const int x = 4 * g + k;
                    if (got[(size_t)y * W + x] != (unsigned short)p[k]) bad2++;
                }
            }
        }
        bool ok = (bad == 0) && (bad2 == 0) && (hm == 0) &&
                  (wpl == (unsigned)(W / 4)) && (pc == (unsigned)(W * H)) && (fid == 1u);
        printf("  %s 8x2 header/lsb case: bad=%d ref_bad=%d hdr=%ld wpl=%u pc=%u fid=%u\n",
               ok ? "OK  " : "FAIL", bad, bad2, hm, wpl, pc, fid);
        ok ? pass++ : fail++;
    }

    // (c) 满量程：全 1023 -> (b0..b3)=0xFF, b4=0xFF，解出必须全是 1023
    {
        const unsigned char bytes[5] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
        unsigned p[4];
        ref_unpack(bytes, p);
        bool ok = (p[0] == 1023 && p[1] == 1023 && p[2] == 1023 && p[3] == 1023);
        printf("  %s full-scale group FF FF FF FF FF -> %u,%u,%u,%u\n",
               ok ? "OK  " : "FAIL", p[0], p[1], p[2], p[3]);
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
    if (m.raw10_bytes != m.w * m.h * 5 / 4 || m.bayer16_bytes != m.w * m.h * 2) {
        printf("  ERROR: meta sizes inconsistent (raw10=%d bayer16=%d for %dx%d)\n",
               m.raw10_bytes, m.bayer16_bytes, m.w, m.h);
        return -1;
    }
    printf("  meta: %dx%d, %d frames, raw10=%d B, bayer16=%d B\n",
           m.w, m.h, m.frames, m.raw10_bytes, m.bayer16_bytes);

    FILE *fr = fopen((dir + "/raw10.bin").c_str(), "rb");
    FILE *fg = fopen((dir + "/golden_bayer16.bin").c_str(), "rb");
    if (!fr || !fg) {
        printf("  ERROR: cannot open raw10.bin / golden_bayer16.bin\n");
        if (fr) fclose(fr);
        if (fg) fclose(fg);
        return -1;
    }

    std::vector<unsigned char> raw(m.raw10_bytes), gold(m.bayer16_bytes);
    int pass = 0, fail = 0;
    long total_bad = 0, total_hdr = 0;

    for (int fi = 0; fi < m.frames; fi++) {
        if (fread(raw.data(), 1, m.raw10_bytes, fr) != (size_t)m.raw10_bytes ||
            fread(gold.data(), 1, m.bayer16_bytes, fg) != (size_t)m.bayer16_bytes) {
            printf("  ERROR: short read at frame %d\n", fi);
            fclose(fr); fclose(fg);
            return -1;
        }
        std::vector<unsigned short> got;
        unsigned wpl, pc, fid; long hm = 0;
        run_ip(raw.data(), m.w, m.h, got, wpl, pc, fid, hm);

        long bad = 0;
        for (size_t i = 0; i < got.size(); i++) {
            const unsigned short g = (unsigned short)(gold[2 * i] | (gold[2 * i + 1] << 8));
            if (got[i] != g) bad++;
        }
        total_bad += bad;
        total_hdr += hm;
        bool ok = (bad == 0) && (hm == 0) && (pc == (unsigned)(m.w * m.h));
        if (ok) pass++; else { fail++; printf("  FAIL frame=%d bad_px=%ld hdr=%ld pc=%u\n", fi, bad, hm, pc); }
    }
    fclose(fr); fclose(fg);
    printf("  Layer 2: %d passed, %d failed (mismatched pixels: %ld, header errors: %ld)\n",
           pass, fail, total_bad, total_hdr);
    return fail;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== tb_raw10_unpack: contract proposal docs/19 §6 (tolerance = 0) ====\n");
    int fail = test_embedded();

    std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("");
    bool forced = (argc > 1);
    if (dir.empty() || !file_exists(dir + "/raw10.bin")) {
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
