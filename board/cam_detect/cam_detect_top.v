// =============================================================================
//  cam_detect_top.v —— 「FFC 处的 MIPI 摄像头到底连上没有」的检测器（纯 PL）
//
//  回答的问题（分级，不是一句话）：
//    A. PL 的 SCCB 主机 + 板上这一段总线好不好？ —— 板载 EEPROM 有没有 ACK
//    B. 相机那一段（排线 + 模块）有没有东西在应答？ —— 相机类地址有没有 ACK
//    C. 应答的是不是 IMX219？ —— MODEL_ID 是不是 0x02 0x19（两种寄存器地址宽度各读一遍）
//    D. 相机拿到时钟了吗？ —— CAM_CLK(pin12/U9) 上实测频率，这是 docs/31 头号未排除病因
//
//  与 board/imx219_probe/pl_imx219_probe.v（2026-10-11 归档）的关系：
//    · 那份只报「扫到几个地址 + 头两个命中地址 + MODEL_ID」，0x50/0x58 分不清是谁，
//      也完全没测 MCLK —— 所以上一轮的负面结论**无法区分**「没插好」「没供电」「没时钟」。
//    · 本设计把四件事一次测完，并且诊断量全部收进【5 个 ILA 探针】：
//        Vivado BASIC 授权下 ILA 最多 5 个 probe（[Chipscope 16-620]），所以位宽换探针数。
//    · 本设计**绝不驱动 CAM_CLK**（cam_clk 是纯 input），所以它只能回答"板外的谁有没有给时钟"。
//      ⚠️ 2026-10-19 原理图复核更正：板上 U19 50M_OSC 经 R156 进的是 PL_CLK_50M(H16)，
//      **不驱动** pin12 CAM_CLK；CAM_CLK 全网只有 FPGA 球 U9 与连接器，因此想给相机时钟
//      只能靠 PL 驱动（见 pl_cam_detect_dbg.v 的 DRIVE_MCLK_SEL=1 与 docs/34 §2）。
//
//  只读不写：全流程没有任何「写寄存器」的事务（不碰板上 24C32 的内容）。
//  参考：docs/31（上一轮上板记录）、board/imx219_driver.py（SCCB 协议层）
// =============================================================================
`timescale 1ns / 1ps

module cam_detect_top #(
    parameter integer CLK_HZ    = 50_000_000,  // H16 PL 晶振 50 MHz
    parameter integer BAUD      = 115200,
    parameter integer SCL_HZ    = 50_000,      // SCCB 时钟：比上一轮 100k 更慢，飞线更宽容
    parameter integer PWRUP_MS  = 500,         // 上电等待（相机内部 LDO/晶振起振）
    parameter integer TXGAP_MS  = 50,          // UART 两串报文的间隔
    parameter integer MCLK_MEAS = 1            // 0 = 不综合 MCLK 测量（万一 P&R 不认 BUFG）
)(
    input  wire        clk,          // H16
    input  wire        rst_n,        // R19 PL_KEY1（按下为低）
    input  wire        cam_clk,      // U9 = CAM_CLK = 连接器 pin12，**只输入**
    inout  wire        sccb_scl,     // A20 = 连接器 pin13
    inout  wire        sccb_sda,     // E17 = 连接器 pin14
    output wire        uart_tx,      // H15 = JP2 pin7（裸 GPIO，主机够不着；有 USB-TTL 才用得上）
    output wire [3:0]  led,          // G14/C20/B20/H17，低电平点亮
    // ---- ILA 探针（恰好 5 个；位段定义见文件末尾 assign 与 README）------------
    output wire [63:0] dbg_bus_lo,   // [i] = 7bit 地址 i(0x00~0x3F) 有 ACK
    output wire [63:0] dbg_bus_hi,   // [i] = 7bit 地址 i+64(0x40~0x7F) 有 ACK
    output wire [63:0] dbg_status,   // 状态/命中数/相机地址/MODEL_ID
    output wire [63:0] dbg_dump,     // EEPROM(0x50/0x58) 前 4 字节指纹
    output wire [31:0] dbg_mclk      // MCLK 实测 kHz + 分档 + 有效位
);

    // -------------------------------------------------------------------------
    // 0. 常量
    // -------------------------------------------------------------------------
    localparam integer PHASE = CLK_HZ / (3 * SCL_HZ);   // I2C 每个 phase 的 clk 数
    localparam integer PWRUP = (CLK_HZ / 1000) * PWRUP_MS;
    localparam integer BITDIV = CLK_HZ / BAUD;          // UART 每 bit 的 clk 数
    localparam integer TXGAP  = (CLK_HZ / 1000) * TXGAP_MS;
    localparam integer WIN    = CLK_HZ / 1000;          // MCLK 测量窗口 = 1.000 ms

    localparam [2:0] SQ_PWRUP = 3'd0, SQ_SCAN = 3'd1, SQ_ID  = 3'd2,
                     SQ_ID8   = 3'd3, SQ_DUMP = 3'd4, SQ_HOLD = 3'd5;

    localparam [1:0] X_PROBE = 2'd0, X_RD16 = 2'd1, X_RD8 = 2'd2;

    // 相机候选 7bit 地址：IMX219 官方 0x10；0x20/0x21 是"8bit 写/读地址"写法被当成
    // 7bit 地址的情形；0x30/0x36 是其它常见 MIPI 传感器。命中第一个就算 cam_ack。
    function [6:0] cand_addr;
        input [2:0] i;
        begin
            case (i)
                3'd0:    cand_addr = 7'h10;
                3'd1:    cand_addr = 7'h20;
                3'd2:    cand_addr = 7'h21;
                3'd3:    cand_addr = 7'h30;
                default: cand_addr = 7'h36;
            endcase
        end
    endfunction

    function [7:0] hexchar;             // 4bit -> ASCII
        input [3:0] v;
        begin
            hexchar = (v < 4'd10) ? (8'h30 + {4'd0, v}) : (8'h37 + {4'd0, v});
        end
    endfunction

    // -------------------------------------------------------------------------
    // 1. 复位：上电自动复位（不依赖人手按钮）+ 按钮
    // -------------------------------------------------------------------------
    reg [7:0] por;
    always @(posedge clk) begin
        if (!rst_n) por <= 8'h00;
        else if (por != 8'hFF) por <= por + 8'd1;
    end
    wire srst_n = rst_n && (por == 8'hFF);      // 内部低有效复位

    // -------------------------------------------------------------------------
    // 2. MCLK 监视器：把 CAM_CLK 当【真正的时钟域】，用格雷码计数器跨到 clk 域
    //    为什么必须这么做：U19 同时喂 H16 与（经 R156）pin12，两者**同频**。
    //    用 clk 去"采样"一个同频方波只会得到恒定的 0 或 1（相位锁死），
    //    所以频率只能靠"一个真实时钟域里数自己的边沿"来测。
    //    若 pin12 上没有时钟（R156 未贴 / 相机拉低），这个域根本不翻，delta = 0。
    // -------------------------------------------------------------------------
    wire mclk_khz_v;  reg [15:0] mclk_khz;
    reg  [3:0]  mclk_bucket;
    reg         mvalid;

    generate if (MCLK_MEAS != 0) begin : g_mclk
        (* ASYNC_REG = "TRUE" *) reg [25:0] mcnt;      // cam_clk 域自由运行计数器
        // initial 只为 xsim：综合成 FF 的 INIT，硬件上 GSR 也会给 0；
        // 就算它是 X，delta 是两次采样的差，仍然良定义。
        initial mcnt = 26'd0;
        always @(posedge cam_clk) mcnt <= mcnt + 26'd1;

        wire [25:0] mcnt_gray = mcnt ^ (mcnt >> 1);    // 二进制 -> 格雷（每次只翻 1 位）

        (* ASYNC_REG = "TRUE" *) reg [25:0] g_s1, g_s2;
        always @(posedge clk) begin
            g_s1 <= mcnt_gray;
            g_s2 <= g_s1;
        end

        function [25:0] gray2bin;
            input [25:0] g;
            integer i;
            begin
                gray2bin[25] = g[25];
                for (i = 24; i >= 0; i = i - 1) gray2bin[i] = gray2bin[i+1] ^ g[i];
            end
        endfunction

        wire [25:0] mbin = gray2bin(g_s2);

        reg  [31:0] win_cnt;
        reg  [25:0] mbin_prev, mdelta;
        always @(posedge clk) begin
            if (!srst_n) begin
                win_cnt <= 32'd0; mbin_prev <= 26'd0; mdelta <= 26'd0; mvalid <= 1'b0;
            end else if (win_cnt == (WIN[31:0] - 32'd1)) begin
                win_cnt   <= 32'd0;
                mdelta    <= mbin - mbin_prev;   // 窗口正好 1.000 ms ⇒ delta 就是 kHz
                mbin_prev <= mbin;
                mvalid    <= 1'b1;
            end else begin
                win_cnt <= win_cnt + 32'd1;
            end
        end

        // 窗口 = 1 ms ⇒ delta[15:0] 直接是 kHz（>65.5 MHz 会回绕，但分档仍正确）
        always @(posedge clk) begin
            if (!srst_n) begin
                mclk_khz   <= 16'd0;
                mclk_bucket<= 4'd0;
            end else begin
                mclk_khz <= mdelta[15:0];
                if      (mdelta == 26'd0)         mclk_bucket <= 4'd0;  // 无活动
                else if (mdelta <  26'd1000)      mclk_bucket <= 4'd1;  // < 1 MHz
                else if (mdelta <  26'd5000)      mclk_bucket <= 4'd2;  // 1~5
                else if (mdelta <  26'd15000)     mclk_bucket <= 4'd3;  // 5~15
                else if (mdelta <  26'd35000)     mclk_bucket <= 4'd4;  // 15~35
                else if (mdelta <  26'd45000)     mclk_bucket <= 4'd5;  // 35~45
                else if (mdelta <= 26'd55000)     mclk_bucket <= 4'd6;  // 45~55（期望：50 MHz）
                else                              mclk_bucket <= 4'd7;  // > 55
            end
        end
    end else begin : g_nomclk
        initial begin
            mclk_khz = 16'd0; mclk_bucket = 4'd0; mvalid = 1'b0;
        end
    end
    endgenerate

    // -------------------------------------------------------------------------
    // 3. SCCB 位引擎（与归档探针同一套，含那条关键修正）
    // -------------------------------------------------------------------------
    localparam [1:0] OP_START = 2'd0, OP_TX = 2'd1, OP_RX = 2'd2, OP_STOP = 2'd3;

    reg        op_go;
    reg  [1:0] op;
    reg  [7:0] op_tx;
    reg        op_nack;      // RX 之后回 1 = NACK
    wire       op_done;      // 【单周期脉冲】
    wire [7:0] op_rx;
    wire       op_ack;       // TX 之后从机给的应答（1 = 有 ACK）

    localparam [4:0]
        I_IDLE    = 5'd0,
        I_ST_SET  = 5'd1,   I_ST_FALL = 5'd2,  I_ST_LOW  = 5'd3,
        I_TX_SET  = 5'd4,   I_TX_HI   = 5'd5,  I_TX_LO   = 5'd6,
        I_ACK_SET = 5'd7,   I_ACK_HI  = 5'd8,  I_ACK_LO  = 5'd9,
        I_RX_SET  = 5'd10,  I_RX_HI   = 5'd11, I_RX_LO   = 5'd12,
        I_NK_SET  = 5'd13,  I_NK_HI   = 5'd14, I_NK_LO   = 5'd15,
        I_SP_SET  = 5'd16,  I_SP_HI   = 5'd17, I_SP_REL  = 5'd18;

    reg  [4:0]  ist;
    reg  [15:0] itick;
    reg  [3:0]  ibit;
    reg  [7:0]  ish;
    reg  [7:0]  irx;
    reg         sda_bit;
    reg         ack_n;
    reg         sda_s, sda_s2;      // SDA 输入两级同步
    reg         op_ack_r;

    //  ⚠️【不要删这条】I_IDLE 也必须把 SCL 拉低：
    //    START 事务收尾时从 I_ST_LOW 回 I_IDLE，若这一拍同时释放 SCL 与 SDA，
    //    从机看到的是"SCL 已高、SDA 上升"= 假 STOP，于是清掉 in_frame，
    //    这一帧后面 8 位全部不再采样，自然永远不会 ACK。
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
            // 终止态【直接】回 I_IDLE，不经 I_DONE：否则 op_go 的单周期脉冲
            // 会在过渡态里被丢掉，上层就卡死了。
            I_ST_LOW : ist_nxt = I_IDLE;
            I_TX_SET : ist_nxt = I_TX_HI;
            I_TX_HI  : ist_nxt = I_TX_LO;
            I_TX_LO  : ist_nxt = (ibit == 4'd7) ? I_ACK_SET : I_TX_SET;
            I_ACK_SET: ist_nxt = I_ACK_HI;
            I_ACK_HI : ist_nxt = I_ACK_LO;
            I_ACK_LO : ist_nxt = I_IDLE;
            I_RX_SET : ist_nxt = I_RX_HI;
            I_RX_HI  : ist_nxt = I_RX_LO;
            // ibit 在 RX_HI 末尾已自增，所以第 8 位读完时这里是 8
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
        if (!srst_n) begin
            ist <= I_IDLE; itick <= 16'd0; ibit <= 4'd0; ish <= 8'd0; irx <= 8'd0;
            sda_bit <= 1'b1; ack_n <= 1'b0; sda_s <= 1'b1; sda_s2 <= 1'b1; op_ack_r <= 1'b0;
        end else begin
            sda_s  <= sccb_sda;          // 引脚三态时靠板上 4.7K 上拉为高
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
                if (ist_nxt == I_TX_SET && ist == I_TX_LO) begin
                    ish     <= {ish[6:0], 1'b0};
                    sda_bit <= ish[6];
                    ibit    <= ibit + 4'd1;
                end
                if (ist == I_RX_HI) begin
                    irx  <= {irx[6:0], sda_s2};
                    ibit <= ibit + 4'd1;
                end
                if (ist == I_ACK_HI) op_ack_r <= ~sda_s2;   // 从机拉低 = ACK
                ist <= ist_nxt;
            end else begin
                itick <= itick + 16'd1;
            end
        end
    end

    reg op_done_r;
    always @(posedge clk) begin
        if (!srst_n) op_done_r <= 1'b0;
        else op_done_r <= clk_tick && ((ist == I_ST_LOW) || (ist == I_ACK_LO) ||
                                       (ist == I_NK_LO) || (ist == I_SP_REL));
    end
    assign op_done = op_done_r;
    assign op_rx   = irx;
    assign op_ack  = op_ack_r;

    // -------------------------------------------------------------------------
    // 4. 事务层：把"探测/读寄存器"翻译成一串位引擎微操作
    //    三种事务（全是只读，绝不写寄存器）：
    //      X_PROBE : START, TX{addr,0}, STOP                     —— 地址探测
    //      X_RD16  : START, TX{addr,0}, TX reg_hi, TX reg_lo,
    //                START, TX{addr,1}, RX(NACK), STOP          —— 16bit 寄存器地址
    //      X_RD8   : START, TX{addr,0}, TX reg,
    //                START, TX{addr,1}, RX(NACK), STOP          —— 8bit 寄存器地址
    // -------------------------------------------------------------------------
    localparam [1:0] XS_IDLE = 2'd0, XS_ISSUE = 2'd1, XS_WAIT = 2'd2, XS_FIN = 2'd3;

    reg  [2:0] x_kind;
    reg  [6:0] x_addr;
    reg  [7:0] x_hi, x_lo;
    reg        x_go;
    reg        x_busy, x_done;
    reg        x_ack;          // 地址字节是否被 ACK
    reg  [7:0] x_dout;
    reg  [1:0] xs;
    reg  [3:0] x_step;

    // 当前微操作（组合）
    reg  [1:0] xo_kind;
    reg  [7:0] xo_tx;
    reg        xo_nack;
    always @(*) begin
        xo_kind = OP_STOP; xo_tx = 8'h00; xo_nack = 1'b0;
        case (x_kind)
            X_PROBE: case (x_step)
                4'd0:    xo_kind = OP_START;
                4'd1:    begin xo_kind = OP_TX; xo_tx = {x_addr, 1'b0}; end
                default: xo_kind = OP_STOP;
            endcase
            X_RD16: case (x_step)
                4'd0:    xo_kind = OP_START;
                4'd1:    begin xo_kind = OP_TX; xo_tx = {x_addr, 1'b0}; end
                4'd2:    begin xo_kind = OP_TX; xo_tx = x_hi; end
                4'd3:    begin xo_kind = OP_TX; xo_tx = x_lo; end
                4'd4:    xo_kind = OP_START;
                4'd5:    begin xo_kind = OP_TX; xo_tx = {x_addr, 1'b1}; end
                4'd6:    begin xo_kind = OP_RX; xo_nack = 1'b1; end
                default: xo_kind = OP_STOP;
            endcase
            default: case (x_step)      // X_RD8
                4'd0:    xo_kind = OP_START;
                4'd1:    begin xo_kind = OP_TX; xo_tx = {x_addr, 1'b0}; end
                4'd2:    begin xo_kind = OP_TX; xo_tx = x_lo; end
                4'd3:    xo_kind = OP_START;
                4'd4:    begin xo_kind = OP_TX; xo_tx = {x_addr, 1'b1}; end
                4'd5:    begin xo_kind = OP_RX; xo_nack = 1'b1; end
                default: xo_kind = OP_STOP;
            endcase
        endcase
    end

    reg [3:0] x_last;
    always @(*) begin
        case (x_kind)
            X_PROBE: x_last = 4'd2;
            X_RD16 : x_last = 4'd7;
            default: x_last = 4'd6;
        endcase
    end

    always @(posedge clk) begin
        op_go   <= 1'b0;
        x_done  <= 1'b0;
        if (!srst_n) begin
            xs <= XS_IDLE; x_step <= 4'd0; x_busy <= 1'b0;
            x_ack <= 1'b0; x_dout <= 8'hFF; x_go <= 1'b0;
        end else begin
            case (xs)
                XS_IDLE: if (x_go) begin
                    x_step <= 4'd0;
                    x_busy <= 1'b1;
                    x_ack  <= 1'b0;
                    xs     <= XS_ISSUE;
                end
                XS_ISSUE: begin
                    op      <= xo_kind;
                    op_tx   <= xo_tx;
                    op_nack <= xo_nack;
                    op_go   <= 1'b1;       // 单周期脉冲：下一拍 XS_WAIT 里被清掉
                    xs      <= XS_WAIT;
                end
                XS_WAIT: begin
                    if (op_done) begin
                        // step1 = 地址字节 ⇒ 它的 ACK 就是"器件在不在"
                        if (x_step == 4'd1) x_ack <= op_ack;
                        if (xo_kind == OP_RX) x_dout <= op_rx;
                        if (x_step == x_last) xs <= XS_FIN;
                        else begin
                            x_step <= x_step + 4'd1;
                            xs     <= XS_ISSUE;
                        end
                    end
                end
                default: begin            // XS_FIN
                    x_busy <= 1'b0;
                    x_done <= 1'b1;
                    xs     <= XS_IDLE;
                end
            endcase
        end
    end

    // -------------------------------------------------------------------------
    // 5. 定序器：扫全总线 -> 读相机 MODEL_ID（两种地址宽度）-> 读 EEPROM 指纹 -> HOLD
    //    跑完就停在 SQ_HOLD（所有探针冻结），所以主机端直接 trigger_now 即可，
    //    不需要任何触发条件——这是上一轮已经验证过的用法。
    // -------------------------------------------------------------------------
    reg  [2:0]   sq;
    reg  [31:0]  sq_cnt;
    reg  [6:0]   scan_a;
    reg  [127:0] busmap;
    reg  [6:0]   scan_hits;
    reg          scan_done;
    reg  [2:0]   cand_i;
    reg  [6:0]   cam_addr;
    reg          cam_ack, id16_ack, id8_ack, dump50_ack, dump58_ack;
    reg  [15:0]  id16, id8;
    reg  [31:0]  dump50, dump58;
    reg  [3:0]   u;

    always @(posedge clk) begin
        x_go <= 1'b0;
        if (!srst_n) begin
            sq <= SQ_PWRUP; sq_cnt <= 32'd0; scan_a <= 7'd0;
            busmap <= {128{1'b0}}; scan_hits <= 7'd0; scan_done <= 1'b0;
            cand_i <= 3'd0; cam_addr <= 7'd0;
            cam_ack <= 1'b0; id16_ack <= 1'b0; id8_ack <= 1'b0;
            dump50_ack <= 1'b0; dump58_ack <= 1'b0;
            id16 <= 16'hFFFF; id8 <= 16'hFFFF; dump50 <= 32'd0; dump58 <= 32'd0;
            u <= 4'd0;
        end else begin
            case (sq)
                // ---- 上电等待 ----
                SQ_PWRUP: begin
                    if (sq_cnt == PWRUP[31:0] - 32'd1) begin
                        sq_cnt <= 32'd0;
                        sq     <= SQ_SCAN;
                    end else sq_cnt <= sq_cnt + 32'd1;
                end
                // ---- 扫 0x00~0x7F：快速写探测（只发地址字节，不写任何寄存器）----
                SQ_SCAN: begin
                    if (!x_busy && !x_go) begin
                        if (x_done) begin
                            if (x_ack) begin
                                busmap[scan_a] <= 1'b1;
                                scan_hits      <= scan_hits + 7'd1;
                            end
                            if (scan_a == 7'd127) begin
                                sq        <= SQ_ID;
                                scan_done <= 1'b1;
                                cand_i    <= 3'd0;
                                u         <= 4'd0;
                            end else begin
                                scan_a <= scan_a + 7'd1;
                            end
                        end else begin
                            x_kind <= X_PROBE;
                            x_addr <= scan_a;
                            x_hi   <= 8'h00;
                            x_lo   <= 8'h00;
                            x_go   <= 1'b1;
                        end
                    end
                end
                // ---- 相机候选地址：16bit 寄存器地址读 MODEL_ID(0x0000/0x0001)----
                SQ_ID: begin
                    if (u == 4'd0) begin
                        if (!x_busy && !x_go) begin
                            if (x_done) begin
                                if (x_ack) begin
                                    cam_addr <= cand_addr(cand_i);
                                    cam_ack  <= 1'b1;
                                    id16[15:8] <= x_dout;
                                    id16_ack   <= 1'b1;
                                    u          <= 4'd1;
                                end else if (cand_i == 3'd4) begin
                                    sq <= SQ_ID8;      // 五个候选全没应答
                                    u  <= 4'd0;
                                end else begin
                                    cand_i <= cand_i + 3'd1;
                                end
                            end else begin
                                x_kind <= X_RD16;
                                x_addr <= cand_addr(cand_i);
                                x_hi   <= 8'h00;
                                x_lo   <= 8'h00;
                                x_go   <= 1'b1;
                            end
                        end
                    end else begin                       // u == 1：读 0x0001
                        if (!x_busy && !x_go) begin
                            if (x_done) begin
                                id16 <= {id16[15:8], x_dout};
                                sq   <= SQ_ID8;
                                u    <= 4'd0;
                            end else begin
                                x_kind <= X_RD16;
                                x_addr <= cam_addr;
                                x_hi   <= 8'h00;
                                x_lo   <= 8'h01;
                                x_go   <= 1'b1;
                            end
                        end
                    end
                end
                // ---- 同地址再用 8bit 寄存器地址读一遍（两种约定都覆盖）----
                SQ_ID8: begin
                    if (!cam_ack) begin
                        sq <= SQ_DUMP; u <= 4'd0;
                    end else if (u == 4'd0) begin
                        if (!x_busy && !x_go) begin
                            if (x_done) begin
                                id8[15:8] <= x_dout;
                                id8_ack   <= x_ack;
                                u         <= 4'd1;
                            end else begin
                                x_kind <= X_RD8;
                                x_addr <= cam_addr;
                                x_lo   <= 8'h00;
                                x_go   <= 1'b1;
                            end
                        end
                    end else begin
                        if (!x_busy && !x_go) begin
                            if (x_done) begin
                                id8 <= {id8[15:8], x_dout};
                                sq  <= SQ_DUMP;
                                u   <= 4'd0;
                            end else begin
                                x_kind <= X_RD8;
                                x_addr <= cam_addr;
                                x_lo   <= 8'h01;
                                x_go   <= 1'b1;
                            end
                        end
                    end
                end
                // ---- 板载 EEPROM 指纹：0x50 与 0x58 各读 4 字节（字地址 0..3）----
                SQ_DUMP: begin
                    if (!x_busy && !x_go) begin
                        if (x_done) begin
                            if (u[2:0] <= 3'd3) begin
                                dump50 <= {dump50[23:0], x_dout};
                                if (u[2:0] == 3'd0) dump50_ack <= x_ack;
                            end else begin
                                dump58 <= {dump58[23:0], x_dout};
                                if (u[2:0] == 3'd4) dump58_ack <= x_ack;
                            end
                            if (u == 4'd7) begin
                                sq <= SQ_HOLD;
                                u  <= 4'd0;
                            end else u <= u + 4'd1;
                        end else begin
                            x_kind <= X_RD16;
                            x_addr <= (u[2:0] <= 3'd3) ? 7'h50 : 7'h58;
                            x_hi   <= 8'h00;
                            x_lo   <= {6'b0, u[1:0]};   // 字地址 0,1,2,3
                            x_go   <= 1'b1;
                        end
                    end
                end
                // ---- 停在 HOLD：探针冻结，等主机 trigger_now ----
                default: ;
            endcase
        end
    end

    // -------------------------------------------------------------------------
    // 6. UART 报文（只有 HOLD 之后才发，避免半途数据在变）
    //    "CD aa c iiii kkkk hh\r\n" —— 22 字节，无缓冲：按发送下标组合出来
    // -------------------------------------------------------------------------
    localparam [4:0] MSG_LAST = 5'd21;

    function [7:0] msg_at;
        input [4:0] i;
        begin
            case (i)
                5'd0 :   msg_at = "C";
                5'd1 :   msg_at = "D";
                5'd2 :   msg_at = " ";
                5'd3 :   msg_at = hexchar(cam_addr[6:4]);
                5'd4 :   msg_at = hexchar(cam_addr[3:0]);
                5'd5 :   msg_at = " ";
                5'd6 :   msg_at = cam_ack ? "1" : "0";
                5'd7 :   msg_at = " ";
                5'd8 :   msg_at = hexchar(id16[15:12]);
                5'd9 :   msg_at = hexchar(id16[11:8]);
                5'd10:   msg_at = hexchar(id16[7:4]);
                5'd11:   msg_at = hexchar(id16[3:0]);
                5'd12:   msg_at = " ";
                5'd13:   msg_at = hexchar(mclk_khz[15:12]);
                5'd14:   msg_at = hexchar(mclk_khz[11:8]);
                5'd15:   msg_at = hexchar(mclk_khz[7:4]);
                5'd16:   msg_at = hexchar(mclk_khz[3:0]);
                5'd17:   msg_at = " ";
                5'd18:   msg_at = hexchar(scan_hits[7:4]);
                5'd19:   msg_at = hexchar(scan_hits[3:0]);
                5'd20:   msg_at = 8'h0D;
                default: msg_at = 8'h0A;
            endcase
        end
    endfunction

    reg  [4:0]  tx_idx;
    reg  [15:0] bdiv;
    reg  [3:0]  bbit;
    reg  [9:0]  bsh;
    reg         bbusy;
    reg  [31:0] bgap;

    assign uart_tx = bbusy ? bsh[0] : 1'b1;

    always @(posedge clk) begin
        if (!srst_n) begin
            tx_idx <= 5'd0; bdiv <= 16'd0; bbit <= 4'd0; bsh <= 10'h3FF;
            bbusy  <= 1'b0; bgap <= 32'd0;
        end else if (!bbusy) begin
            if (bgap != 32'd0) begin
                bgap <= bgap - 32'd1;
            end else if (sq == SQ_HOLD) begin
                bsh    <= {1'b1, msg_at(tx_idx), 1'b0};   // stop, data(8), start
                bbit   <= 4'd0;
                bdiv   <= 16'd0;
                bbusy  <= 1'b1;
            end
        end else begin
            if (bdiv == BITDIV[15:0] - 16'd1) begin
                bdiv <= 16'd0;
                if (bbit == 4'd9) begin
                    bbusy  <= 1'b0;
                    bgap   <= TXGAP[31:0];
                    tx_idx <= (tx_idx == MSG_LAST) ? 5'd0 : tx_idx + 5'd1;
                end else begin
                    bbit <= bbit + 4'd1;
                    bsh  <= {1'b1, bsh[9:1]};
                end
            end else bdiv <= bdiv + 16'd1;
        end
    end

    // -------------------------------------------------------------------------
    // 7. 现场判据：4 个 LED（低电平点亮 —— 亮 = 有）
    //    led[0] 心跳         led[1] 测完(HOLD)
    //    led[2] 相机类地址有 ACK             ← "连上了"的现场答案
    //    led[3] CAM_CLK 上有活动             ← 时钟病因的现场答案
    // -------------------------------------------------------------------------
    reg [23:0] hb;
    always @(posedge clk) begin
        if (!srst_n) hb <= 24'd0;
        else hb <= hb + 24'd1;
    end

    assign led[0] = ~hb[23];
    assign led[1] = ~(sq == SQ_HOLD);
    assign led[2] = ~cam_ack;
    assign led[3] = ~(mclk_bucket != 4'd0);

    // -------------------------------------------------------------------------
    // 8. ILA 探针打包（5 个，位段就是契约；改这里必须同步 check_camera.py）
    //    probe0 dbg_bus_lo[63:0] : bit i    = 地址 0x00+i 有 ACK
    //    probe1 dbg_bus_hi[63:0] : bit i    = 地址 0x40+i 有 ACK
    //    probe2 dbg_status[63:0] :
    //        [63:61] sq[2:0]           [60] scan_done
    //        [59] cam_ack              [58] id16_ack   [57] id8_ack  [56] mvalid
    //        [55:52] mclk_bucket[3:0]  [51:45] scan_hits[6:0]  [44:38] cam_addr[6:0]
    //        [37] dump50_ack           [36] dump58_ack
    //        [34:19] id16[15:0]        [18:3]  id8[15:0]  [2:0] 保留
    //    probe3 dbg_dump[63:0]   : [55:40] 0x58 前 2 字节, [39:24] 0x50 前 2 字节
    //    probe4 dbg_mclk[31:0]   : [20] mvalid, [19:16] bucket, [15:0] kHz
    // -------------------------------------------------------------------------
    assign dbg_bus_lo = busmap[63:0];
    assign dbg_bus_hi = busmap[127:64];

    assign dbg_status = { sq, scan_done, cam_ack, id16_ack, id8_ack, mvalid,
                          mclk_bucket, scan_hits, cam_addr,
                          dump50_ack, dump58_ack, id16, id8, 4'b0000 };

    assign dbg_dump   = { dump58[15:0], dump50[15:0], 32'h0000_0000 };

    assign dbg_mclk   = { 11'h000, mvalid, mclk_bucket, mclk_khz };

endmodule
