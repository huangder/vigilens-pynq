// =============================================================================
//  host_model_scale.cpp —— frame_scale 的**主机端算术模型**
// -----------------------------------------------------------------------------
//  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
//
//  这是什么：把 frame_scale 的内层判定**原样转写**成能在本机秒级运行的 C++ 程序。
//  ⚠️ **不是 HLS 证据**：不建模 AXI-Stream 握手 / FIFO 深度 / 时序。
//     真正 csim/csynth/cosim 必须在完整权限终端用 vitis-run 跑。
//
//  四组自证：
//    1) 内嵌边界用例（人工可核对）：反向映射序列、被丢弃的行列绝不出现、
//       冲激落在保留位必须原样出现在输出、无几何失真（两轴同一比例）
//    2) **正向扫描（镜像 IP）vs 反向映射（全帧直取）**：两条完全不同的代码路径，必须逐位相同
//    3) **跨语言黄金参考**：与 Python/NumPy 生成的 golden_scale.bin 全量逐位比对
//    4) **入库 CSV 复核**：重读 golden_scale.csv 逐行核对
//
//  用法： host_model_scale <data_dir>
//         host_model_scale fpga/sim/data_scale
// =============================================================================

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>

typedef uint8_t u8;

#define SCALE_NUM 2
#define SCALE_DEN 3

// 正向判定：输入 (x,y) 是否被保留（与 IP 中的条件逐字对应）
static inline bool keep_pixel(int x, int y, int x0, int cw)
{
    if ((y % SCALE_DEN) >= SCALE_NUM) return false;
    if (x < x0) return false;
    const int k = x - x0;
    if (k >= cw) return false;
    return ((k % SCALE_DEN) < SCALE_NUM);
}

// 反向映射：输出 (ox,oy) -> 源 (sx,sy)
static inline int src_x(int ox, int x0) { return x0 + ox + ox / 2; }
static inline int src_y(int oy) { return oy + oy / 2; }

// ---- 实现 A：正向扫描（**镜像 IP 的循环结构**）------------------------------
static void scale_forward(const u8 *in, int w, int h, int x0, int cw, std::vector<u8> &out)
{
    const int ow = cw / SCALE_DEN * SCALE_NUM;
    const int oh = h  / SCALE_DEN * SCALE_NUM;
    out.assign((size_t)ow * oh * 3, 0);
    size_t k = 0;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            if (keep_pixel(x, y, x0, cw)) {
                const size_t o = 3u * (size_t)(y * w + x);
                out[k + 0] = in[o + 0];
                out[k + 1] = in[o + 1];
                out[k + 2] = in[o + 2];
                k += 3;
            }
        }
    }
}

// ---- 实现 B：反向映射（全帧直取）--------------------------------------------
static void scale_reverse(const u8 *in, int w, int h, int x0, int cw, std::vector<u8> &out)
{
    const int ow = cw / SCALE_DEN * SCALE_NUM;
    const int oh = h  / SCALE_DEN * SCALE_NUM;
    out.assign((size_t)ow * oh * 3, 0);
    for (int oy = 0; oy < oh; oy++) {
        const int sy = src_y(oy);
        for (int ox = 0; ox < ow; ox++) {
            const int sx = src_x(ox, x0);
            const size_t o = 3u * (size_t)(sy * w + sx);
            const size_t d = 3u * (size_t)(oy * ow + ox);
            out[d + 0] = in[o + 0];
            out[d + 1] = in[o + 1];
            out[d + 2] = in[o + 2];
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
    int w = 0, h = 0, crop_x0 = 0, crop_w = 0, ow = 0, oh = 0, frames = 0;
    int rgb_in_bytes = 0, rgb_out_bytes = 0;
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
        int iv = atoi(v.c_str());
        if      (k == "in_width")     m.w = iv;
        else if (k == "in_height")    m.h = iv;
        else if (k == "crop_x0")      m.crop_x0 = iv;
        else if (k == "crop_w")       m.crop_w = iv;
        else if (k == "out_width")    m.ow = iv;
        else if (k == "out_height")   m.oh = iv;
        else if (k == "frames")       m.frames = iv;
        else if (k == "rgb_in_bytes") m.rgb_in_bytes = iv;
        else if (k == "rgb_out_bytes")m.rgb_out_bytes = iv;
    }
    fclose(f);
    return (m.w > 0 && m.h > 0 && m.frames > 0);
}

// -----------------------------------------------------------------------------
// 第 1 组：内嵌边界用例
// -----------------------------------------------------------------------------
static int test_embedded()
{
    printf("---- [1] embedded cases (hand-checkable) ----\n");
    int pass = 0, fail = 0;

    // (a) 反向映射序列：ox=0..7 -> k=0,1,3,4,6,7,9,10
    {
        const int x0 = 100;
        const int exp[8] = {0, 1, 3, 4, 6, 7, 9, 10};
        int bad = 0;
        for (int ox = 0; ox < 8; ox++) if (src_x(ox, x0) != x0 + exp[ox]) bad++;
        printf("  %s inverse map ox->sx : %s (bad=%d)\n", bad ? "FAIL" : "OK  ",
               bad ? "" : "0,1,3,4,6,7,9,10 (+crop_x0)", bad);
        bad ? fail++ : pass++;
    }

    // (b) 被丢弃的行/列绝不出现：整幅只有第三列(组内 k=2)与第三行(y%3==2)为白
    {
        const int W = 12, H = 9, X0 = 3, CW = 6;   // 输出 4x6
        std::vector<u8> in((size_t)W * H * 3, 0);
        for (int y = 0; y < H; y++) {
            for (int x = 0; x < W; x++) {
                const bool dropped = ((x >= X0) && ((x - X0) % SCALE_DEN == 2)) ||
                                     ((y % SCALE_DEN) == 2);
                if (dropped) {
                    const size_t o = 3u * (size_t)(y * W + x);
                    in[o] = in[o + 1] = in[o + 2] = 255;
                }
            }
        }
        std::vector<u8> out;
        scale_forward(in.data(), W, H, X0, CW, out);
        int whites = 0;
        for (size_t i = 0; i < out.size(); i += 3)
            if (out[i] == 255 && out[i + 1] == 255 && out[i + 2] == 255) whites++;
        bool ok = (whites == 0);
        printf("  %s dropped rows/cols never appear: whites in output = %d (expect 0)\n",
               ok ? "OK  " : "FAIL", whites);
        ok ? pass++ : fail++;
    }

    // (c) 冲激落在保留位必须原样出现，且落在正确的位置
    {
        const int W = 12, H = 9, X0 = 3, CW = 6;   // 输出 4x6
        std::vector<u8> in((size_t)W * H * 3, 0);
        // 取源 (X0+3, 3) 即 k=3（保留）、y=3（保留）-> 输出 (2,2)
        const int sx = X0 + 3, sy = 3;
        const size_t o = 3u * (size_t)(sy * W + sx);
        in[o] = 11; in[o + 1] = 22; in[o + 2] = 33;
        std::vector<u8> out;
        scale_forward(in.data(), W, H, X0, CW, out);
        const int ow = CW / SCALE_DEN * SCALE_NUM;
        const size_t d = 3u * (size_t)(2 * ow + 2);
        bool ok = (out[d] == 11 && out[d + 1] == 22 && out[d + 2] == 33);
        printf("  %s kept impulse lands at out(2,2) : got (%u,%u,%u) expect (11,22,33)\n",
               ok ? "OK  " : "FAIL", out[d], out[d + 1], out[d + 2]);
        ok ? pass++ : fail++;
    }

    // (d) 无几何失真：两轴比例必须相同（3:2 == 3:2）
    {
        const int W = 1280, H = 720, X0 = 160, CW = 960;
        const int ow = CW / SCALE_DEN * SCALE_NUM, oh = H / SCALE_DEN * SCALE_NUM;
        bool ok = (ow * 3 == CW * 2) && (oh * 3 == H * 2) && (ow == 640) && (oh == 480);
        printf("  %s aspect check: %dx%d -> %dx%d  (hx=3:2, vy=3:2, no distortion)\n",
               ok ? "OK  " : "FAIL", W, H, ow, oh);
        ok ? pass++ : fail++;
    }

    printf("  embedded: %d passed, %d failed\n", pass, fail);
    return fail;
}

// -----------------------------------------------------------------------------
// 第 2 组：正向 vs 反向（两条代码路径）
// -----------------------------------------------------------------------------
static int test_forward_vs_reverse(int w, int h, int x0, int cw)
{
    printf("---- [2] forward scan (IP mirror) vs inverse map (full-frame) ----\n");
    std::vector<u8> in((size_t)w * h * 3);
    unsigned s = 20260929u;
    for (size_t i = 0; i < in.size(); i++) {
        s = s * 1664525u + 1013904223u;
        in[i] = (u8)((s >> 16) & 0xFF);
    }
    std::vector<u8> a, b;
    scale_forward(in.data(), w, h, x0, cw, a);
    scale_reverse(in.data(), w, h, x0, cw, b);
    size_t bad = 0;
    for (size_t i = 0; i < a.size(); i++) if (a[i] != b[i]) bad++;
    printf("  %s %dx%d -> %zux3 bytes: mismatches=%zu\n",
           bad ? "FAIL" : "OK  ", w, h, a.size() / 3, bad);
    if (bad) {
        for (size_t i = 0; i < a.size(); i++) {
            if (a[i] != b[i]) {
                printf("       first mismatch at byte %zu (px %zu, ch %zu): fwd=%u rev=%u\n",
                       i, i / 3, i % 3, a[i], b[i]);
                break;
            }
        }
        return 1;
    }
    return 0;
}

// -----------------------------------------------------------------------------
// 第 3 组：跨语言黄金参考
// -----------------------------------------------------------------------------
static int test_golden(const std::string &dir, const Meta &m)
{
    printf("---- [3] cross-language golden reference (Python/NumPy) ----\n");
    std::vector<u8> in, gold;
    if (!read_file(dir + "/rgb_in.bin", in) || !read_file(dir + "/golden_scale.bin", gold)) {
        printf("  ERROR: missing .bin vectors in %s (run gen_scale_vectors.py)\n", dir.c_str());
        return -1;
    }
    const size_t exp_in = (size_t)m.frames * (size_t)m.rgb_in_bytes;
    const size_t exp_out = (size_t)m.frames * (size_t)m.rgb_out_bytes;
    if (in.size() != exp_in || gold.size() != exp_out) {
        printf("  ERROR: size mismatch in=%zu/%zu out=%zu/%zu\n",
               in.size(), exp_in, gold.size(), exp_out);
        return -1;
    }
    printf("  meta: in %dx%d crop(%d,%d) -> out %dx%d, %d frames\n",
           m.w, m.h, m.crop_x0, m.crop_w, m.ow, m.oh, m.frames);

    int fail = 0;
    size_t total_fwd = 0, total_rev = 0;
    for (int fi = 0; fi < m.frames; fi++) {
        const u8 *src = in.data() + (size_t)fi * m.rgb_in_bytes;
        const u8 *g   = gold.data() + (size_t)fi * m.rgb_out_bytes;
        std::vector<u8> fw, rv;
        scale_forward(src, m.w, m.h, m.crop_x0, m.crop_w, fw);
        scale_reverse(src, m.w, m.h, m.crop_x0, m.crop_w, rv);
        size_t bf = 0, br = 0;
        for (size_t i = 0; i < fw.size(); i++) {
            if (fw[i] != g[i]) bf++;
            if (rv[i] != g[i]) br++;
        }
        total_fwd += bf;
        total_rev += br;
        bool ok = (bf == 0 && br == 0);
        if (!ok) fail++;
        printf("  %s frame %d: forward_bad=%zu reverse_bad=%zu\n",
               ok ? "OK  " : "FAIL", fi, bf, br);
    }
    printf("  totals: forward mismatches=%zu, reverse mismatches=%zu\n", total_fwd, total_rev);
    if (total_fwd || total_rev) { printf("  ==> golden comparison FAILED\n"); return fail > 0 ? fail : 1; }
    printf("  ==> all %d frames bit-exact vs Python/NumPy golden\n", m.frames);
    return 0;
}

// -----------------------------------------------------------------------------
// 第 4 组：入库 CSV 复核
// -----------------------------------------------------------------------------
static int test_csv(const std::string &dir, const Meta &m)
{
    printf("---- [4] committed golden CSV re-check (golden_scale.csv) ----\n");
    std::vector<u8> in;
    if (!read_file(dir + "/rgb_in.bin", in)) { printf("  SKIP: rgb_in.bin not present\n"); return 0; }
    FILE *f = fopen((dir + "/golden_scale.csv").c_str(), "r");
    if (!f) { printf("  SKIP: golden_scale.csv not found\n"); return 0; }

    std::vector<std::vector<u8> > cache(m.frames);
    char line[512];
    int rows = 0, bad = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || strncmp(line, "frame,", 6) == 0) continue;
        int fr, ox, oy, sx, sy, r8, g8, b8;
        char name[64];
        if (sscanf(line, "%d,%63[^,],%d,%d,%d,%d,%d,%d,%d",
                   &fr, name, &ox, &oy, &sx, &sy, &r8, &g8, &b8) != 9) continue;
        if (fr < 0 || fr >= m.frames) { bad++; rows++; continue; }
        if (cache[fr].empty()) scale_forward(in.data() + (size_t)fr * m.rgb_in_bytes,
                                             m.w, m.h, m.crop_x0, m.crop_w, cache[fr]);
        const int ex = src_x(ox, m.crop_x0), ey = src_y(oy);
        const size_t d = 3u * (size_t)(oy * m.ow + ox);
        const u8 *p = &cache[fr][d];
        bool ok = (p[0] == (u8)r8) && (p[1] == (u8)g8) && (p[2] == (u8)b8) &&
                  (ex == sx) && (ey == sy);
        if (!ok) {
            bad++;
            printf("  FAIL frame=%d %s (%d,%d): csv src=(%d,%d) rgb=(%d,%d,%d) ; model src=(%d,%d) rgb=(%u,%u,%u)\n",
                   fr, name, ox, oy, sx, sy, r8, g8, b8, ex, ey, p[0], p[1], p[2]);
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
    printf("==== host_model_scale: frame_scale arithmetic model ====\n");
    printf("     (NOT HLS evidence -- arithmetic only; no stream/BRAM/timing model)\n\n");

    int fail = test_embedded();

    const std::string dir = (argc > 1) ? std::string(argv[1]) : std::string("fpga/sim/data_scale");
    Meta m;
    if (!read_meta(dir + "/meta.txt", m)) {
        printf("\n---- [2][3][4] SKIPPED: cannot read %s/meta.txt ----\n", dir.c_str());
        printf("     hint: run  python fpga/sim/gen_scale_vectors.py  first.\n");
        printf("\n==== RESULT: %s (embedded only) ====\n", fail == 0 ? "PASS" : "FAIL");
        return fail == 0 ? 0 : 1;
    }

    if (test_forward_vs_reverse(m.w, m.h, m.crop_x0, m.crop_w) != 0) fail++;
    int f3 = test_golden(dir, m);
    if (f3 < 0) { fail++; printf("  golden: ERROR\n"); } else fail += f3;
    fail += test_csv(dir, m);

    printf("\n==== RESULT: %s ====\n", fail == 0 ? "PASS" : "FAIL");
    return fail == 0 ? 0 : 1;
}
