//=============================================================================
// pl_imx219_probe.v  --  VigiLens / 知倦   最小相机存活实验（纯 PL，不进 BD）
//-----------------------------------------------------------------------------
// 目的：照 S1（board/openmv/pl_uart_echo.v）的纪律，用一小块 PL 逻辑回答四问：
//   ① IMX219 排线插对没有？  ② 3.3V 有没有？
//   ③ MCLK 有没有？          ④ SCCB(I2C) 通不通？
// 判据：能读到 IMX219 chip ID —— reg 0x0000 = 0x02，reg 0x0001 = 0x19。
// 结果按 ASCII 打在 115200 8N1 的 UART 上（板上 USB-UART = console，COM11）。
//
// 不进 Block Design、不启 PS、不用 DDR —— 与 S1 完全同一套纪律。
//
// ⚠️ 诚实的边界（读之前先读这段）
//   · 本文件是【新写的】。写下时【尚未仿真、尚未综合、尚未上板】。
//   · 引脚一律不写在这里，全部在 XDC 里给；引脚号必须来自 Mizar-Z7 原理图，不得猜。
//   · IMX219 的 7bit SCCB 地址 0x10 与「16bit 寄存器地址」两条口径，按
//     board/imx219_driver.py 的登记都是 UNVERIFIED。所以本设计**扫全总线 0x00~0x7F**，
//     并且**两种寄存器地址宽度都读一遍**，把"到底哪一种"变成一次实测而不是一个假设。
//   · 本设计【只读不写】：不配置相机、不修改任何寄存器 ⇒ 不可能把相机配坏。
//
// ⚠️ 已知的设计取舍（刻意为之，不是遗漏）
//   · MCLK 用 50 MHz 二分频 = 25 MHz。IMX219 的 INCK 常态是 24 MHz，但**读 chip ID
//     只需要 INCK 在合法范围内、不需要精确**；等真要出图（配 PLL）时再换 MMCM 出精确
//     24 MHz。这里刻意不引入 MMCM —— 少一个时钟域、少一个 lock 信号 = 少一个变量。
//   · SCL 定在 100 kHz（SCCB 保守档）。每个数据位 = 3 个 phase（SET/SCL低 -> HI/SCL高
//     -> LO/SCL低），phase = CLK_HZ/(3*SCL_HZ) = 166 clk @50MHz。
//     这样设计的原因：**SDA 只允许在 SCL 为低时变化**，否则会被从机当成 START/STOP。
//=============================================================================
`timescale 1ns / 1ps

module pl_imx219_probe #(
    parameter integer CLK_HZ     = 50_000_000,  // H16 PL 晶振 50 MHz
    parameter integer BAUD       = 115200,
    parameter integer SCL_HZ     = 100_000,     // SCCB 时钟
    parameter [6:0]   PROBE_ADDR = 7'h10,       // 默认探测地址（driver 登记为 UNVERIFIED）
    parameter integer PWRUP_MS   = 200,         // 上电等待：给相机内部 LDO 起振的时间
    // 0 = 不驱动 mclk，把该脚置高阻。
    //   理由（原理图第 14 页实测）：板上 U19 (50M_OSC) 经 R156 33R 直接驱动
    //   连接器 pin 12 (CAM_CLK)。PL 若同时输出就是两个源对打。
    //   此时相机吃到的是 50 MHz —— IMX219 规格是 6~27 MHz，属超规，
    //   SCCB 未必应答，这本身就是本次实验要回答的问题之一。
    // 1 = 由 PL 驱动 mclk（50 MHz 二分频 = 25 MHz，落在规格内）。
    //   仅在确认 R156 未贴装 / U19 无输出时使用。
    parameter integer DRIVE_MCLK = 0
)(
    input  wire       clk,
    input  wire       rst_n,
    output wire       mclk,        // -> 相机 INCK
    inout  wire       sccb_scl,    // -> 相机 SCL
    inout  wire       sccb_sda,    // <-> 相机 SDA
    output wire       uart_tx,     // -> 板上 USB-UART (console)
    output wire [3:0] led,         // 低电平点亮
    // ---- 调试输出：经 JTAG/ILA 读回，不占板上外设 ----
    output wire [2:0] dbg_sq,      // 定序器状态
    output wire [3:0] dbg_hit_n,   // 扫描命中数
    output wire [7:0] dbg_hit0,    // 第 1 个命中地址（8bit 写地址）
    output wire [7:0] dbg_hit1,    // 第 2 个命中地址 —— 总线上预期是 EEPROM(0xA0)+相机(0x20)
    output wire [7:0] dbg_id0,     // regv16[0] = MODEL_ID_H（16bit 地址读）
    output wire [7:0] dbg_id1,     // regv16[1] = MODEL_ID_L（16bit 地址读）
    output wire [7:0] dbg_id80,    // regv8[0]（8bit 地址读）
    output wire [7:0] dbg_id81     // regv8[1]（8bit 地址读）
);

    //--------------------------------------------------------------------------
    // 0. 常量与消息版式
    //--------------------------------------------------------------------------
    localparam integer DIV   = CLK_HZ / BAUD;            // UART 每 bit 的 clk 数
    localparam integer PHASE = CLK_HZ / (3 * SCL_HZ);    // I2C 每个 phase 的 clk 数
    localparam integer PWRUP = (CLK_HZ / 1000) * PWRUP_MS;

    localparam [7:0] ADDR_W = {PROBE_ADDR, 1'b0};        // 8bit 写地址
    localparam [7:0] ADDR_R = {PROBE_ADDR, 1'b1};        // 8bit 读地址

    localparam integer MSG_LEN = 96;
    localparam [6:0]
        M_CNT   = 7'd25,   // 25..26  命中数（2 位十六进制）
        M_ADDRS = 7'd28,   // 28..51  8 个命中地址，每个 "XX "（3 字节）
        M_D16   = 7'd59,   // 59..67  3 个寄存器，每个 "XX "（3 字节）
        M_D8    = 7'd75;   // 75..83  同上
    localparam [7:0] DUMP_END = 8'd91;

    // 十六进制 -> ASCII（放在最前面，避免"先用后声明"）
    function [7:0] hexchar;
        input [3:0] v;
        begin
            hexchar = (v < 4'd10) ? (8'h30 + {4'd0, v}) : (8'h37 + {4'd0, v});
        end
    endfunction

    //--------------------------------------------------------------------------
    // 1. MCLK：50 MHz 二分频 -> 25 MHz
    //--------------------------------------------------------------------------
    reg mclk_r;
    always @(posedge clk) begin
        if (!rst_n) mclk_r <= 1'b0;
        else        mclk_r <= ~mclk_r;
    end
    assign mclk = DRIVE_MCLK ? mclk_r : 1'bz;

    //--------------------------------------------------------------------------
    // 2. UART 发送（结构与 board/openmv/pl_uart_echo.v 的 TX 一致，那个已综合验证）
    //--------------------------------------------------------------------------
    reg  [15:0] tx_cnt;
    reg  [3:0]  tx_nbit;
    reg  [9:0]  tx_sh;
    reg         tx_busy;
    reg  [7:0]  tx_data;
    reg         tx_start;

    assign uart_tx = tx_busy ? tx_sh[0] : 1'b1;

    always @(posedge clk) begin
        if (!rst_n) begin
            tx_busy <= 1'b0; tx_cnt <= 16'd0; tx_nbit <= 4'd0; tx_sh <= 10'h3FF;
        end else if (!tx_busy) begin
            if (tx_start) begin
                tx_sh   <= {1'b1, tx_data, 1'b0};   // bit0=start, bit1..8=data, bit9=stop
                tx_busy <= 1'b1;
                tx_cnt  <= 16'd0;
                tx_nbit <= 4'd0;
            end
        end else begin
            if (tx_cnt == DIV[15:0] - 16'd1) begin
                tx_cnt <= 16'd0;
                if (tx_nbit == 4'd9) tx_busy <= 1'b0;
                else begin
                    tx_nbit <= tx_nbit + 4'd1;
                    tx_sh   <= {1'b1, tx_sh[9:1]};
                end
            end else begin
                tx_cnt <= tx_cnt + 16'd1;
            end
        end
    end

    //--------------------------------------------------------------------------
    // 3. I2C(SCCB) 位引擎
    //    输出由【状态】组合译码后直接驱动引脚，SDA 只在 SCL 为低的相位入口改变。
    //    每个数据位三态：SET(SCL低,摆 SDA) -> HI(SCL高) -> LO(SCL低,保持)。
    //--------------------------------------------------------------------------
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
    reg         op_ack_r;

    //  I_IDLE 也必须拉低 SCL：START 事务收尾时从 I_ST_LOW 回 I_IDLE，
    //  若这一拍同时释放 SCL 和 SDA，从机看到的就是"SCL 已高、SDA 上升"= 一个假 STOP，
    //  于是它清掉 in_frame，这一帧后面 8 位全都不再采样，自然不会 ACK。
    //  事务中途把 SCL 压在低电平即可；I_ST_SET 不在表里，第一个 START 照样能把 SCL 抬起来。
    wire scl_lo = (ist == I_IDLE)    ||
                  (ist == I_ST_LOW)  || (ist == I_TX_SET)  || (ist == I_TX_LO)  ||
                  (ist == I_ACK_SET) || (ist == I_ACK_LO) || (ist == I_RX_SET) ||
                  (ist == I_RX_LO)   || (ist == I_NK_SET) || (ist == I_NK_LO) ||
                  (ist == I_SP_SET);

    wire sda_lo = ((ist == I_ST_FALL) || (ist == I_ST_LOW) ||
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
        end else begin
            sda_s  <= sccb_sda;          // 引脚三态时靠上拉为高
            sda_s2 <= sda_s;

            if (ist == I_IDLE) begin
                itick <= 16'd0;
                if (op_go) begin
                    ibit    <= 4'd0;
                    ish     <= op_tx;
                    irx     <= 8'd0;
                    ack_n   <= op_nack;
                    sda_bit <= op_tx[7];
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

    //--------------------------------------------------------------------------
    // 4. 消息缓冲（复位时预填文字，定序器只往固定槽位补十六进制）
    //--------------------------------------------------------------------------
    reg [7:0] msg [0:MSG_LEN-1];
    integer mi;
    initial begin
        for (mi = 0; mi < MSG_LEN; mi = mi + 1) msg[mi] = 8'h20;
        msg[ 0]="V"; msg[ 1]="I"; msg[ 2]="G"; msg[ 3]="I"; msg[ 4]="L"; msg[ 5]="E";
        msg[ 6]="N"; msg[ 7]="S"; msg[ 8]=" "; msg[ 9]="C"; msg[10]="A"; msg[11]="M";
        msg[12]=" "; msg[13]="P"; msg[14]="R"; msg[15]="O"; msg[16]="B"; msg[17]="E";
        msg[18]=8'h0D; msg[19]=8'h0A;
        msg[20]="S"; msg[21]="C"; msg[22]="A"; msg[23]="N"; msg[24]=" ";
        msg[52]=8'h0D; msg[53]=8'h0A;
        msg[54]="I"; msg[55]="D"; msg[56]="1"; msg[57]="6"; msg[58]=" ";
        msg[68]=8'h0D; msg[69]=8'h0A;
        msg[70]="I"; msg[71]="D"; msg[72]="8"; msg[73]=" "; msg[74]=" ";
        msg[84]=8'h0D; msg[85]=8'h0A;
        msg[86]="E"; msg[87]="N"; msg[88]="D"; msg[89]=8'h0D; msg[90]=8'h0A;
    end

    //--------------------------------------------------------------------------
    // 5. 顶层定序器
    //--------------------------------------------------------------------------
    localparam [2:0]
        SQ_PWRUP = 3'd0, SQ_SCAN = 3'd1, SQ_RD16 = 3'd2,
        SQ_RD8   = 3'd3, SQ_FILL = 3'd4, SQ_DUMP = 3'd5, SQ_HOLD = 3'd6;

    reg  [2:0]  sq;
    reg  [31:0] sq_cnt;
    reg  [7:0]  scan_a;
    reg  [3:0]  hit_n;
    reg  [7:0]  hit_a [0:15];
    reg  [2:0]  rd_i;
    reg  [3:0]  ustep;
    reg  [7:0]  regv16 [0:2];
    reg  [7:0]  regv8  [0:2];
    reg  [7:0]  dump_i;
    reg         i2c_act;
    reg  [3:0]  fill_i;

    always @(posedge clk) begin
        if (!rst_n) begin
            sq <= SQ_PWRUP; sq_cnt <= 32'd0; scan_a <= 8'd0; hit_n <= 4'd0;
            rd_i <= 3'd0; ustep <= 4'd0; dump_i <= 8'd0; op_go <= 1'b0;
            op <= OP_START; op_tx <= 8'd0; op_nack <= 1'b0; i2c_act <= 1'b0;
            tx_start <= 1'b0; tx_data <= 8'd0; fill_i <= 3'd0;
        end else begin
            op_go    <= 1'b0;
            tx_start <= 1'b0;

            case (sq)
            //---- 上电等待 ----
            SQ_PWRUP: begin
                if (sq_cnt == PWRUP[31:0] - 32'd1) begin
                    sq <= SQ_SCAN; sq_cnt <= 32'd0; scan_a <= 8'd0;
                    hit_n <= 4'd0; ustep <= 4'd0;
                    op <= OP_START; op_go <= 1'b1;      // 起第一个事务
                end else sq_cnt <= sq_cnt + 32'd1;
            end
            //---- 总线扫描 0x00..0x7F：START / TX(addr<<1|W) / STOP ----
            SQ_SCAN: begin
                i2c_act <= 1'b1;
                if (op_done) begin
                    case (ustep)
                    4'd0: begin op <= OP_TX;   op_tx <= {scan_a[6:0], 1'b0};
                                op_nack <= 1'b0; op_go <= 1'b1; ustep <= 4'd1; end
                    4'd1: begin op <= OP_STOP; op_tx <= 8'd0;
                                op_nack <= 1'b0; op_go <= 1'b1; ustep <= 4'd2; end
                    4'd2: begin
                              // op_ack 是刚才那次 TX 的应答（STOP 不会覆盖它）
                              if (op_ack && hit_n != 4'd15) begin
                                  hit_a[hit_n] <= {scan_a[6:0], 1'b0};
                                  hit_n <= hit_n + 4'd1;
                              end
                              if (scan_a == 8'd127) begin
                                  sq <= SQ_RD16; rd_i <= 3'd0; ustep <= 4'd0;
                                  op <= OP_START; op_go <= 1'b1;
                              end else begin
                                  scan_a <= scan_a + 8'd1; ustep <= 4'd0;
                                  op <= OP_START; op_go <= 1'b1;
                              end
                          end
                    default: ustep <= 4'd0;
                    endcase
                end
            end
            //---- 读 3 个寄存器（16bit 寄存器地址）----
            //  START / TX AW / TX hi / TX lo / START / TX AR / RX(NACK) / STOP / 存
            SQ_RD16: begin
                i2c_act <= 1'b1;
                if (op_done) begin
                    ustep <= ustep + 4'd1;
                    case (ustep)
                    4'd0: begin op <= OP_TX;   op_tx <= ADDR_W; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd1: begin op <= OP_TX;   op_tx <= 8'h00;  op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd2: begin op <= OP_TX;   op_tx <= {5'd0, rd_i}; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd3: begin op <= OP_START; op_go <= 1'b1; end
                    4'd4: begin op <= OP_TX;   op_tx <= ADDR_R; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd5: begin op <= OP_RX;   op_nack <= 1'b1; op_go <= 1'b1; end
                    4'd6: begin
                              // ⚠️ RX 刚读完，必须在这一拍就把字节存走：紧接着的 STOP 操作
                              //    一开场就会在 I_IDLE 载入时把 irx 清 0，若等到 ustep7 再取
                              //    op_rx 只会拿到 00（实测 ID16/ID8 全 00 就是这个原因）。
                              regv16[rd_i] <= op_rx;
                              op <= OP_STOP; op_nack <= 1'b0; op_go <= 1'b1;
                          end
                    4'd7: begin
                              ustep <= 4'd0;
                              if (rd_i == 3'd2) begin sq <= SQ_RD8; rd_i <= 3'd0; end
                              else rd_i <= rd_i + 3'd1;
                              // ⚠️ 必须【重新发一个 START】给下一个寄存器的读事务开场。
                              //    实测漏掉这一句会让 op_done 永不再来，定序器死锁在 SQ_RD16。
                              op <= OP_START; op_go <= 1'b1;
                          end
                    default: ustep <= 4'd0;
                    endcase
                end
            end
            //---- 读 3 个寄存器（8bit 寄存器地址）----
            //  START / TX AW / TX lo / START / TX AR / RX(NACK) / STOP / 存
            SQ_RD8: begin
                i2c_act <= 1'b1;
                if (op_done) begin
                    ustep <= ustep + 4'd1;
                    case (ustep)
                    4'd0: begin op <= OP_TX;   op_tx <= ADDR_W; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd1: begin op <= OP_TX;   op_tx <= {5'd0, rd_i}; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd2: begin op <= OP_START; op_go <= 1'b1; end
                    4'd3: begin op <= OP_TX;   op_tx <= ADDR_R; op_nack <= 1'b0; op_go <= 1'b1; end
                    4'd4: begin op <= OP_RX;   op_nack <= 1'b1; op_go <= 1'b1; end
                    4'd5: begin
                              // ⚠️ 同 SQ_RD16：RX 刚读完必须立刻存，STOP 一开场就会清掉 irx。
                              regv8[rd_i] <= op_rx;
                              op <= OP_STOP; op_nack <= 1'b0; op_go <= 1'b1;
                          end
                    4'd6: begin
                              ustep <= 4'd0;
                              if (rd_i == 3'd2) begin sq <= SQ_FILL; fill_i <= 3'd0; i2c_act <= 1'b0; end
                              else rd_i <= rd_i + 3'd1;
                              // ⚠️ 同 SQ_RD16：下一个寄存器的读事务必须重新发 START 开场。
                              op <= OP_START; op_go <= 1'b1;
                          end
                    default: ustep <= 4'd0;
                    endcase
                end
            end
            //---- 把结果写进消息槽（每周期一个，简单可靠）----
            SQ_FILL: begin
                case (fill_i)
                4'd0: msg[25] <= hexchar(hit_n[3:0]);
                4'd1: begin msg[28] <= hexchar(hit_a[0][7:4]); msg[29] <= hexchar(hit_a[0][3:0]); end
                4'd2: begin msg[31] <= hexchar(hit_a[1][7:4]); msg[32] <= hexchar(hit_a[1][3:0]); end
                4'd3: begin msg[34] <= hexchar(hit_a[2][7:4]); msg[35] <= hexchar(hit_a[2][3:0]); end
                4'd4: begin msg[37] <= hexchar(hit_a[3][7:4]); msg[38] <= hexchar(hit_a[3][3:0]); end
                4'd5: begin msg[40] <= hexchar(hit_a[4][7:4]); msg[41] <= hexchar(hit_a[4][3:0]); end
                4'd6: begin msg[43] <= hexchar(hit_a[5][7:4]); msg[44] <= hexchar(hit_a[5][3:0]); end
                4'd7: begin msg[46] <= hexchar(hit_a[6][7:4]); msg[47] <= hexchar(hit_a[6][3:0]); end
                default: begin      // fill_i == 8：最后一个地址 + 六个 ID 字节
                          msg[49] <= hexchar(hit_a[7][7:4]); msg[50] <= hexchar(hit_a[7][3:0]);
                          msg[59] <= hexchar(regv16[0][7:4]); msg[60] <= hexchar(regv16[0][3:0]);
                          msg[62] <= hexchar(regv16[1][7:4]); msg[63] <= hexchar(regv16[1][3:0]);
                          msg[65] <= hexchar(regv16[2][7:4]); msg[66] <= hexchar(regv16[2][3:0]);
                          msg[75] <= hexchar(regv8[0][7:4]);  msg[76] <= hexchar(regv8[0][3:0]);
                          msg[78] <= hexchar(regv8[1][7:4]);  msg[79] <= hexchar(regv8[1][3:0]);
                          msg[81] <= hexchar(regv8[2][7:4]);  msg[82] <= hexchar(regv8[2][3:0]);
                          sq <= SQ_DUMP; dump_i <= 8'd0;
                      end
                endcase
                fill_i <= fill_i + 4'd1;
            end
            //---- 把消息缓冲逐字节打出去 ----
            SQ_DUMP: begin
                if (!tx_busy && !tx_start) begin
                    if (dump_i == DUMP_END) sq <= SQ_HOLD;
                    else begin
                        tx_data  <= msg[dump_i[6:0]];
                        tx_start <= 1'b1;
                        dump_i   <= dump_i + 8'd1;
                    end
                end
            end
            SQ_HOLD: i2c_act <= 1'b0;
            default: sq <= SQ_HOLD;
            endcase
        end
    end

    //--------------------------------------------------------------------------
    // 6. LED（低电平点亮）
    //--------------------------------------------------------------------------
    reg [23:0] hb;
    always @(posedge clk) begin
        if (!rst_n) hb <= 24'd0;
        else        hb <= hb + 24'd1;
    end

    assign led[0] = ~hb[23];              // 心跳（约 1.5 Hz）
    assign led[1] = ~i2c_act;             // I2C 活动
    assign led[2] = ~(hit_n != 4'd0);     // 扫到东西
    assign led[3] = ~(sq == SQ_HOLD);     // 完成

    //--------------------------------------------------------------------------
    // 7. 调试输出（经 JTAG/ILA 读回；板上不接任何东西）
    //    SQ_HOLD 之后这些值就冻结了，所以 ILA 用 -trigger_now 抓当前值即可。
    //--------------------------------------------------------------------------
    assign dbg_sq    = sq;
    assign dbg_hit_n = hit_n;
    assign dbg_hit0  = hit_a[0];     // 第一个命中地址（8bit 写地址：0x20=IMX219, 0xA0=24C32）
    assign dbg_hit1  = hit_a[1];     // 第二个命中地址
    assign dbg_id0   = regv16[0];    // MODEL_ID_H，期望 0x02
    assign dbg_id1   = regv16[1];    // MODEL_ID_L，期望 0x19
    assign dbg_id80  = regv8[0];     // 8bit 地址方式读到的 MODEL_ID_H
    assign dbg_id81  = regv8[1];     // 8bit 地址方式读到的 MODEL_ID_L

endmodule
