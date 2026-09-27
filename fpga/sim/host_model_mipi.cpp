// =============================================================================
//  host_model_mipi.cpp —— raw10_unpack / bayer_demosaic 的**主机端算术模型**
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//
//  这是什么：把两个 IP 的内层算式**原样转写**成一个能在本机秒级运行的 C++ 程序。
//  为什么需要：本机 MinGW 是 win32 线程模型，跑不了 `hls::stream` 的 C 仿真模型
//             （链接报 0xC000039 类错误）；但**不含 hls 头**的纯算术程序可以编译运行。
//             于是"算术对不对"这件事不必等 Vitis，本机就能证。
//
//  ⚠️ **这不是 HLS 证据**：它不建模 AXI-Stream 握手、不建模 BRAM/FIFO 深度、
//     不建模时序。它只回答一件事：**算式与黄金参考是否逐位相同**。
//     真正的 csim / csynth / cosim 必须在完整权限终端用 vitis-run 跑（见 fpga/README.md）。
//
//  本文件做了四组自证：
//    1) 内嵌边界用例（人工可核对）：打包口径、to8 饱和、平坦场、四角钳位
//    2) **流式实现 vs 朴素实现**：同一套口径、两条完全不同的代码路径（滚动 3 行缓冲
//       vs 直接按全帧邻居取值），必须逐位相同 —— 这是对 IP 里"缓冲旋转"逻辑的独立检查
//    3) **跨语言黄金参考**：与 Python/NumPy 生成的 golden_bayer16.bin / golden_rgb.bin 全量逐位比对
//    4) **入库 CSV 复核**：重新读 golden_mipi.csv，逐行核对（保证入库的那份黄金参考不是孤儿文件）
//
//  用法： host_model_mipi <data_dir>
//         host_model_mipi fpga/sim/data_mipi
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>

typedef uint8_t  u8;
typedef uint16_t u16;

// ---- 冻结口径 1：10 bit -> 8 bit（四舍五入 + 饱和）--------------------------
static u8 to8(u16 v10)
{
    unsigned t = ((unsigned)v10 + 2u) >> 2;
    return (t > 255u) ? (u8)255 : (u8)t;
}

// ---- 冻结口径 2：MIPI CSI-2 RAW10 打包 -> 4 个像素 --------------------------
static void unpack_group(const u8 *b, u16 p[4])
{
    p[0] = (u16)(((u16)b[0] << 2) | ((b[4] >> 0) & 0x3));
    p[1] = (u16)(((u16)b[1] << 2) | ((b[4] >> 2) & 0x3));
    p[2] = (u16)(((u16)b[2] << 2) | ((b[4] >> 4) & 0x3));
    p[3] = (u16)(((u16)b[3] << 2) | ((b[4] >> 6) & 0x3));
}

// ---- 冻结口径 3：RGGB 双线性 + 坐标钳位 -------------------------------------
//  单像素核心：给 9 个邻居算 (R,G,B)，10 bit 输入、12 bit 中间量
static void demosaic_pixel(u16 c, u16 up, u16 dn, u16 lf, u16 rt,
                           u16 ul, u16 ur, u16 dl, u16 dr,
                           int x, int y, u8 rgb[3])
{
    const bool odd_x = (x & 1) != 0;
    const bool odd_y = (y & 1) != 0;
    u16 R, G, B;
    if (!odd_x && !odd_y) {            // R 位置
        R = c;
        G = (u16)((up + dn + lf + rt + 2) >> 2);
        B = (u16)((ul + ur + dl + dr + 2) >> 2);
    } else if (odd_x && odd_y) {       // B 位置
        B = c;
        G = (u16)((up + dn + lf + rt + 2) >> 2);
        R = (u16)((ul + ur + dl + dr + 2) >> 2);
    } else if (odd_x && !odd_y) {      // G 位置（Gr）
        G = c;
        R = (u16)((lf + rt + 1) >> 1);
        B = (u16)((up + dn + 1) >> 1);
    } else {                           // G 位置（Gb）
        G = c;
        R = (u16)((up + dn + 1) >> 1);
        B = (u16)((lf + rt + 1) >> 1);
    }
    rgb[0] = to8(R);
    rgb[1] = to8(G);
    rgb[2] = to8(B);
}

// ---- 实现 A：朴素全帧（直接下标 + 钳位）-------------------------------------
static void demosaic_naive(const u16 *bayer, int w, int h, std::vector<u8> &out)
{
    out.assign((size_t)w * h * 3, 0);
    for (int y = 0; y < h; y++) {
        const int ym = (y > 0) ? y - 1 : 0;
        const int yp = (y < h - 1) ? y + 1 : h - 1;
        for (int x = 0; x < w; x++) {
            const int xm = (x > 0) ? x - 1 : 0;
            const int xp = (x < w - 1) ? x + 1 : w - 1;
            u8 rgb[3];
            demosaic_pixel(bayer[y * w + x], bayer[ym * w + x], bayer[yp * w + x],
                           bayer[y * w + xm], bayer[y * w + xp],
                           bayer[ym * w + xm], bayer[ym * w + xp],
                           bayer[yp * w + xm], bayer[yp * w + xp],
                           x, y, rgb);
            const size_t o = 3u * (size_t)(y * w + x);
            out[o] = rgb[0]; out[o + 1] = rgb[1]; out[o + 2] = rgb[2];
        }
    }
}

// ---- 实现 B：流式 3 行滚动缓冲（**镜像 IP 的缓冲旋转逻辑**）------------------
static void demosaic_stream(const u16 *bayer, int w, int h, std::vector<u8> &out)
{
    out.assign((size_t)w * h * 3, 0);
    std::vector<u16> rb[3];
    for (int i = 0; i < 3; i++) rb[i].assign((size_t)w, 0);

    int cur = 0, prev = 2, prev2 = 1;
    for (int y = 0; y <= h; y++) {
        if (y < h) {
            for (int x = 0; x < w; x++) rb[cur][x] = bayer[y * w + x];
        }
        if (y >= 1) {
            const int ym   = y - 1;
            const int upi  = (y >= 2) ? prev2 : prev;
            const int midi = prev;
            const int dni  = (y < h) ? cur : prev;
            for (int x = 0; x < w; x++) {
                const int xm = (x == 0)     ? 0     : x - 1;
                const int xp = (x == w - 1) ? w - 1 : x + 1;
                u8 rgb[3];
                demosaic_pixel(rb[midi][x], rb[upi][x], rb[dni][x],
                               rb[midi][xm], rb[midi][xp],
                               rb[upi][xm], rb[upi][xp],
                               rb[dni][xm], rb[dni][xp],
                               x, ym, rgb);
                const size_t o = 3u * (size_t)(ym * w + x);
                out[o] = rgb[0]; out[o + 1] = rgb[1]; out[o + 2] = rgb[2];
            }
        }
        const int t = prev2; prev2 = prev; prev = cur; cur = t;
    }
}

// ---- 实现 C：流式解包（**镜像 IP 的循环结构**）------------------------------
static void unpack_stream(const u8 *raw, int w, int h, std::vector<u16> &out)
{
    out.assign((size_t)w * h, 0);
    const int wpl = w >> 2;
    size_t k = 0;
    for (int y = 0; y < h; y++) {
        for (int g = 0; g < wpl; g++) {
            u16 p[4];
            unpack_group(raw + 5u * (size_t)(y * wpl + g), p);
            for (int i = 0; i < 4; i++) out[k++] = p[i];
        }
    }
}

// ---- 工具 -------------------------------------------------------------------
static bool read_file(const std::string &path, std::vector<u8> &buf)
{
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf.assign((size_t)(n > 0 ? n : 0), 0);
    if (n > 0 && fread(buf.data(), 1, (size_t)n, f) != (size_t)n) { fclose(f); return false; }
    fclose(f);
    return true;
}

static std::string trim(const std::string &s)
{
    size_t a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    size_t b = s.find_last_not_of(" \t\r\n");
    return s.substr(a, b - a + 1);
}

struct Meta {
    int in_width = 0, in_height = 0, frames = 0;
    int raw10_bytes = 0, bayer16_bytes = 0, rgb_bytes = 0;
    std::string bayer_pattern, frame_names;
};

static bool read_meta(const std::string &path, Meta &m)
{
    FILE *f = fopen(path.c_str(), "r");
    if (!f) return false;
    char line[512];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        std::string k = trim(line), v = trim(eq + 1);
        if      (k == "in_width")      m.in_width = atoi(v.c_str());
        else if (k == "in_height")     m.in_height = atoi(v.c_str());
        else if (k == "frames")        m.frames = atoi(v.c_str());
        else if (k == "raw10_bytes")   m.raw10_bytes = atoi(v.c_str());
        else if (k == "bayer16_bytes") m.bayer16_bytes = atoi(v.c_str());
        else if (k == "rgb_bytes")     m.rgb_bytes = atoi(v.c_str());
        else if (k == "bayer_pattern") m.bayer_pattern = v;
        else if (k == "frame_names")   m.frame_names = v;
    }
    fclose(f);
    return (m.in_width > 0 && m.in_height > 0 && m.frames > 0);
}

// -----------------------------------------------------------------------------
// 第 1 组：内嵌边界用例（人工可核对）
// -----------------------------------------------------------------------------
static int test_embedded()
{
    printf("---- [1] embedded cases (hand-checkable) ----\n");
    int pass = 0, fail = 0;

    // (a) to8 饱和边界
    {
        struct C { u16 v; u8 e; } cases[] = {
            {0, 0}, {1, 0}, {2, 1}, {3, 1}, {4, 1}, {6, 2},
            {1020, 255}, {1021, 255}, {1022, 255}, {1023, 255},
        };
        int bad = 0;
        for (int i = 0; i < 10; i++) if (to8(cases[i].v) != cases[i].e) bad++;
        printf("  %s to8 saturation (%d cases, bad=%d)\n", bad ? "FAIL" : "OK  ", 10, bad);
        bad ? fail++ : pass++;
    }

    // (b) 打包口径：(0,1,2,1023) -> 00 00 00 FF E4
    {
        const u8 bytes[5] = {0x00, 0x00, 0x00, 0xFF,
                             (u8)((0 & 3) | ((1 & 3) << 2) | ((2 & 3) << 4) | ((3 & 3) << 6))};
        u16 p[4];
        unpack_group(bytes, p);
        bool ok = (p[0] == 0 && p[1] == 1 && p[2] == 2 && p[3] == 1023);
        printf("  %s RAW10 packing (0,1,2,1023) -> %u,%u,%u,%u  (bytes %02X %02X %02X %02X %02X)\n",
               ok ? "OK  " : "FAIL", p[0], p[1], p[2], p[3],
               bytes[0], bytes[1], bytes[2], bytes[3], bytes[4]);
        ok ? pass++ : fail++;
    }

    // (c) 平坦场：R=G=B 常数场 -> 输出恒为该常数的 to8
    for (int vi = 0; vi < 3; vi++) {
        const u16 v = (u16)(vi == 0 ? 0 : (vi == 1 ? 511 : 1023));
        std::vector<u16> f(4, v);
        std::vector<u8> o;
        demosaic_naive(f.data(), 2, 2, o);
        bool ok = true;
        for (int i = 0; i < 12; i++) if (o[(size_t)i] != to8(v)) ok = false;
        printf("  %s flat 2x2 field v=%4u -> all channels == %3u\n", ok ? "OK  " : "FAIL", v, to8(v));
        ok ? pass++ : fail++;
    }

    // (d) 四角钳位：4x4 RGGB，只有 (0,0) 为满量程 1023、其余为 0。逐项手算：
    //     (0,0) 是 R 位置 -> R = 1023
    //     G = (上+下+左+右 + 2)>>2，其中"上"钳到行 0、"左"钳到列 0（都=1023）：
    //         = (1023 + 0 + 1023 + 0 + 2)>>2 = 512  -> to8 = 128
    //     B = (四对角 + 2)>>2，只有**左上**钳位到自身(1023)，右上/左下/右下都是 0：
    //         = (1023 + 0 + 0 + 0 + 2)>>2 = 256     -> to8(256) = 64
    //     ⚠️ 这条用例曾把 B 误写成 1023（以为四对角全钳到自身）—— 是**用例写错**，
    //        跑主机模型时被抓住；这正是本文件的用途。口径本身没变。
    {
        std::vector<u16> f(16, 0);
        f[0] = 1023;
        std::vector<u8> o;
        demosaic_naive(f.data(), 4, 4, o);
        const u8 r = o[0], g = o[1], b = o[2];
        bool ok = (r == to8(1023) && g == to8(512) && b == to8(256));
        printf("  %s corner clamp (4x4, only (0,0)=1023) -> R=%u G=%u B=%u (expect %u,%u,%u)\n",
               ok ? "OK  " : "FAIL", r, g, b, to8(1023), to8(512), to8(256));
        ok ? pass++ : fail++;
    }

    // (e) 两种 G 相位必须朝**相反方向**取 R/B：Gr 的 R 取自同一行的左右、B 取自同一列的上下；
    //     Gb 的 R 取自同一列的上下、B 取自同一行的左右。这是 demosaic 最容易写反、
    //     且在平坦图上完全测不出来的地方。用一个"方向探针"场来钉住它：
    //       R 位置 = 100*(x/2)   （只随列变化 -> 同一列的上下必然相等）
    //       B 位置 = 100*((y-1)/2)（只随行变化 -> 同一行的左右必然相等）
    //       G 位置 = 0
    //     取两个**内部**像素避免边界钳位干扰（x=2..3, y=2..3）：
    //       (3,2) Gr：R = (行2的x=2 100 + 行2的x=4 200 +1)>>1 = 150 -> 38
    //                 B = (行1的x=3   0 + 行3的x=3 100 +1)>>1 =  50 -> 13
    //       (2,3) Gb：R = (行2的x=2 100 + 行4的x=2 100 +1)>>1 = 100 -> 25
    //                 B = (行3的x=1 100 + 行3的x=3 100 +1)>>1 = 100 -> 25
    //     ⇒ 若把两种 G 相位的方向写反，Gr 的 R 会变成 25（而不是 38）—— 立刻被抓住。
    {
        const int W = 6, H = 6;
        std::vector<u16> f((size_t)W * H, 0);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                if (!(x & 1) && !(y & 1))      f[y * W + x] = (u16)(100 * (x / 2));        // R 位置
                else if ((x & 1) && (y & 1))   f[y * W + x] = (u16)(100 * ((y - 1) / 2));  // B 位置
                // G 位置保持 0
            }
        }
        std::vector<u8> o;
        demosaic_naive(f.data(), W, H, o);
        const u8 gr_r = o[3 * (2 * W + 3) + 0];
        const u8 gr_g = o[3 * (2 * W + 3) + 1];
        const u8 gr_b = o[3 * (2 * W + 3) + 2];
        const u8 gb_r = o[3 * (3 * W + 2) + 0];
        const u8 gb_g = o[3 * (3 * W + 2) + 1];
        const u8 gb_b = o[3 * (3 * W + 2) + 2];
        bool ok = (gr_r == to8(150) && gr_g == to8(0) && gr_b == to8(50) &&
                   gb_r == to8(100) && gb_g == to8(0) && gb_b == to8(100));
        printf("  %s G phase direction probe: Gr(3,2)=(%u,%u,%u) exp (%u,%u,%u) | Gb(2,3)=(%u,%u,%u) exp (%u,%u,%u)\n",
               ok ? "OK  " : "FAIL",
               gr_r, gr_g, gr_b, to8(150), to8(0), to8(50),
               gb_r, gb_g, gb_b, to8(100), to8(0), to8(100));
        printf("       (if the two G phases were swapped, Gr.R would read %u instead of %u)\n",
               to8(100), to8(150));
        ok ? pass++ : fail++;
    }

    printf("  embedded: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 组：流式 vs 朴素（同一口径、两条代码路径）
// -----------------------------------------------------------------------------
static int test_stream_vs_naive(int w, int h, int seed)
{
    printf("---- [2] stream (rolling 3-row) vs naive (full-frame) ----\n");
    std::vector<u16> f((size_t)w * h);
    unsigned s = (unsigned)seed;
    for (size_t i = 0; i < f.size(); i++) {
        s = s * 1664525u + 1013904223u;
        f[i] = (u16)((s >> 13) & 0x3FF);
    }
    std::vector<u8> a, b;
    demosaic_naive(f.data(), w, h, a);
    demosaic_stream(f.data(), w, h, b);

    size_t bad = 0;
    for (size_t i = 0; i < a.size(); i++) if (a[i] != b[i]) bad++;
    printf("  %s %dx%d random field: %zu bytes compared, mismatches=%zu\n",
           bad ? "FAIL" : "OK  ", w, h, a.size(), bad);
    if (bad) {
        for (size_t i = 0; i < a.size() && i < a.size(); i++) {
            if (a[i] != b[i]) {
                size_t px = i / 3;
                printf("       first mismatch at byte %zu (x=%zu y=%zu ch=%zu): naive=%u stream=%u\n",
                       i, px % (size_t)w, px / (size_t)w, i % 3, a[i], b[i]);
                break;
            }
        }
        return 1;
    }
    return 0;
}

// -----------------------------------------------------------------------------
// 第 3 组：跨语言黄金参考（Python/NumPy 产物）全量逐位比对
// -----------------------------------------------------------------------------
static int test_golden(const std::string &dir, const Meta &m)
{
    printf("---- [3] cross-language golden reference (Python/NumPy) ----\n");
    std::vector<u8> raw, gb, gr;
    if (!read_file(dir + "/raw10.bin", raw) ||
        !read_file(dir + "/golden_bayer16.bin", gb) ||
        !read_file(dir + "/golden_rgb.bin", gr)) {
        printf("  ERROR: missing .bin vectors in %s (run gen_mipi_vectors.py)\n", dir.c_str());
        return -1;
    }
    const size_t exp_raw = (size_t)m.frames * (size_t)m.raw10_bytes;
    const size_t exp_gb  = (size_t)m.frames * (size_t)m.bayer16_bytes;
    const size_t exp_gr  = (size_t)m.frames * (size_t)m.rgb_bytes;
    if (raw.size() != exp_raw || gb.size() != exp_gb || gr.size() != exp_gr) {
        printf("  ERROR: size mismatch raw=%zu/%zu bayer16=%zu/%zu rgb=%zu/%zu\n",
               raw.size(), exp_raw, gb.size(), exp_gb, gr.size(), exp_gr);
        return -1;
    }
    printf("  meta: %dx%d, %d frames, raw10=%d B, bayer16=%d B, rgb=%d B, pattern=%s\n",
           m.in_width, m.in_height, m.frames, m.raw10_bytes, m.bayer16_bytes, m.rgb_bytes,
           m.bayer_pattern.c_str());

    const int w = m.in_width, h = m.in_height;
    int fail = 0;
    size_t bad_unpack = 0, bad_naive = 0, bad_stream = 0;

    for (int fi = 0; fi < m.frames; fi++) {
        // --- 解包 ---
        std::vector<u16> up;
        unpack_stream(raw.data() + (size_t)fi * m.raw10_bytes, w, h, up);
        const u8 *gbp = gb.data() + (size_t)fi * m.bayer16_bytes;
        size_t bad = 0;
        for (size_t i = 0; i < up.size(); i++) {
            const u16 gold = (u16)(gbp[2 * i] | (gbp[2 * i + 1] << 8));
            if (up[i] != gold) bad++;
        }
        bad_unpack += bad;

        // --- 去马赛克（两条路径都与黄金参考比）---
        std::vector<u8> na, st;
        demosaic_naive(up.data(), w, h, na);
        demosaic_stream(up.data(), w, h, st);
        const u8 *grp = gr.data() + (size_t)fi * m.rgb_bytes;
        size_t bn = 0, bs = 0;
        for (size_t i = 0; i < na.size(); i++) {
            if (na[i] != grp[i]) bn++;
            if (st[i] != grp[i]) bs++;
        }
        bad_naive += bn;
        bad_stream += bs;

        bool ok = (bad == 0 && bn == 0 && bs == 0);
        if (!ok) fail++;
        printf("  %s frame %d: unpack_bad=%zu naive_bad=%zu stream_bad=%zu\n",
               ok ? "OK  " : "FAIL", fi, bad, bn, bs);
    }

    printf("  totals: unpack mismatches=%zu, naive mismatches=%zu, stream mismatches=%zu\n",
           bad_unpack, bad_naive, bad_stream);
    if (bad_unpack || bad_naive || bad_stream) {
        printf("  ==> golden comparison FAILED\n");
        return fail > 0 ? fail : 1;
    }
    printf("  ==> all %d frames bit-exact vs Python/NumPy golden\n", m.frames);
    return 0;
}

// -----------------------------------------------------------------------------
// 第 4 组：入库 CSV 复核（保证 golden_mipi.csv 可被程序复算）
// -----------------------------------------------------------------------------
static int test_csv(const std::string &dir, const Meta &m)
{
    printf("---- [4] committed golden CSV re-check (golden_mipi.csv) ----\n");
    std::vector<u8> raw;
    if (!read_file(dir + "/raw10.bin", raw)) {
        printf("  SKIP: raw10.bin not present\n");
        return 0;
    }
    FILE *f = fopen((dir + "/golden_mipi.csv").c_str(), "r");
    if (!f) { printf("  SKIP: golden_mipi.csv not found\n"); return 0; }

    const int w = m.in_width, h = m.in_height;
    std::vector<std::vector<u16> > cache(m.frames);
    std::vector<std::vector<u8> > cache_rgb(m.frames);

    char line[512];
    int rows = 0, bad = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || strncmp(line, "frame,", 6) == 0) continue;
        int fr, x, y, b10, r8, g8, b8;
        char name[64];
        if (sscanf(line, "%d,%63[^,],%d,%d,%d,%d,%d,%d", &fr, name, &x, &y,
                   &b10, &r8, &g8, &b8) != 8) continue;
        if (fr < 0 || fr >= m.frames) { bad++; rows++; continue; }

        if (cache[fr].empty()) {
            unpack_stream(raw.data() + (size_t)fr * m.raw10_bytes, w, h, cache[fr]);
            demosaic_stream(cache[fr].data(), w, h, cache_rgb[fr]);
        }
        const u16 gb = cache[fr][(size_t)y * w + x];
        const u8 *p = &cache_rgb[fr][3u * (size_t)(y * w + x)];
        bool ok = (gb == (u16)b10) && (p[0] == (u8)r8) && (p[1] == (u8)g8) && (p[2] == (u8)b8);
        if (!ok) {
            bad++;
            printf("  FAIL frame=%d %s (%d,%d): csv=(%d,%d,%d,%d) model=(%u,%u,%u,%u)\n",
                   fr, name, x, y, b10, r8, g8, b8, gb, p[0], p[1], p[2]);
        }
        rows++;
    }
    fclose(f);
    printf("  %s CSV rows=%d, mismatches=%d\n", bad ? "FAIL" : "OK  ", rows, bad);
    return bad ? 1 : 0;
}

// -----------------------------------------------------------------------------
int main(int argc, char **argv)
{
    printf("==== host_model_mipi: raw10_unpack + bayer_demosaic arithmetic model ====\n");
    printf("     (NOT HLS evidence -- arithmetic only; no stream/BRAM/timing model)\n\n");

    int fail = test_embedded();

    const std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("fpga/sim/data_mipi");
    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) {
        printf("\n---- [2][3][4] SKIPPED: cannot read %s/meta.txt ----\n", dir.c_str());
        printf("     hint: run  python fpga/sim/gen_mipi_vectors.py  first.\n");
        printf("\n==== RESULT: %s (embedded only) ====\n", fail == 0 ? "PASS" : "FAIL");
        return fail == 0 ? 0 : 1;
    }

    if (test_stream_vs_naive(m.in_width, m.in_height, 12345) != 0) fail++;

    int f3 = test_golden(dir, m);
    if (f3 < 0) { fail++; printf("  golden: ERROR\n"); } else fail += f3;

    fail += test_csv(dir, m);

    printf("\n==== RESULT: %s ====\n", fail == 0 ? "PASS" : "FAIL");
    return fail == 0 ? 0 : 1;
}
