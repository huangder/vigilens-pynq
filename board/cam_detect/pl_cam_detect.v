`timescale 1ns / 1ps
//=============================================================================
// pl_cam_detect.v —— FFC 处 MIPI 摄像头「连接检测」探针（纯 PL）
//-----------------------------------------------------------------------------
// 【回答什么问题】按证据强度从低到高：
//   Q1 SCCB 主机自己活着吗？    —— 板载 EEPROM（0x50/0x58）有没有 ACK
//   Q2 总线上都有谁？           —— 0x00~0x7F 全扫，记下命中的 8bit 写地址
//   Q3 相机（7bit 0x10）应答吗？ —— 最强证据：ACK + MODEL_ID=02 19 ⇒ 连上了
//   Q4 相机时钟（CAM_CLK, pin12）到了吗？ —— 实测周期，用来区分
//                                  「排线/相机问题」与「时钟根本没到」
//
// 【为什么是纯 PL】不启 PS、不用 DDR、不进 Block Design：
//   这是本板目前唯一跑得通的路线（board/build_bd.tcl 尚未跑通，见 docs/30 §S3）。
//
// 【纪律】
//   · 只读不写：全程只发 START/地址/寄存器地址/读，**一个写数据都不发**，
//     绝无可能把相机配坏。
//   · 与 board/imx219_probe/ 完全独立：那份是 2026-10-11 的实验归档（有原始
//     ILA 证据），**不许改**。本文件是它的后继检测器，差异见 README。
//   · 判据不靠"看起来对"：仿真里放 3 个从机模型，分别测出
//     「有相机 / 无相机 / 无时钟」三种结论（见 tb_pl_cam_detect.v）。
//
// 【ILA 探针打包】（Vivado BASIC 授权限 5 个 probe，故把字段打进 4 个向量）
//   dbg0[31:0] = { 7'b0, cam_ack, scan_a[7:0], step[3:0], ms[3:0],
//                  mclk_seen, mclk_self, i2c_act, pwr_done, hit_n[3:0] }
//        [31:25]=0  [24]=cam_ack  [23:16]=scan_a  [15:12]=step  [11:8]=ms
//        [7]=mclk_seen [6]=mclk_self [5]=i2c_act [4]=pwr_done [3:0]=hit_n
//   dbg1[63:0] = { rec0[31:0], hit_a[0][7:0], mclk_period[15:0], mclk_npulse[7:0] }
//   dbg2[63:0] = { rec1[31:0], hit_a[1], hit_a[2], hit_a[3], hit_a[4] }
//   dbg3[63:0] = { cam16_rec[31:16], cam8_rec[31:0], hit_a[5], hit_a[6] }
//   recX 是「该设备寄存器 0x00..0x03 的 4 个字节，大端」。
//   ★ 这份布局同时写在 cam_detect_pins.xdc / build_dbg.tcl / check_camera.py，
//     改一处必须改三处（check_camera.py 的 --selftest 会拿本文件核对）。
//
// 【已知不做的】
//   · CAM_GPIO（连接器 pin11）**没有约束**：原理图上的 FPGA ball 号未确认，
//     不许猜。要测它是万用表的事，见 README「人工三根线」。
//   · 不做 MIPI D-PHY 接收：BANK13 是 HR bank，而且 VCCO_13 还没量（docs/31 §9）。
//=============================================================================

module pl_cam_detect #(
    parameter integer CLK_HZ     = 50_000_000,  // 板上 U19 经 H16 给的 PL 时钟
    parameter integer SCL_HZ     = 100_000,     // SCCB 时钟
    parameter integer PWRUP_MS   = 200,         // 上电等待：给相机内部 LDO 起振
    parameter [6:0]   CAM_ADDR   = 7'h10,       // IMX219 的 7bit 地址
    parameter integer DRIVE_MCLK = 0,           // 1 = PL 驱动 CAM_CLK（板上无第二驱动源，见 docs/34 §2）
    parameter integer MCLK_DIV   = 2,           // DRIVE_MCLK=1 时的分频：clk/2 = 25 MHz
    parameter integer MCLK_STATIC = 0,          // ★ 2026-10-20 新增：pin12 静态电平
                                                //   0 = 不用静态（按 DRIVE_MCLK 走：1=25 MHz / 0=高阻）
                                                //   1 = 常低，2 = 常高
                                                //   为什么需要：本模组是 Jetson 向的，Jetson 15-pin
                                                //   根本没引出 MCLK（Vadzo 表：12=RESERVED），所以模组
                                                //   自带晶振；把 25 MHz 硬灌到 pin12 可能正好灌进它的
                                                //   XCLR/PWDN。这一档用来"完全不驱动 pin12"再测一次
                                                //   （高阻 / 常低 / 常高 三种都能选，避免悬空猜电平）
    parameter integer MEAS_N     = 4096,        // 每 MEAS_N 个 cam_clk 周期出一次测量脉冲
    parameter integer CAM_PWR_EN = 1,           // led[3]=H17→R33→pin11(CAM_GPIO) 的电平：1=高，0=低
    parameter integer RESET_MS   = 20           // ★ 2026-10-20 新增：上电先把 pin11 按住 RESET_MS 毫秒
                                                //   （此时 MCLK 已经在跑）再释放，按 IMX219 数据手册
                                                //   的"先 INCK 稳定、后放 XCLR"顺序来；若 pin11 实际是
                                                //   LDO 使能，这段就等价于一次 20 ms 断电重上电，同样安全。
                                                //   2026-10-20 新增：用来一次实验里换极性
                                                //   （不同厂商模组对 pin11 的定义不同：POWER-EN 高有效 / PWDN 高有效）
)(
    input  wire        clk,
    input  wire        rst_n,
    inout  wire        mclk,        // CAM_CLK = 连接器 pin12 = U9（测量它；DRIVE_MCLK=1 才驱动）
    inout  wire        sccb_scl,    // CAM_SCL = pin13 = A20
    inout  wire        sccb_sda,    // CAM_SDA = pin14 = E17
    output wire [3:0]  led,         // 低电平点亮
    output wire [31:0] dbg0,
    output wire [63:0] dbg1,
    output wire [63:0] dbg2,
    output wire [63:0] dbg3
);

    //==========================================================================
    // 1. CAM_CLK：可选驱动 + 真实频率测量
    //==========================================================================
    //  量法：cam_clk 域每 MEAS_N 个周期把 cam_tog 翻一次（1bit CDC，两级同步），
    //        clk 域量两次翻转之间的 clk 数 = mclk_period。
    //        于是 f_cam = MEAS_N * CLK_HZ / mclk_period。
    //        例：50 MHz ⇒ 4096；24 MHz ⇒ 8533；12.5 MHz ⇒ 16384。
    //  ★ 不写死"有/没有"：给的是一个可核算的周期数，频率由上位机算。
    //  ★ mclk_period 饱和在 0xFFFF ⇒ 低于 MEAS_N*50e6/65535 ≈ 3.1 MHz 报"饱和"。
    //--------------------------------------------------------------------------
    reg [15:0] mclk_div;
    reg        mclk_r;
    always @(posedge clk) begin
        if (!rst_n) begin
            mclk_div <= 16'd0;
            mclk_r   <= 1'b0;
        end else if (mclk_div == (MCLK_DIV/2)-1) begin
            mclk_div <= 16'd0;
            mclk_r   <= ~mclk_r;
        end else begin
            mclk_div <= mclk_div + 16'd1;
        end
    end
    // DRIVE_MCLK=0 ⇒ 高阻；=1 ⇒ 由 PL 自己产生 INCK。
    // ⚠️ 2026-10-19 原理图复核更正：CAM_CLK(U9) 全网【只有】本引脚 + 连接器 pin12，
    //    板上 U19 的 50 MHz 经 R156 进的是 PL_CLK_50M(H16)，与 CAM_CLK 无关
    //    （旧注释与 docs/31 §2.3 说"会与 U19 对打"是误读，见 docs/34 §2）。
    assign mclk = (MCLK_STATIC == 1) ? 1'b0 :
                  (MCLK_STATIC == 2) ? 1'b1 :
                  (DRIVE_MCLK ? mclk_r : 1'bz);

    // 量测时钟域：★ 2026-10-19 更正 —— 必须量**引脚本体**，不能量自己的分频器。
    //   旧写法 `DRIVE_MCLK ? mclk_r : mclk` 在驱动模式下量的是 mclk_r，
    //   那是"自证式测量"：计数器当然会跑，但它证明不了 25 MHz 真的出到了 pad
    //   （引脚被外部拉死、或输出级没通，读数一样是正常的）。
    //   改为无条件读引脚：mclk 是 inout，Vivado 会生成带输入支路的 IOBUF，
    //   因此这条测量同时验证「波形出得去」；XDC 的 CLOCK_DEDICATED_ROUTE FALSE
    //   也重新对应到真实的 mclk_IBUF 上。
    //   组合环风险说明：mclk_r 是寄存器输出，引脚回读只进 cam_div 的时钟端，
    //   没有从 mclk 回到 mclk_r 的组合通路。
    // ★ 静态档下 pin12 是常量，没有可量的时钟：此时把计数器冻结（诚实反映"没给时钟"），
    //   否则悬空/常量的引脚回读会被计数器采成随机翻转，读数反而误导。
    wire mclk_meas = (MCLK_STATIC != 0) ? 1'b0 : mclk;

    reg [15:0] cam_div;
    reg        cam_tog;
    initial begin                  // cam_clk 域没有复位，靠上电初值定态
        cam_div = 16'd0;
        cam_tog = 1'b0;
    end
    always @(posedge mclk_meas) begin
        if (cam_div == (MEAS_N - 1)) begin
            cam_div <= 16'd0;
            cam_tog <= ~cam_tog;
        end else begin
            cam_div <= cam_div + 16'd1;
        end
    end

    //  ★ ASYNC_REG：告诉 Vivado 这是一对同步器，自动按 CDC 处理
    //    （比在 XDC 里写 -from/-to 更稳：改个寄存器名约束就失效了）
    (* ASYNC_REG = "TRUE" *) reg cam_tog_s1, cam_tog_s2;
    wire       cam_pulse = cam_tog_s2 ^ cam_tog_s1;
    reg [15:0] cam_win;
    reg [15:0] mclk_period;
    reg [7:0]  mclk_npulse;
    reg        mclk_seen;
    always @(posedge clk) begin
        if (!rst_n) begin
            cam_tog_s1  <= 1'b0;
            cam_tog_s2  <= 1'b0;
            cam_win     <= 16'd0;
            mclk_period <= 16'd0;
            mclk_npulse <= 8'd0;
            mclk_seen   <= 1'b0;
        end else begin
            cam_tog_s1 <= cam_tog;
            cam_tog_s2 <= cam_tog_s1;
            if (cam_pulse) begin
                // ★ 只有【第二次及以后】的脉冲才采信。CAM_CLK 是自己驱动的、
                //   cam_clk 域没有复位，复位放开那一拍可能留下一个假翻转
                //   （TB 切换场景时实测到过 mclk_seen=1 period=1 npulse=1）。
                //   单次毛刺不该被判成"相机时钟到了"。
                if (mclk_npulse >= 8'd1) begin
                    mclk_period <= cam_win;
                    mclk_seen   <= 1'b1;
                end
                cam_win     <= 16'd1;
                mclk_npulse <= mclk_npulse + 8'd1;
            end else if (cam_win != 16'hFFFF) begin
                cam_win <= cam_win + 16'd1;
            end
        end
    end

    //==========================================================================
    // 2. SCCB 位翻转引擎
    //    ★ 本段逐行照搬 board/imx219_probe/pl_imx219_probe.v 里【已仿真通过】的
    //      实现（那次真机上 EEPROM 的 ACK 证明它是对的）。除把 PROBE_ADDR 改名
    //      CAM_ADDR，逻辑一字未动。
    //==========================================================================
    localparam integer PHASE = CLK_HZ / (3 * SCL_HZ);   // I2C 每个 phase 的 clk 数
    localparam integer PWRUP = (CLK_HZ / 1000) * PWRUP_MS;
    localparam integer RST   = (CLK_HZ / 1000) * RESET_MS;   // pin11 按住时长（MCLK 已在跑）

    localparam [7:0] ADDR_W = {CAM_ADDR, 1'b0};         // 8bit 写地址
    localparam [7:0] ADDR_R = {CAM_ADDR, 1'b1};         // 8bit 读地址

    localparam [1:0] OP_START = 2'd0, OP_TX = 2'd1, OP_RX = 2'd2, OP_STOP = 2'd3;

    reg        op_go;
    reg  [1:0] op;
    reg  [7:0] op_tx;
    reg        op_nack;      // RX 后：1 = 回 NACK，0 = 回 ACK
    wire       op_done;      // 【单周期脉冲】
    wire [7:0] op_rx;
    wire       op_ack;       // TX 后从机给的 ACK（1 = 有应答）

    localparam [4:0]
        I_IDLE    = 5'd0,
        I_ST_SET  = 5'd1,   I_ST_FALL = 5'd2,  I_ST_LOW  = 5'd3,
        I_TX_SET  = 5'd4,   I_TX_HI   = 5'd5,  I_TX_LO   = 5'd6,
        I_ACK_SET = 5'd7,   I_ACK_HI  = 5'd8,  I_ACK_LO  = 5'd9,
        I_RX_SET  = 5'd10,  I_RX_HI   = 5'd11, I_RX_LO   = 5'd12,
        I_NK_SET  = 5'd13,  I_NK_HI   = 5'd14, I_NK_LO   = 5'd15,
        I_SP_SET  = 5'd16,  I_SP_HI   = 5'd17, I_SP_REL  = 5'd18,
        I_DONE    = 5'd19;

    reg  [4:0]  ist;
    reg  [15:0] itick;
    reg  [3:0]  ibit;
    reg  [7:0]  ish;
    reg  [7:0]  irx;
    reg         sda_bit;
    reg         ack_n;
    reg         sda_s, sda_s2;      // SDA 输入两级同步
    reg         scl_s, scl_s2;      // SCL 输入两级同步（2026-10-20 新增：总线负载测量要用）
    reg         force_lo;           // 2026-10-20 新增：STEP_RISE 期间把 SCL/SDA 一起按住
    reg         meas_release;       // 2026-10-20 新增：STEP_RISE 释放相——此时要屏蔽
                                    //   `ist==I_IDLE ⇒ SCL 拉低` 这条，否则 SCL 永远起不来
    reg         op_ack_r;

    //  I_IDLE 也必须拉低 SCL：START 事务收尾时从 I_ST_LOW 回 I_IDLE，
    //  若这一拍同时释放 SCL 和 SDA，从机看到的就是"SCL 已高、SDA 上升"= 一个假 STOP。
    wire scl_lo = force_lo ||
                  (!meas_release && (ist == I_IDLE)) ||
                  (ist == I_ST_LOW)  || (ist == I_TX_SET)  || (ist == I_TX_LO)  ||
                  (ist == I_ACK_SET) || (ist == I_ACK_LO) || (ist == I_RX_SET) ||
                  (ist == I_RX_LO)   || (ist == I_NK_SET) || (ist == I_NK_LO) ||
                  (ist == I_SP_SET);

    wire sda_lo = force_lo ? 1'b1 :
                  ((ist == I_ST_FALL) || (ist == I_ST_LOW) ||
                   (ist == I_SP_SET)  || (ist == I_SP_HI)) ? 1'b1 :
                  ((ist == I_TX_SET) || (ist == I_TX_HI) || (ist == I_TX_LO)) ? ~sda_bit :
                  ((ist == I_NK_SET) || (ist == I_NK_HI)) ? ack_n : 1'b0;

    assign sccb_scl = scl_lo ? 1'b0 : 1'bz;
    assign sccb_sda = sda_lo ? 1'b0 : 1'bz;

    wire clk_tick = (itick == PHASE[15:0] - 16'd1);

    reg [4:0] ist_nxt;
    always @(*) begin
        case (ist)
            I_IDLE   : ist_nxt = op_go ? ((op == OP_START) ? I_ST_SET :
                                          (op == OP_TX)    ? I_TX_SET :
                                          (op == OP_RX)    ? I_RX_SET : I_SP_SET) : I_IDLE;
            I_ST_SET : ist_nxt = I_ST_FALL;
            I_ST_FALL: ist_nxt = I_ST_LOW;
            // ⚠️ 终止态【直接】回 I_IDLE，不经 I_DONE：
            //    否则 op_go 的单周期脉冲会在 I_DONE 的 166 个周期里丢失，定序器卡死。
            I_ST_LOW : ist_nxt = I_IDLE;
            I_TX_SET : ist_nxt = I_TX_HI;
            I_TX_HI  : ist_nxt = I_TX_LO;
            I_TX_LO  : ist_nxt = (ibit == 4'd7) ? I_ACK_SET : I_TX_SET;
            I_ACK_SET: ist_nxt = I_ACK_HI;
            I_ACK_HI : ist_nxt = I_ACK_LO;
            I_ACK_LO : ist_nxt = I_IDLE;
            I_RX_SET : ist_nxt = I_RX_HI;
            I_RX_HI  : ist_nxt = I_RX_LO;
            // ibit 在 RX_HI 末尾已自增，所以第 8 位读完时这里是 8 而不是 7
            I_RX_LO  : ist_nxt = (ibit == 4'd8) ? I_NK_SET : I_RX_SET;
            I_NK_SET : ist_nxt = I_NK_HI;
            I_NK_HI  : ist_nxt = I_NK_LO;
            I_NK_LO  : ist_nxt = I_IDLE;
            I_SP_SET : ist_nxt = I_SP_HI;
            I_SP_HI  : ist_nxt = I_SP_REL;
            I_SP_REL : ist_nxt = I_IDLE;
            default  : ist_nxt = I_IDLE;
        endcase
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            ist <= I_IDLE; itick <= 16'd0; ibit <= 4'd0; ish <= 8'd0; irx <= 8'd0;
            sda_bit <= 1'b1; ack_n <= 1'b0; sda_s <= 1'b1; sda_s2 <= 1'b1; op_ack_r <= 1'b0;
            scl_s <= 1'b1; scl_s2 <= 1'b1;
            // ⚠️ force_lo / meas_release **不能**在这里复位：它们由定序器那个 always 块驱动，
            //    两处都赋值会被综合成 multi-driven net（Vivado 报 CRITICAL WARNING
            //    [Synth 8-6859] multi-driven net on pin Q ... 2nd driver pin 'GND'，
            //    2026-10-20 实测）。复位统一放在定序器的复位分支里。
        end else begin
            sda_s  <= sccb_sda;          // 引脚三态时靠上拉为高
            sda_s2 <= sda_s;
            scl_s  <= sccb_scl;
            scl_s2 <= scl_s;

            if (ist == I_IDLE) begin
                itick <= 16'd0;
                if (op_go) begin
                    ibit    <= 4'd0;
                    ish     <= op_tx;
                    irx     <= 8'd0;
                    ack_n   <= op_nack;
                    sda_bit <= op_tx[7];
                    // ⚠️ 每个新事务都要清零：START/STOP 根本没有 ACK 相，
                    //    不清零的话定序器读到的是【上一个字节】的陈旧 ACK，
                    //    于是"记录站点"那一步会把不存在/不应答的站点判成应答。
                    op_ack_r <= 1'b0;
                    ist     <= ist_nxt;
                end
            end else if (clk_tick) begin
                itick <= 16'd0;
                // ---- TX：在 SET 相入口推进移位（此刻 SCL 前后都是低）----
                if (ist_nxt == I_TX_SET && ist == I_TX_LO) begin
                    ish     <= {ish[6:0], 1'b0};
                    sda_bit <= ish[6];
                    ibit    <= ibit + 4'd1;
                end
                // ---- RX：在 HI 相【末尾】采样（此刻 SCL 为高、数据已建立）----
                if (ist == I_RX_HI) begin
                    irx  <= {irx[6:0], sda_s2};
                    ibit <= ibit + 4'd1;
                end
                // ---- ACK 采样也在 HI 相末尾 ----
                if (ist == I_ACK_HI) op_ack_r <= ~sda_s2;   // 从机拉低 = ACK
                ist <= ist_nxt;
            end else begin
                itick <= itick + 16'd1;
            end
        end
    end

    // op_done：只在终止态的最后一个周期出【一个脉冲】
    reg op_done_r;
    always @(posedge clk) begin
        if (!rst_n) op_done_r <= 1'b0;
        else op_done_r <= clk_tick && ((ist == I_ST_LOW) || (ist == I_ACK_LO) ||
                                       (ist == I_NK_LO) || (ist == I_SP_REL));
    end
    assign op_done = op_done_r;
    assign op_rx   = irx;      // irx 一直保持到下一次 RX 操作，定序器读它时是稳定的
    assign op_ack  = op_ack_r;

    //==========================================================================
    // 3. 定序器：STEP_* 主步骤 + ms 微步
    //==========================================================================
    localparam [3:0]
        STEP_IDLE  = 4'd0,
        STEP_PWRUP = 4'd1,
        STEP_SCAN  = 4'd2,
        STEP_REC0  = 4'd3,   // 命中槽 0 的 0x00..0x03（16bit 地址方式）
        STEP_REC1  = 4'd4,   // 命中槽 1 的 0x00..0x03
        STEP_CAM16 = 4'd5,   // 相机 7bit 0x10 的 0x0000..0x0003（16bit 地址方式）
        STEP_CAM8  = 4'd6,   // 相机 7bit 0x10 的 0x00..0x03（8bit 地址方式）
        STEP_DONE  = 4'd7,
        // ★ 2026-10-20 新增：拔掉/插上模组时 SCL/SDA 的**上升时间**会变（4.7k 上拉
        //   × 总电容），用 50 MHz 采样去数"释放后多少拍才被采成高"。不碰总线事务，
        //   纯被动测量 ⇒ 用来回答"模组到底有没有摸到 I2C 两根线上"。
        STEP_RISE  = 4'd8;

    localparam [1:0] DEST_REC0 = 2'd0, DEST_REC1 = 2'd1, DEST_C16 = 2'd2, DEST_C8 = 2'd3;

    reg [3:0]  step;
    reg [3:0]  ms;
    reg [7:0]  scan_a;
    reg [4:0]  hit_n;
    reg [7:0]  hit_a [0:15];
    reg [1:0]  rec_i;
    reg [31:0] pwr_cnt;
    reg        pwr_en;          // ★ 0 = pin11 被按住（复位/断电），1 = 已释放
    reg [31:0] rec0, rec1, cam16_rec, cam8_rec;
    reg [15:0] rc_cnt;          // STEP_RISE 的相位计数
    reg [7:0]  rise_scl;        // 释放后 SCL 仍被采成低的拍数（越小 = 总线电容越小）
    reg [7:0]  rise_sda;        // 同上，SDA
    localparam integer RISE_HOLD = (CLK_HZ / 100000);   // 按住 10 µs（50 MHz ⇒ 500 拍）
    localparam integer RISE_WIN  = (CLK_HZ / 50000);    // 观察窗 20 µs（50 MHz ⇒ 1000 拍）
    reg        cam_ack;
    reg [6:0]  tgt_addr;
    reg [1:0]  dest;

    wire running = (step != STEP_IDLE) && (step != STEP_DONE);

    // ---- 微步数（含 START/STOP）：扫描 3 拍（0..2）、8bit 地址方式 7 拍（0..6）、
    //      16bit 地址方式 8 拍（0..7）----------------------------------------------
    wire [3:0] MS_LAST = (step == STEP_SCAN) ? 4'd2 :
                         (step == STEP_CAM8) ? 4'd6 : 4'd7;

    // ---- 转移与配置（纯函数，无副作用）----------------------------------------
    function [3:0] next_step_of;
        input [3:0] s;
        input [4:0] hn;
        begin
            case (s)
                STEP_SCAN : next_step_of = (hn >= 5'd1) ? STEP_REC0 : STEP_CAM16;
                STEP_REC0 : next_step_of = (hn >= 5'd2) ? STEP_REC1 : STEP_CAM16;
                STEP_REC1 : next_step_of = STEP_CAM16;
                STEP_CAM16: next_step_of = STEP_CAM8;
                STEP_CAM8 : next_step_of = STEP_RISE;
                default   : next_step_of = STEP_DONE;
            endcase
        end
    endfunction

    function [1:0] dest_of;
        input [3:0] s;
        begin
            case (s)
                STEP_REC0 : dest_of = DEST_REC0;
                STEP_REC1 : dest_of = DEST_REC1;
                STEP_CAM8 : dest_of = DEST_C8;
                default   : dest_of = DEST_C16;
            endcase
        end
    endfunction

    function [6:0] addr_of;
        input [3:0] s;
        input [7:0] h0;
        input [7:0] h1;
        begin
            case (s)
                STEP_REC0 : addr_of = h0[7:1];
                STEP_REC1 : addr_of = h1[7:1];
                default   : addr_of = CAM_ADDR;
            endcase
        end
    endfunction

    // 事务运行器的应答信号：**必须先声明**，因为 hn_eff 要用它。
    //  （xvlog 报错实录：ERROR: [VRFC 10-3380] identifier 't_ack' is used before
    //    its declaration —— 原文声明写在下面的运行器里。Verilog 的 wire 不能在
    //    使用之后才声明，故把这两行提前到这里。）
    reg  t_ack_r;
    wire t_ack = t_ack_r;

    // 扫描那一步的 hit_n 还要算上本拍刚 ACK 的那个地址（非阻塞赋值的时滞）
    wire [4:0] hn_eff = ((step == STEP_SCAN) && t_ack && (hit_n < 5'd15))
                        ? (hit_n + 5'd1) : hit_n;

    // ---- 本微步要发的 op（组合）------------------------------------------------
    reg [1:0] req_op;
    reg [7:0] req_tx;
    reg       req_nack;
    reg       req_valid;
    always @(*) begin
        req_valid = 1'b0;
        req_op    = OP_TX;
        req_tx    = 8'h00;
        req_nack  = 1'b0;
        if (running) begin
            case (step)
                // 扫描：每次探测是一个完整的最小 I2C 事务 = START + 写地址字节 + STOP。
                // ⚠️ START 绝不能省：从机只在「SCL 为高时 SDA 下降」才认 START，
                //    没有它任何器件都不会应答（首轮仿真 17 项 FAIL 的根因就是这个）。
                STEP_SCAN : begin
                    case (ms)
                        4'd0: begin req_valid = 1'b1; req_op = OP_START; end
                        4'd1: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {scan_a[6:0], 1'b0}; end
                        4'd2: begin req_valid = 1'b1; req_op = OP_STOP; end
                        default: ;
                    endcase
                end
                STEP_REC0, STEP_REC1, STEP_CAM16 : begin
                    case (ms)
                        4'd0: begin req_valid = 1'b1; req_op = OP_START; end
                        4'd1: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {tgt_addr, 1'b0}; end
                        4'd2: begin req_valid = 1'b1; req_op = OP_TX; req_tx = 8'h00; end
                        4'd3: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {6'd0, rec_i}; end
                        4'd4: begin req_valid = 1'b1; req_op = OP_START; end
                        4'd5: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {tgt_addr, 1'b1}; end
                        // ⚠️ 极性：引擎里 sda_lo = ack_n，即 op_nack=1 会把 SDA 拉低 = ACK。
                        //    只读一个字节、随后就 STOP，这里必须是 NACK ⇒ op_nack=0（释放 SDA）。
                        4'd6: begin req_valid = 1'b1; req_op = OP_RX; req_nack = 1'b0; end
                        4'd7: begin req_valid = 1'b1; req_op = OP_STOP; end
                        default: ;
                    endcase
                end
                STEP_CAM8 : begin
                    case (ms)
                        4'd0: begin req_valid = 1'b1; req_op = OP_START; end
                        4'd1: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {tgt_addr, 1'b0}; end
                        4'd2: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {6'd0, rec_i}; end
                        4'd3: begin req_valid = 1'b1; req_op = OP_START; end
                        4'd4: begin req_valid = 1'b1; req_op = OP_TX; req_tx = {tgt_addr, 1'b1}; end
                        4'd5: begin req_valid = 1'b1; req_op = OP_RX; req_nack = 1'b0; end
                        4'd6: begin req_valid = 1'b1; req_op = OP_STOP; end
                        default: ;
                    endcase
                end
                default: ;
            endcase
        end
    end

    // ---- 记录：把读回的字节移进目标寄存器（大端：第 0 个字节落在 [31:24]）----
    task do_store;
        input [7:0] b;
        begin
            case (dest)
                DEST_REC0: rec0      <= {rec0[23:0],      b};
                DEST_REC1: rec1      <= {rec1[23:0],      b};
                DEST_C16 : cam16_rec <= {cam16_rec[23:0], b};
                default  : cam8_rec  <= {cam8_rec[23:0],  b};
            endcase
        end
    endtask

    // ---- 事务运行器：把「一个 op」跑完再交给定序器 -----------------------------
    reg        t_busy;
    reg [1:0]  t_op;
    reg [7:0]  t_tx;
    reg        t_nack;
    reg        t_done;
    reg [7:0]  t_rx;
    reg        t_launched;
    // ★ 「本次事务读到的那一个字节」必须单独锁存：
    //   t_rx 每完成一个 op 就被改写一次，而记录步的最后一步是 STOP 事务，
    //   引擎在 `if (op_go)` 里会把 irx 清零 ⇒ STOP 完成时 t_rx 变成 0x00，
    //   定序器若在 ms==MS_LAST（STOP 那一拍）才 do_store(t_rx) 就只会写进 0。
    //   2026-10-12 实测：RX 那一拍 t_rx=02（真实值，正确），下一拍 STOP 就变 00。
    reg [7:0]  rx_hold;

    always @(posedge clk) begin
        if (!rst_n) begin
            op_go <= 1'b0; t_busy <= 1'b0; t_done <= 1'b0;
            t_op <= 2'd0; t_tx <= 8'd0; t_nack <= 1'b0;
            t_rx <= 8'd0; rx_hold <= 8'd0; t_ack_r <= 1'b0; t_launched <= 1'b0;
        end else begin
            t_done <= 1'b0;
            if (!t_busy) begin
                // ★ `!t_done` 是必须的：t_done 那一拍定序器**还没**推进 ms，
                //   req_op 仍是上一步的 op。若此刻就发新 op，就会把上一步的 op
                //   再发一遍，并且让"op 的应答"与"读它的 ms"整体错开一拍 ——
                //   2026-10-12 实测后果：从机明明 ACK 了 0xA0（sda=0、op_ack=1），
                //   但这个 ACK 落在 ms=2 那一拍上，被 ms==1 的判据丢掉 ⇒ hit_n=0。
                if (req_valid && !t_launched && !t_done) begin
                    op         <= req_op;
                    op_tx      <= req_tx;
                    op_nack    <= req_nack;
                    op_go      <= 1'b1;
                    t_busy     <= 1'b1;
                    t_launched <= 1'b1;
                end else begin
                    op_go <= 1'b0;
                end
            end else begin
                op_go <= 1'b0;
                if (op_done) begin
                    t_busy     <= 1'b0;
                    t_done     <= 1'b1;
                    t_rx       <= op_rx;
                    // 只有 RX 事务的读回值才有效，此刻 irx 还没被下一个 op 清零
                    if (op == OP_RX) rx_hold <= op_rx;
                    t_ack_r    <= op_ack;
                    t_launched <= 1'b0;
                end
            end
        end
    end

    // ---- 主步骤机 --------------------------------------------------------------
    integer k;
    always @(posedge clk) begin
        if (!rst_n) begin
            step      <= STEP_IDLE;
            ms        <= 4'd0;
            scan_a    <= 8'd0;
            hit_n     <= 5'd0;
            rec_i     <= 2'd0;
            pwr_cnt   <= 32'd0;
            rec0      <= 32'd0;
            rec1      <= 32'd0;
            cam16_rec <= 32'd0;
            cam8_rec  <= 32'd0;
            cam_ack   <= 1'b0;
            pwr_en    <= 1'b0;
            tgt_addr  <= 7'd0;
            dest      <= 2'd0;
            rc_cnt    <= 16'd0;
            rise_scl  <= 8'd0;
            rise_sda  <= 8'd0;
            force_lo     <= 1'b0;
            meas_release <= 1'b0;
            for (k = 0; k < 16; k = k + 1) hit_a[k] <= 8'd0;
        end else if (step == STEP_IDLE) begin
            // 上电即自动开始（不用外部 start）
            step    <= STEP_PWRUP;
            pwr_cnt <= 32'd0;
            ms      <= 4'd0;
        end else if (step == STEP_PWRUP) begin
            // ⚠️ PWRUP 不涉及总线，等不到 t_done，必须单独一个分支。
            //    （第一版把它写在 `else if (t_done)` 里 ⇒ pwr_cnt 永不自增 ⇒ 卡死。）
            // ★ 2026-10-20：两段式上电顺序 ——
            //   [0, RST)              pin11 按住（低），此时 MCLK 已在跑 ⇒ 满足
            //                         "INCK 稳定后再释放 XCLR" 的数据手册上电顺序；
            //   [RST, RST+PWRUP)      pin11 释放，等相机内部 LDO/PLL 起振；
            //   RST+PWRUP 之后       开始扫总线。
            if (pwr_cnt < RST) begin
                pwr_en  <= 1'b0;
                pwr_cnt <= pwr_cnt + 32'd1;
            end else if (pwr_cnt < RST + PWRUP) begin
                pwr_en  <= 1'b1;
                pwr_cnt <= pwr_cnt + 32'd1;
            end else begin
                step   <= STEP_SCAN;
                scan_a <= 8'd0;
                ms     <= 4'd0;
            end
        end else if (step == STEP_RISE) begin
            // ★ 两相：先按住（force_lo=1）→ 再释放，并数"释放后还被采成低"的拍数。
            //   释放后 4.7k 上拉经总线电容充电，到 VIH≈1.65V 约需 0.7·R·C；
            //   50 MHz 采样 ⇒ 1 拍 = 20 ns，能区分"~10 拍（光板）"与"~25 拍（接了模组/长排线）"。
            //   注意 scl_s2/sda_s2 各带 2 拍同步延迟，两条线一样，比较时互相抵消。
            if (rc_cnt == 16'd0) begin
                rise_scl <= 8'd0;
                rise_sda <= 8'd0;
                force_lo <= 1'b1;
                meas_release <= 1'b0;
                rc_cnt   <= 16'd1;
            end else if (rc_cnt < RISE_HOLD) begin
                force_lo <= 1'b1;
                meas_release <= 1'b0;
                rc_cnt   <= rc_cnt + 16'd1;
            end else if (rc_cnt < RISE_HOLD + RISE_WIN) begin
                force_lo <= 1'b0;
                meas_release <= 1'b1;
                // 只为「第一次变高」计数：一旦采成高就不再自增
                if (!scl_s2 && (rise_scl != 8'hFF)) rise_scl <= rise_scl + 8'd1;
                if (!sda_s2 && (rise_sda != 8'hFF)) rise_sda <= rise_sda + 8'd1;
                rc_cnt   <= rc_cnt + 16'd1;
            end else begin
                force_lo <= 1'b0;
                meas_release <= 1'b0;
                step     <= STEP_DONE;
                // 测量值放 hit_a[5]/hit_a[6]（dbg3 的低 16 位）：
                // 一路正常时命中站点只有 2 个，这两槽本来空着；而 cam16_rec/cam8_rec
                // 要保留"型号寄存器=0"的语义，**不能**借来放测量值。
                hit_a[5] <= rise_scl;
                hit_a[6] <= rise_sda;
            end
        end else if (t_done) begin
            case (step)
            //----------------------------------------------------------------
            // 全总线扫描：每个地址发一个「写地址」字节，看有没有 ACK
            STEP_SCAN: begin
                // ★ 只有 ms==1（真正发地址字节的那一拍）的 ACK 才算数
                if (ms == 4'd1) begin
                    if (t_ack) begin
                        if (hit_n < 5'd16) hit_a[hit_n[3:0]] <= {scan_a[6:0], 1'b0};
                        if (hit_n < 5'd15) hit_n <= hit_n + 5'd1;
                        if ({scan_a[6:0], 1'b0} == ADDR_W) cam_ack <= 1'b1;
                    end
                end
                if (ms == MS_LAST) begin
                    // 本站（START + 写地址 + STOP）跑完，换下一个地址
                    if (scan_a == 8'd127) begin
                        step <= next_step_of(STEP_SCAN, hn_eff);
                        dest     <= dest_of(next_step_of(STEP_SCAN, hn_eff));
                        tgt_addr <= addr_of(next_step_of(STEP_SCAN, hn_eff), hit_a[0], hit_a[1]);
                        ms    <= 4'd0;
                        rec_i <= 2'd0;
                    end else begin
                        scan_a <= scan_a + 8'd1;
                        ms     <= 4'd0;
                    end
                end else begin
                    ms <= ms + 4'd1;
                end
            end
            //----------------------------------------------------------------
            // 记录某站的 0x00..0x03
            STEP_REC0, STEP_REC1, STEP_CAM16, STEP_CAM8: begin
                // ★ ms==1 是"发设备写地址"那一拍，它的 ACK 才代表这个站点在不在
                if ((ms == 4'd1) && !t_ack) begin
                    // 目标不应答 ⇒ 跳过本站（读取口径不对时就是这个下场）
                    step <= next_step_of(step, hit_n);
                    dest     <= dest_of(next_step_of(step, hit_n));
                    tgt_addr <= addr_of(next_step_of(step, hit_n), hit_a[0], hit_a[1]);
                    ms    <= 4'd0;
                    rec_i <= 2'd0;
                end else begin
                    // 相机地址应答了（**只对相机那两站**；EEPROM 的 ACK 不算）
                    if ((ms == 4'd1) && ((step == STEP_CAM16) || (step == STEP_CAM8)))
                        cam_ack <= 1'b1;
                    if (ms == MS_LAST) begin
                        do_store(rx_hold);   // ★ 用 RX 那一拍锁下的字节，不用 t_rx（STOP 已把它清成 0）
                        if (rec_i == 2'd3) begin
                            step <= next_step_of(step, hit_n);
                            dest     <= dest_of(next_step_of(step, hit_n));
                            tgt_addr <= addr_of(next_step_of(step, hit_n), hit_a[0], hit_a[1]);
                            ms    <= 4'd0;
                            rec_i <= 2'd0;
                        end else begin
                            rec_i <= rec_i + 2'd1;
                            ms    <= 4'd0;
                        end
                    end else begin
                        ms <= ms + 4'd1;
                    end
                end
            end
            //----------------------------------------------------------------
            default: ;
            endcase
        end
    end

    //==========================================================================
    // 4. 状态/调试输出
    //==========================================================================
    wire i2c_act  = (ist != I_IDLE) || t_busy;
    wire pwr_done = pwr_en && (step != STEP_IDLE);

    assign dbg0 = { 7'b0, cam_ack, scan_a, step, ms,
                    mclk_seen, (DRIVE_MCLK != 0), i2c_act, pwr_done, hit_n[3:0] };
    assign dbg1 = { rec0, hit_a[0], mclk_period, mclk_npulse };
    assign dbg2 = { rec1, hit_a[1], hit_a[2], hit_a[3], hit_a[4] };
    assign dbg3 = { cam16_rec[31:16], cam8_rec, hit_a[5], hit_a[6] };

    //==========================================================================
    // 5. LED / CAM_GPIO：不用上位机也能看一眼（低电平点亮）
    //----------------------------------------------------------------------------
    // ★ 2026-10-19 关键改动：led[3] = H17 = PL_LED4，经 **R33(0R, 默认不贴)**
    //   接到相机座 J11 的 pin11 = CAM_GPIO。树莓派官方 15-pin 表与 Arducam
    //   Jetson 版 IMX219 机械图都把 pin11 定义为 **POWER-EN / CAM_IO0**，且
    //   树莓派文档明说「Typically, CAM_IO0 is used as an active-high power
    //   enable.」⇒ 这一脚必须**常高**，模组的传感器才会上电。
    //   旧写法 `~(step == STEP_DONE)` 是反的（扫完反而拉低 = 断电），已改常高。
    //   ⚠️ 光改代码不够：R33 未贴时 H17 与 pin11 之间没有电气通路，
    //      必须补焊/短接 R33，这一脚才真的能到连接器。
    //==========================================================================
    reg [23:0] hb;
    always @(posedge clk) begin
        if (!rst_n) hb <= 24'd0;
        else hb <= hb + 24'd1;
    end
    assign led[0] = ~hb[23];                    // 心跳 ~1.5 Hz
    assign led[1] = ~i2c_act;                   // SCCB 正在活动
    assign led[2] = ~cam_ack;                   // ★ 相机地址应答了（最要紧的一颗）
    assign led[3] = (CAM_PWR_EN != 0) ? pwr_en : ~pwr_en;
                                                // ★ CAM_GPIO(pin11)：由 pwr_en 按 RST→释放 两段式驱动；
                                                //   极性由 CAM_PWR_EN 选（默认 1=高=使能）

endmodule
