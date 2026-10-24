/* S4 上板自检（裸机版）：由 A9 本身驱动 axi_dma + roi/rgb2gray/motion 跑一帧。
 *
 * 为什么不用 PYNQ 的 board 脚本（bringup_check / dma_test / hw_sw_compare）：
 * 本板没有 Linux/PYNQ（COM11 无输出），三个脚本又都 import pynq；
 * JTAG 逐寄存器轮询又会把 FTDI 链路拖掉线。
 * 所以把轮询放进 CPU，JTAG 只负责下载程序 + 读结果块。
 *
 * 结果块放在 0x0A000000：magic/status + ASCII 日志（mrd -bin 一次取回）。
 */
#define REG32(a) (*(volatile unsigned *)(a))

/* ---- AXI DMA 寄存器（PG021，simple mode）---- */
#define MM2S_CR   0x41E00000u
#define MM2S_SR   0x41E00004u
#define MM2S_SA   0x41E00018u
#define MM2S_LEN  0x41E00028u
#define MM2S_SADDR 0x41E00018u
#define S2MM_CR   0x41E00030u
#define S2MM_SR   0x41E00034u
#define S2MM_DA   0x41E00048u
#define S2MM_LEN  0x41E00058u

/* ---- HLS IP 控制/数据寄存器 ---- */
#define ROI_BASE  0x40040000u
#define RGB_BASE  0x40030000u
#define MOT_BASE  0x40020000u
#define CTRL      0x00u
#define ROI_X0    0x10u
#define ROI_Y0    0x18u
#define ROI_X1    0x20u
#define ROI_Y1    0x28u
#define ROI_W     0x30u
#define ROI_H     0x38u
#define ROI_SUM_R 0x40u
#define ROI_SUM_G 0x48u
#define ROI_SUM_B 0x50u
#define ROI_CNT   0x58u
#define ROI_FID   0x60u
#define RGB_W     0x10u
#define RGB_H     0x18u
#define RGB_OW    0x20u
#define RGB_OH    0x28u
#define RGB_PC    0x30u
#define RGB_SG    0x38u
#define RGB_FID   0x40u
#define MOT_W     0x10u
#define MOT_H     0x18u
#define MOT_TH    0x20u
#define MOT_DIFF  0x28u
#define MOT_PIX   0x30u
#define MOT_RATIO 0x38u
#define MOT_CNT   0x40u
#define MOT_FID   0x48u

#define FRAME_ADDR 0x08000000u
#define GRAY_ADDR  0x09000000u
#define RES_ADDR   0x0A000000u
#define GOLD_ADDR  0x0B000000u
/* phase=9（streaming）用的大缓冲：300 帧×129,600 B = 38.88 MB。
 * 放在 256 MB 处，既避开上面的结果块/黄金参考，也仍在 HP0 的 1 GB 段内。 */
#define STREAM_ADDR 0x10000000u
/* 阶段选择：驱动（s4_run.py）在 con 之前往这里写 1 = 只做 4 字节微测试，2 = 只做整帧。
 * 为什么要分两个阶段：微测试也会把字节推进链路，IP 的像素计数器会先吃掉几十个像素，
 * 同一轮里再做整帧，第一帧的像素就会整体错位 ⇒ 黄金参考必然对不上。 */
#define PHASE_ADDR 0x0A001000u
/* 寄存器写入顺序的 A/B 对照开关（驱动写 0/1）：
 *   0 = PG021 规定顺序：DMACR.RS=1 → 地址 → **LENGTH 最后写**
 *   1 = 旧顺序（SA/DA → LENGTH → RS），用来在同一块板上把两种顺序各跑一次，
 *       避免"改完还是不通用猜"——两次结果一对比，顺序到底是不是原因当场就能定。
 * 为什么要做这个对照：我们的固件一开始写的正是旧顺序，而 SR 里连一个错误位都没有，
 * 症状（通道自认在跑、永不发 AR/TVALID）与"启动事件被漏掉"高度吻合，但需要板上证据。 */
#define ORDER_ADDR 0x0A001004u

/* 几何档位：**编译期**决定（灰度抽取比是 rgb2gray 的编译期宏，运行时改不了）。
 *   640×480 档（默认，契约 v1.1）：3/5 抽取 → 384×288
 *   720p60 档（v1.5 主档）：    3/8 抽取 → 480×270
 * 用法：加 -DFRAME_W=1280u -DFRAME_H=720u -DGRAY_W=480u -DGRAY_H=270u 重编。
 * ⚠️ 必须与烧进去的 bitstream 同档：bitstream 里的 rgb2gray 是按某个抽取比综合的，
 *    档位对不上时灰度图看起来"有内容"但每行都错位 —— 那是静默错，不是崩溃。 */
#ifndef FRAME_W
#define FRAME_W 640u
#endif
#ifndef FRAME_H
#define FRAME_H 480u
#endif
#ifndef GRAY_W
#define GRAY_W  384u
#endif
#ifndef GRAY_H
#define GRAY_H  288u
#endif

#define MAGIC_RUN  0xDEADBEEFu
#define MAGIC_DONE 0x600D600Du

#define POLL_MAX 2000000u

/* 推荐接法（BUG-037）里 S2MM 目标长度比本帧字节数多要的余量。
 * 为什么不精确等于一帧：收满即 halt 会让 axis_tlast_suppress 的下游 TREADY 撤掉，
 * ROI/rgb2gray 的收尾写拿不到应答（CTRL 停在 0x1、结果寄存器全 0），而灰度数据其实是对的。
 * 384 B 是 SmartConnect 一拍 burst（64 bit × 16）的 3 倍，够盖住帧末抖动的几拍。 */
#define SINK_MARGIN 384u

/* 门限 6 要求"连续 ≥300 帧不死锁" */
#define LONG_FRAMES 300u

static char *const logbuf = (char *)(RES_ADDR + 16);
static unsigned loglen;

static void log_ch(char c) {
    if (loglen < 3000) logbuf[loglen++] = c;
}
static void log_str(const char *s) { while (*s) log_ch(*s++); }
static void log_hex(unsigned v) {
    const char *h = "0123456789ABCDEF";
    int i;
    log_str("0x");
    for (i = 7; i >= 0; i--) log_ch(h[(v >> (i * 4)) & 0xF]);
}
static void log_dec(unsigned v) {
    char t[12];
    int i = 0;
    if (!v) { log_ch('0'); return; }
    while (v) { t[i++] = (char)('0' + v % 10); v /= 10; }
    while (i) log_ch(t[--i]);
}
static void log_kv(const char *k, unsigned v) {
    log_str(k); log_str("="); log_hex(v); log_str("\n");
}
static void log_nv(const char *k, unsigned v) {
    log_str(k); log_str("="); log_dec(v); log_str("\n");
}

static void wr(unsigned addr, unsigned val) { REG32(addr) = val; }
static unsigned rd(unsigned addr) { return REG32(addr); }

/* 结果块头部：magic, status, log_len, log_crc */
static void finish(unsigned status) {
    REG32(RES_ADDR + 4) = loglen;
    REG32(RES_ADDR + 8) = status;
    REG32(RES_ADDR + 0) = MAGIC_DONE;
}

/* S2MM 一次传输要收多少字节：
 *   phase 3/4/5 = 迷你帧（1 像素 → 1 字节灰度）
 *   phase 6     = 故意多要（120000 > 本帧实际 110592），让链尾永不反压
 *   phase 7     = 本帧实际字节 + 384 余量（推荐接法）：链尾不提前停，帧末由软件
 *                 写 S2MM_CR.RS=0 停掉（见 phase==7 的处理）
 *   其余        = 一帧灰度 384x288 = 110592 */
static unsigned s2mm_len_of(unsigned phase) {
    if (phase >= 3u && phase <= 5u) return 1u;
    if (phase == 6u) return 120000u;
    if (phase >= 7u) return (unsigned)(GRAY_W * GRAY_H) + SINK_MARGIN;
    return (unsigned)(GRAY_W * GRAY_H);
}

/* 软复位一个通道：CR.Reset=1 → 轮询到硬件自清 */
static unsigned ch_reset(unsigned cr, const char *tag) {
    unsigned n = 0;
    wr(cr, 0x00000004u);
    while ((rd(cr) & 0x4u) && n < POLL_MAX) n++;
    log_str(tag);
    log_nv("_reset_loops", n);
    return n;
}

/* 静默版软复位：长跑循环里每帧都要调，不能往日志里刷（日志区只有 3000 B） */
static unsigned ch_reset_quiet(unsigned cr) {
    unsigned n = 0;
    wr(cr, 0x00000004u);
    while ((rd(cr) & 0x4u) && n < POLL_MAX) n++;
    return n;
}

/* 微测试：只发 MM2S，不启动任何 IP。
 * 目的：把"HP0 读 DDR 通不通"和"下游反压"分开——
 *   若是 HP0/读通路死了，连 64 B 都完不成（SR 停在 0，无 IOC）；
 *   若只是下游堵，64 B/1 KB 能塞进 conv_rgb 的 FIFO，SR 会置 IOC(bit12)+Idle(bit1)。 */
static unsigned mm2s_short(unsigned sa, unsigned len, const char *tag) {
    unsigned n = 0;
    ch_reset(MM2S_CR, tag);
    /* ⚠️ PG021《Direct Register Mode (Simple DMA)》的**规定顺序**：
     *   ① DMACR.RS=1 → ② 写 MM2S_SA → ③ **MM2S_LENGTH 必须最后写**
     *   （"The MM2S_LENGTH register must be written last." 写 LENGTH 才是启动传输的事件）。
     * 之前写成 SA → LEN → RS，正好把启动事件漏在最前面 ⇒ 通道自认在跑却永不发 AR/TVALID，
     * 而且 SR 无任何错误位（正是板上观察到的"零进展、零错误"）。 */
    wr(MM2S_CR, 0x00000001u);
    wr(MM2S_SA, sa);
    wr(MM2S_LEN, len);
    /* 关键取证：CR 是否保持住 RS、SA/LEN 是否原样写入。
     * 若 CR 读回 0 → RS 没写进去（复位或被忽略）；若 LEN 被截断 → 长度寄存器位宽不够。 */
    log_str(tag);
    log_kv("_cr_rb", rd(MM2S_CR));
    log_str(tag);
    log_kv("_sa_rb", rd(MM2S_SA));
    log_str(tag);
    log_kv("_len_rb", rd(MM2S_LEN));
    while (((rd(MM2S_SR) & 0x2u) == 0) && n < 200000u) n++;
    log_str(tag);
    log_nv("_idle_loops", n);
    log_str(tag);
    log_kv("_sr", rd(MM2S_SR));
    return n;
}

int main(void) {
    unsigned roi_ctrl, rgb_ctrl, mot_ctrl;
    unsigned n;
    unsigned phase;
    unsigned order;

    phase = rd(PHASE_ADDR);
    REG32(RES_ADDR + 0) = MAGIC_RUN;
    log_str("S4 bench start\n");
    log_kv("phase", phase);

    log_kv("pre_mm2s_cr", rd(MM2S_CR));
    log_kv("pre_mm2s_sr", rd(MM2S_SR));
    log_kv("pre_s2mm_cr", rd(S2MM_CR));
    log_kv("pre_s2mm_sr", rd(S2MM_SR));

    /* 清掉上次残留的 DMA 状态位（W1C） */
    wr(MM2S_SR, 0x00000FF0u);
    wr(S2MM_SR, 0x00000FF0u);

    /* 门限 4 的复位语义：软复位必须能自己完成（sg 版这里是死循环） */
    ch_reset(MM2S_CR, "mm2s");
    ch_reset(S2MM_CR, "s2mm");

    /* IP 配置：phase=3/4 用 1x1 迷你帧，其余用 640x480 档。
     * phase=4 与 3 相同，只是 MM2S 的源地址改成 0x40000000（HP0 不可达）
     * —— 用来判断 DMA 到底有没有发 AR：若发了，HP0 会回 DECERR ⇒ SR 的
     * DMADecErr(bit5)/Err(bit14) 会置位；若一位都不置，说明 AR 根本没发出。 */
    {
        unsigned mini = (phase >= 3u && phase <= 5u) ? 1u : 0u;
        unsigned w  = mini ? 1u : FRAME_W;
        unsigned h  = mini ? 1u : FRAME_H;
        unsigned gw = mini ? 1u : GRAY_W;
        unsigned gh = mini ? 1u : GRAY_H;
        /* phase=6：故意把 MOT 要的像素数配大 1 行（384x289），并让 S2MM 的目标长度
         * 大于本帧实际输出（120000 > 110592）⇒ 链尾**永远不反压**、MOT 永远不结束。
         * 目的：判断整帧跑完后 ROI/RGB 不置 ap_done 到底是
         *   ① 下游反压（链尾停下 → MOT 停读 → RGB 卡在最后一次写 → ROI 再卡）
         *   ② 还是 IP 自己收尾（epilogue 写 s_axilite）就回不来。
         * 若本阶段 ROI/RGB 能置 ap_done/写回结果寄存器 ⇒ 是 ①；仍 0x1 ⇒ 是 ②。 */
        if (phase == 6u) gh = GRAY_H + 1u;
        wr(ROI_BASE + ROI_X0, 0);
        wr(ROI_BASE + ROI_Y0, 0);
        wr(ROI_BASE + ROI_X1, w);
        wr(ROI_BASE + ROI_Y1, h);
        wr(ROI_BASE + ROI_W, w);
        wr(ROI_BASE + ROI_H, h);
        wr(RGB_BASE + RGB_W, w);
        wr(RGB_BASE + RGB_H, h);
        wr(MOT_BASE + MOT_W, gw);
        wr(MOT_BASE + MOT_H, gh);
        wr(MOT_BASE + MOT_TH, 16);
        log_nv("cfg_w", w);
        log_nv("cfg_h", h);
        log_nv("cfg_gw", gw);
        log_nv("cfg_gh", gh);
        log_kv("roi_x1_rb", rd(ROI_BASE + ROI_X1));
        log_kv("mot_w_rb", rd(MOT_BASE + MOT_W));
    }

    /* 启动 IP（阻塞在 stream 读上等数据）。
     * 顺序很关键：HLS 的 s_axis TREADY 在 ap_start 之前是 0。
     * phase=5：**只启动 roi**，用来把"链首这一跳通不通"与下游区分开
     *          （roi 的 video_out 没有消费者会反压，但 MM2S 只要能动就够了）。 */
    wr(ROI_BASE + CTRL, 1);
    if (phase != 5u) {
        wr(RGB_BASE + CTRL, 1);
        wr(MOT_BASE + CTRL, 1);
    }
    log_kv("roi_ctrl_started", rd(ROI_BASE + CTRL));
    log_kv("rgb_ctrl_started", rd(RGB_BASE + CTRL));
    log_kv("mot_ctrl_started", rd(MOT_BASE + CTRL));

    /* 阶段 1（历史探针，结果**不可作为判据**）：这里只启动 IP、没有 arm S2MM ⇒ 链尾
     * s_axis_s2mm_tready=0，整链反压到 MM2S，连 4 B 都搬不动。该结论已作废，
     * 数据面判据改看 phase=3 的迷你链路（1x1 帧、3 B 进、1 B 出）。 */
    if (phase == 1u) {
        mm2s_short(0x08000000u, 4u, "t_run4");
        mm2s_short(0x08000000u, 64u, "t_run64");
        mm2s_short(0x00000000u, 16u, "t_ocm16");
        /* 未映射地址（0x40000000 在 HP0 的 1G 段之外，DMA 母口只连到 HP0）。 */
        mm2s_short(0x40000000u, 4u, "t_undec4");
        log_str("probe_done\n");
        finish(0);
        for (;;) { }
    }
    ch_reset(MM2S_CR, "mm2s_pre_main");
    ch_reset(S2MM_CR, "s2mm_pre_main");

    /* 关键顺序（PG021 simple mode 明文规定）：
     *   ① DMACR.RS=1 → ② 写地址 → ③ **LENGTH 最后写**（写 LENGTH 即启动传输）。
     * 之前写的 SA → LEN → RS 是错的：LENGTH 写的时候 RS 还是 0，启动事件被漏掉，
     * 通道自认在跑却永远不发 AR、也不发 TVALID，SR 里还没有任何错误位。
     * order=1 时故意走回旧顺序，做成板上 A/B 对照（见 ORDER_ADDR 的注释）。 */
    order = rd(ORDER_ADDR);
    log_kv("order", order);
    if (order == 0u) {
        wr(S2MM_CR, 0x00000001u);
        wr(S2MM_DA, GRAY_ADDR);
        wr(S2MM_LEN, s2mm_len_of(phase));
    } else {
        wr(S2MM_DA, GRAY_ADDR);
        wr(S2MM_LEN, s2mm_len_of(phase));
        wr(S2MM_CR, 0x00000001u);
    }
    log_kv("s2mm_da_rb", rd(S2MM_DA));
    log_kv("s2mm_len_rb", rd(S2MM_LEN));
    log_kv("s2mm_armed_cr", rd(S2MM_CR));
    log_kv("s2mm_armed_sr", rd(S2MM_SR));

    if (order == 0u) {
        wr(MM2S_CR, 0x00000001u);
        wr(MM2S_SA, (phase == 4u) ? 0x40000000u : FRAME_ADDR);
        wr(MM2S_LEN, (phase >= 3u && phase <= 5u) ? 3u : (FRAME_W * FRAME_H * 3u));
    } else {
        wr(MM2S_SA, (phase == 4u) ? 0x40000000u : FRAME_ADDR);
        wr(MM2S_LEN, (phase >= 3u && phase <= 5u) ? 3u : (FRAME_W * FRAME_H * 3u));
        wr(MM2S_CR, 0x00000001u);
    }
    log_kv("mm2s_len_rb", rd(MM2S_LEN));
    log_kv("mm2s_armed_cr", rd(MM2S_CR));
    log_kv("mm2s_armed_sr", rd(MM2S_SR));

    /* 等 S2MM 收完（SR.Idle=bit1）。phase=8 用推荐接法（长度带余量），本来就不会自己收满，
     * 所以这一等必然打满上界、纯浪费时间 —— 跳过，改由后面的 ap_done + 软件停判定。 */
    if (phase != 8u && phase != 9u) {
        n = 0;
        while ((rd(S2MM_SR) & 0x2u) == 0) {
            n++;
            if (n > POLL_MAX) break;
        }
        log_nv("s2mm_wait_loops", n);
        log_kv("s2mm_sr_after", rd(S2MM_SR));
    }

    /* 等 MM2S 空闲（这里是"发完了"的直接证据） */
    n = 0;
    while ((rd(MM2S_SR) & 0x2u) == 0) {
        n++;
        if (n > POLL_MAX) break;
    }
    log_nv("mm2s_idle_loops", n);
    log_kv("mm2s_sr_after", rd(MM2S_SR));

    /* 等三个 IP ap_done（CTRL.bit1） */
    n = 0;
    while (((rd(ROI_BASE + CTRL) & 0x2u) == 0) && n < POLL_MAX) n++;
    roi_ctrl = rd(ROI_BASE + CTRL);
    log_nv("roi_wait_loops", n);
    log_kv("roi_ctrl", roi_ctrl);
    n = 0;
    while (((rd(RGB_BASE + CTRL) & 0x2u) == 0) && n < POLL_MAX) n++;
    rgb_ctrl = rd(RGB_BASE + CTRL);
    log_nv("rgb_wait_loops", n);
    log_kv("rgb_ctrl", rgb_ctrl);
    n = 0;
    while (((rd(MOT_BASE + CTRL) & 0x2u) == 0) && n < POLL_MAX) n++;
    mot_ctrl = rd(MOT_BASE + CTRL);
    log_nv("mot_wait_loops", n);
    log_kv("mot_ctrl", mot_ctrl);

    if (phase >= 7u) {
        /* 推荐接法：链尾由软件停。
         * S2MM 的目标长度故意多要 384 字节，所以帧末它既不会 halt（不反压上游），
         * 也不会报 datamover 内部错（那是"收满却没见过 TLAST"才会亮的 bit4）。
         * 等本帧的 IP 全部收尾后，软件把 RS 清掉即可。 */
        wr(S2MM_CR, 0x00000000u);
        log_kv("s2mm_stopped_cr", rd(S2MM_CR));
        log_kv("s2mm_stopped_sr", rd(S2MM_SR));
    }

    /* 结果寄存器 */
    log_str("ROI "); log_dec(rd(ROI_BASE + ROI_SUM_R)); log_ch(' ');
    log_dec(rd(ROI_BASE + ROI_SUM_G)); log_ch(' ');
    log_dec(rd(ROI_BASE + ROI_SUM_B)); log_ch(' ');
    log_dec(rd(ROI_BASE + ROI_CNT)); log_ch(' ');
    log_dec(rd(ROI_BASE + ROI_FID)); log_str("\n");
    log_str("RGB "); log_dec(rd(RGB_BASE + RGB_OW)); log_ch(' ');
    log_dec(rd(RGB_BASE + RGB_OH)); log_ch(' ');
    log_dec(rd(RGB_BASE + RGB_PC)); log_ch(' ');
    log_dec(rd(RGB_BASE + RGB_SG)); log_ch(' ');
    log_dec(rd(RGB_BASE + RGB_FID)); log_str("\n");
    log_str("MOT "); log_dec(rd(MOT_BASE + MOT_DIFF)); log_ch(' ');
    log_dec(rd(MOT_BASE + MOT_PIX)); log_ch(' ');
    log_dec(rd(MOT_BASE + MOT_RATIO)); log_ch(' ');
    log_dec(rd(MOT_BASE + MOT_CNT)); log_ch(' ');
    log_dec(rd(MOT_BASE + MOT_FID)); log_str("\n");

    log_str("done\n");

    /* ── 诊断：延时后二次复读 ─────────────────────────────────────────────
     * 整帧跑完时观察到 ROI/RGB 的 CTRL=0x1（ap_start=1、ap_idle=0、ap_done=0）
     * 而结果寄存器全 0；下游 MOT 却是 0x4（ap_idle=1）+ fid=1。
     * 数据确实流到了 MOT（CMP mismatch=0），说明 ROI/RGB 的像素循环跑过，
     * 却在"收尾（写输出寄存器 / 置 ap_done）"这一步没回来 —— 这是逻辑矛盾，
     * 所以这里空转一段时间再读一次，用来区分两种情况：
     *   ① 二次读变成 0x4/0x6 且 fid=1  ⇒ 只是"读得太早"（DMA 停得比 IP 收尾快）
     *   ② 二次读仍是 0x1 且 fid=0     ⇒ IP 真的卡在收尾，需要单独查
     * 空转次数按 100 MHz、每次约 3 拍估，1.2e8 次 ≈ 3.6 s（远大于 IP 收尾所需）。
     */
    {
        volatile unsigned spin;
        for (spin = 0; spin < 8000000u; spin++) {
            /* 故意空转 */
        }
    }
    log_kv("post_roi_ctrl", rd(ROI_BASE + CTRL));
    log_kv("post_rgb_ctrl", rd(RGB_BASE + CTRL));
    log_kv("post_mot_ctrl", rd(MOT_BASE + CTRL));
    log_nv("post_roi_fid", rd(ROI_BASE + ROI_FID));
    log_nv("post_roi_cnt", rd(ROI_BASE + ROI_CNT));
    log_nv("post_rgb_fid", rd(RGB_BASE + RGB_FID));
    log_nv("post_rgb_pc", rd(RGB_BASE + RGB_PC));
    log_nv("post_mot_fid", rd(MOT_BASE + MOT_FID));
    log_nv("post_mot_cnt", rd(MOT_BASE + MOT_CNT));
    log_kv("post_mm2s_sr", rd(MM2S_SR));
    log_kv("post_s2mm_sr", rd(S2MM_SR));

    /* 门限 8 的比对放进 CPU：JTAG 一次读 110 KB 会拖垮 FTDI 链路（实测掉线），
     * 所以把黄金参考也灌进 DDR（GOLD_ADDR），由 A9 逐字节比对，只回传三个数。 */
    if (phase == 2u || phase == 7u || phase == 9u) {
        const unsigned char *hw = (const unsigned char *)GRAY_ADDR;
        const unsigned char *gd = (const unsigned char *)GOLD_ADDR;
        unsigned i, bad = 0, first = 0xFFFFFFFFu;
        for (i = 0; i < GRAY_W * GRAY_H; i++) {
            if (hw[i] != gd[i]) {
                if (first == 0xFFFFFFFFu) first = i;
                bad++;
            }
        }
        log_str("CMP mismatch="); log_dec(bad);
        log_str(" first_idx="); log_dec(first);
        if (first != 0xFFFFFFFFu) {
            log_str(" hw="); log_dec(hw[first]);
            log_str(" golden="); log_dec(gd[first]);
            log_str(" y="); log_dec(first / GRAY_W);
            log_str(" x="); log_dec(first % GRAY_W);
        }
        log_str("\n");
    }

    /* ── phase=8：门限 6「连续 ≥300 帧不死锁」+ 门限 7「确定性」─────────────
     * 同一帧重复 LONG_FRAMES 次，每帧都走**推荐接法**：
     *   清三个 IP 的 CTRL → 启动三个 IP → S2MM(RS → DA → LEN=帧+余量)
     *   → MM2S(RS → SA → LEN=整帧) → 等 MM2S idle → 等三个 IP ap_done → 软件停 S2MM
     * 判据：① 300 帧里没有一帧超时（不死锁）；② 三个 IP 的 frame_id 逐帧 +1（不跳不重）。
     * 计时只报 Cortex-A9 global timer（0xF8F00200）的 tick 差，**不换算成 fps**：
     * 它的频率与 CPU_3x2x 的关系我没有板上证据，不编数字。
     */
    if (phase == 8u) {
        unsigned f, fails = 0, first_fail = 0, okf = 0;
        unsigned fid0 = rd(ROI_BASE + ROI_FID);
        unsigned t0 = rd(0xF8F00200u), t1;
        unsigned k;
        log_nv("long_frames", LONG_FRAMES);
        log_nv("long_fid0", fid0);
        for (f = 1; f <= LONG_FRAMES; f++) {
            unsigned bad = 0;
            /* 每帧开始把两个通道**软复位**并清中断位：
             * 上一帧帧末是我们用 CR.RS=0 手动停的 S2MM（它从没收到 TLAST、也从没 IOC），
             * 不复位直接改写 LEN 再 RS=1，实测第 1 帧就走不动（SR=0x5011、三个 IP 都不 done）。
             * 复位后 SR=Halted，W1C 清掉残留的 IOC/Err 位，后面的等待才有确定的起点。 */
            ch_reset_quiet(MM2S_CR);
            ch_reset_quiet(S2MM_CR);
            wr(MM2S_SR, 0x00007000u);
            wr(S2MM_SR, 0x00007000u);
            wr(ROI_BASE + CTRL, 1); wr(RGB_BASE + CTRL, 1); wr(MOT_BASE + CTRL, 1);
            wr(S2MM_CR, 1);
            wr(S2MM_DA, GRAY_ADDR);
            wr(S2MM_LEN, (unsigned)(GRAY_W * GRAY_H) + SINK_MARGIN);
            wr(MM2S_CR, 1);
            wr(MM2S_SA, FRAME_ADDR);
            wr(MM2S_LEN, (unsigned)(FRAME_W * FRAME_H * 3u));
            /* 等 MM2S 的 IOC（bit12）而不是 Idle：复位后 Idle 可能还没掉，等 Idle 会立刻误判 */
            k = 0;
            while ((rd(MM2S_SR) & 0x1000u) == 0 && k < POLL_MAX) k++;
            if (k >= POLL_MAX) bad = 1u;
            if (!bad) {
                k = 0;
                while (((rd(ROI_BASE + CTRL) & 0x2u) == 0 ||
                        (rd(RGB_BASE + CTRL) & 0x2u) == 0 ||
                        (rd(MOT_BASE + CTRL) & 0x2u) == 0) && k < POLL_MAX) k++;
                if (k >= POLL_MAX) bad = 2u;
            }
            wr(S2MM_CR, 0);
            if (!bad && (rd(ROI_BASE + ROI_FID) != fid0 + f ||
                         rd(RGB_BASE + RGB_FID)  != fid0 + f ||
                         rd(MOT_BASE + MOT_FID)  != fid0 + f)) bad = 3u;
            if (bad) {
                fails++;
                if (first_fail == 0u) {
                    first_fail = f;
                    log_nv("fail_frame", f);
                    log_nv("fail_kind", bad);
                    log_kv("fail_mm2s_sr", rd(MM2S_SR));
                    log_kv("fail_s2mm_sr", rd(S2MM_SR));
                    log_kv("fail_roi_ctrl", rd(ROI_BASE + CTRL));
                    log_kv("fail_rgb_ctrl", rd(RGB_BASE + CTRL));
                    log_kv("fail_mot_ctrl", rd(MOT_BASE + CTRL));
                    log_nv("fail_roi_fid", rd(ROI_BASE + ROI_FID));
                    log_nv("fail_rgb_fid", rd(RGB_BASE + RGB_FID));
                    log_nv("fail_mot_fid", rd(MOT_BASE + MOT_FID));
                }
                break;
            }
            okf++;
        }
        t1 = rd(0xF8F00200u);
        log_nv("long_ok_frames", okf);
        log_nv("long_fails", fails);
        log_nv("long_first_fail", first_fail);
        log_nv("long_gt_ticks", t1 - t0);
        log_nv("long_roi_fid", rd(ROI_BASE + ROI_FID));
        log_nv("long_rgb_fid", rd(RGB_BASE + RGB_FID));
        log_nv("long_mot_fid", rd(MOT_BASE + MOT_FID));
        log_nv("long_roi_sum_r", rd(ROI_BASE + ROI_SUM_R));
        log_nv("long_roi_cnt", rd(ROI_BASE + ROI_CNT));
        log_nv("long_rgb_pc", rd(RGB_BASE + RGB_PC));
        log_nv("long_mot_cnt", rd(MOT_BASE + MOT_CNT));
        log_nv("long_s2mm_sr", rd(S2MM_SR));
        log_str("done_long\n");
        finish(0);
        for (;;) { }
    }

    /* ── phase=9：帧间不停通道（streaming）—— "720p60 到底能不能过 60 fps"────────
     * phase=8 每帧都把两个通道软复位、重新 arm S2MM、帧末再软件停 S2MM。那是**取证接法**
     * （每帧起点确定、好判读），但固定开销被 300 帧各付一次。这里把通道开销摊到一"块"帧上：
     *   · S2MM 整段只 arm 一次，长度 = 帧数×一帧灰度 + 余量（不收满 ⇒ 不 halt ⇒ 不反压）；
     *     2026-10-24：BUILD 里 c_sg_length_width 已从 23 抬到 26（上限 67,108,863 B），
     *     300 帧 = 38,880,000 B 一次装得下。⚠️ 23 位的旧 bitstream 会在这里第 33 帧 halt
     *     （DMAIntErr，见 .tmp/s4/stream_720_a.log），所以这一版固件**必须配 26 位的 bitstream**。
     *   · MM2S 每帧只重写 CR.RS / SA / LEN（不复位通道，只清 IOC 位）；
     *   · 三个 HLS IP 仍每帧写一次 CTRL.ap_start —— 这是它们"一帧一 run"的工作方式，不是开销。
     * 灰度按帧紧密排列落在 STREAM_ADDR（0x10000000，避开 0x0A000000 结果块与 0x0B000000 黄金参考）。
     * 判据：① 300 帧全过；② 三个 IP 的 fid 逐帧 +1；③ 第 1 帧与第 N 帧都与黄金参考逐字节相等。
     */
    if (phase == 9u) {
        const unsigned fb = (unsigned)(GRAY_W * GRAY_H);
        const unsigned sbase = STREAM_ADDR;
        const unsigned chunk_frames = (unsigned)LONG_FRAMES;
        unsigned f, fails = 0u, first_fail = 0u, okf = 0u, arms = 0u;
        unsigned fid0 = rd(ROI_BASE + ROI_FID);
        unsigned t0, t1, k, j;
        log_nv("stream_frames", LONG_FRAMES);
        log_nv("stream_fid0", fid0);
        log_kv("stream_base", sbase);
        log_nv("stream_chunk_frames", chunk_frames);
        /* 通道只在这里复位一次 */
        ch_reset_quiet(MM2S_CR);
        ch_reset_quiet(S2MM_CR);
        wr(MM2S_SR, 0x00007000u);
        wr(S2MM_SR, 0x00007000u);
        t0 = rd(0xF8F00200u);
        j = 0u;
        for (f = 1u; f <= LONG_FRAMES; f++) {
            unsigned bad = 0u;
            if (j == 0u) {
                unsigned rem = LONG_FRAMES - f + 1u;
                unsigned cf = (rem < chunk_frames) ? rem : chunk_frames;
                wr(S2MM_CR, 0x00000000u);            /* 上一块可能还在跑；停掉再重新 arm */
                wr(S2MM_SR, 0x00007000u);
                wr(S2MM_CR, 0x00000001u);
                wr(S2MM_DA, sbase + (f - 1u) * fb);
                wr(S2MM_LEN, cf * fb + SINK_MARGIN); /* LEN 最后写 = 启动 */
                arms++;
            }
            /* HLS kernel 一帧一 run：写 ap_start 会清掉上一帧的 ap_done */
            wr(ROI_BASE + CTRL, 1u);
            wr(RGB_BASE + CTRL, 1u);
            wr(MOT_BASE + CTRL, 1u);
            /* MM2S 不复位，只把 RS 拉一次并重写 SA/LEN（LEN 最后写 = 启动） */
            wr(MM2S_CR, 0x00000000u);
            wr(MM2S_SR, 0x00001000u);
            wr(MM2S_CR, 0x00000001u);
            wr(MM2S_SA, FRAME_ADDR);
            wr(MM2S_LEN, (unsigned)(FRAME_W * FRAME_H * 3u));
            k = 0u;
            while ((rd(MM2S_SR) & 0x1000u) == 0u && k < POLL_MAX) k++;
            if (k >= POLL_MAX) bad = 1u;
            if (bad == 0u) {
                k = 0u;
                while (((rd(ROI_BASE + CTRL) & 0x2u) == 0u ||
                        (rd(RGB_BASE + CTRL) & 0x2u) == 0u ||
                        (rd(MOT_BASE + CTRL) & 0x2u) == 0u) && k < POLL_MAX) k++;
                if (k >= POLL_MAX) bad = 2u;
            }
            if (bad == 0u && (rd(ROI_BASE + ROI_FID) != fid0 + f ||
                              rd(RGB_BASE + RGB_FID) != fid0 + f ||
                              rd(MOT_BASE + MOT_FID) != fid0 + f)) bad = 3u;
            if (bad != 0u) {
                fails++;
                if (first_fail == 0u) {
                    first_fail = f;
                    log_nv("stream_fail_frame", f);
                    log_nv("stream_fail_kind", bad);
                    log_kv("stream_fail_mm2s_sr", rd(MM2S_SR));
                    log_kv("stream_fail_s2mm_sr", rd(S2MM_SR));
                    log_kv("stream_fail_roi_ctrl", rd(ROI_BASE + CTRL));
                    log_kv("stream_fail_rgb_ctrl", rd(RGB_BASE + CTRL));
                    log_kv("stream_fail_mot_ctrl", rd(MOT_BASE + CTRL));
                }
                break;
            }
            okf++;
            j++;
            if (j >= chunk_frames) j = 0u;
        }
        wr(S2MM_CR, 0x00000000u);
        t1 = rd(0xF8F00200u);
        log_nv("stream_ok_frames", okf);
        log_nv("stream_fails", fails);
        log_nv("stream_first_fail", first_fail);
        log_nv("stream_arms", arms);
        log_nv("stream_gt_ticks", t1 - t0);
        log_kv("stream_s2mm_sr", rd(S2MM_SR));
        log_nv("stream_roi_fid", rd(ROI_BASE + ROI_FID));
        log_nv("stream_rgb_fid", rd(RGB_BASE + RGB_FID));
        log_nv("stream_mot_fid", rd(MOT_BASE + MOT_FID));
        log_nv("stream_roi_cnt", rd(ROI_BASE + ROI_CNT));
        log_nv("stream_rgb_pc", rd(RGB_BASE + RGB_PC));
        log_nv("stream_mot_cnt", rd(MOT_BASE + MOT_CNT));
        if (okf > 0u) {
            const unsigned char *gd = (const unsigned char *)GOLD_ADDR;
            const unsigned char *fw = (const unsigned char *)STREAM_ADDR;
            const unsigned char *lw = (const unsigned char *)(STREAM_ADDR + (okf - 1u) * fb);
            unsigned i, bad, first;
            bad = 0u; first = 0xFFFFFFFFu;
            for (i = 0; i < fb; i++) {
                if (fw[i] != gd[i]) { if (first == 0xFFFFFFFFu) first = i; bad++; }
            }
            log_str("stream_cmp_first mismatch="); log_dec(bad);
            log_str(" first_idx="); log_dec(first); log_str("\n");
            bad = 0u; first = 0xFFFFFFFFu;
            for (i = 0; i < fb; i++) {
                if (lw[i] != gd[i]) { if (first == 0xFFFFFFFFu) first = i; bad++; }
            }
            log_str("stream_cmp_last mismatch="); log_dec(bad);
            log_str(" first_idx="); log_dec(first); log_str("\n");
        }
        log_str("done_stream\n");
        finish(0);
        for (;;) { }
    }

    finish(0);
    for (;;) { }
}
