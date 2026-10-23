`timescale 1ns / 1ps
//=============================================================================
// pl_eeprom_dump.v —— 把 SCCB 总线上两颗 EEPROM 的前 16 字节倒出来（纯 PL，只读）
//-----------------------------------------------------------------------------
// 【为什么要有这个东西】
//   2026-10-19 的 cam_detect 探针确认：CAM_CLK 已被 PL 驱动到 25 MHz（片上实测），
//   但相机（7bit 0x10）在任何地址都不应答，只有 0x50 / 0x58 两个 EEPROM 类地址应答。
//   板上只有一颗 24C32(U18)，A1/A2 接地 ⇒ 它只能占 0x50 一个地址（见 docs/34、
//   _cam_build/sch/p14_eeprom.png 复核）。那 **0x58 是谁**？
//     · 若 0x58 是「相机模块自带的 EEPROM」⇒ 模块已通电、排线也通，
//       故障点就精准落在传感器本体 / XCLR 上；
//     · 若 0x58 与 0x50 读回**完全相同的内容** ⇒ 是同一颗芯片（A0 悬空被判成 1），
//       那就与模块无关。
//   两类结论用「读回内容比对」就能分开，所以本探针只做一件事：读内容。
//
// 【读什么】对每个器件地址执行标准 EEPROM 随机读：
//     START, 写地址(A0h), 字地址高字节 00h, 低字节 00h   ← dummy write，定游标
//     START(重发起), 读地址(A1h), 连续读 NBYTES 个字节, 最后一个回 NACK
//     STOP
//   全程**只读不写**：唯一"写"的是字地址指针 00 00，不改变任何存储内容。
//
// 【结果怎么拿出去】4 条 dbg 总线（ILA 4 探针 32/64/64/64）：
//   dbg0[31:0] = { 16'hCAFE,          // 魔数：证明抓到的是本探针的终态
//                  last_qi[7:0],      // 最后完成的脚本步号
//                  done,              // 1 = 两轮都跑完
//                  a_ok,              // 器件 A(0x50) 的地址被 ACK 过
//                  b_ok,              // 器件 B(0x58) 的地址被 ACK 过
//                  mclk_seen,         // CAM_CLK 上量到过翻转（顺手自检）
//                  fail_seen,         // 出现过 NACK
//                  2'b00,
//                  devi }             // 当前/最后处理的器件号
//   dbg1[63:0] = A 的 byte0..7   （byte0 在 [7:0]，byte7 在 [63:56]）
//   dbg2[63:0] = A 的 byte8..15
//   dbg3[63:0] = B 的 byte0..7
//   ★ 未应答/未被写入的字节保持 0xFF（复位值），便于与"空白 EEPROM"对照。
//
// 【与 pl_cam_detect.v 的关系】
//   SCCB 位翻转引擎（OP_START/TX/RX/STOP）与 CAM_CLK 驱动段是**逐行照搬**
//   pl_cam_detect.v（那套引擎已在真机上被 EEPROM 的 ACK 证明可用）；本文件只换了
//   上层的定序器。改引擎请两边一起改，否则两份探针行为会分叉。
//=============================================================================

module pl_eeprom_dump #(
    parameter integer CLK_HZ     = 50_000_000,  // H16 上的 50 MHz
    parameter integer SCL_HZ     = 100_000,     // SCCB 时钟
    parameter integer PWRUP_MS   = 200,         // 上电等待
    parameter [6:0]   DEV_A      = 7'h50,       // 板上 24C32(U18) 的 7bit 地址
    parameter [6:0]   DEV_B      = 7'h58,       // 待确认的第二个应答地址
    parameter integer DRIVE_MCLK = 1,           // 1 = PL 驱动 CAM_CLK（板上无第二驱动源）
    parameter integer MCLK_DIV   = 2,           // clk/2 = 25 MHz
    parameter integer MEAS_N     = 4096,
    parameter integer NBYTES     = 16           // 每个器件读多少字节（本版固定 ≤16）
)(
    input  wire        clk,         // H16，50 MHz
    input  wire        rst_n,       // R19，PL_KEY1
    inout  wire        mclk,        // U9  CAM_CLK  = pin12
    inout  wire        sccb_scl,    // A20 CAM_SCL  = pin13
    inout  wire        sccb_sda,    // E17 CAM_SDA  = pin14
    output wire [3:0]  led,         // G14/C20/B20/H17（低电平点亮）
    output wire [31:0] dbg0,
    output wire [63:0] dbg1,
    output wire [63:0] dbg2,
    output wire [63:0] dbg3
);

    //==========================================================================
    // 1. CAM_CLK：驱动 + 「有没有在翻」的粗测（与 pl_cam_detect.v 同源，精简版）
    //==========================================================================
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
    assign mclk = DRIVE_MCLK ? mclk_r : 1'bz;

    wire mclk_meas = DRIVE_MCLK ? mclk_r : mclk;

    reg [15:0] cam_div;
    reg        cam_tog;
    initial begin
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

    (* ASYNC_REG = "TRUE" *) reg tog_s1, tog_s2;
    always @(posedge clk) begin
        tog_s1 <= cam_tog;
        tog_s2 <= tog_s1;
    end
    wire cam_pulse = tog_s2 ^ tog_s1;

    reg [7:0]  pulse_cnt;
    reg        mclk_seen;
    always @(posedge clk) begin
        if (!rst_n) begin
            pulse_cnt <= 8'd0;
            mclk_seen <= 1'b0;
        end else if (cam_pulse) begin
            if (pulse_cnt != 8'hFF) pulse_cnt <= pulse_cnt + 8'd1;
            if (pulse_cnt >= 8'd1)  mclk_seen <= 1'b1;   // 第二拍才采信，防上电假沿
        end
    end

    //==========================================================================
    // 2. SCCB 位翻转引擎（照搬 pl_cam_detect.v:152-290，逻辑未改）
    //==========================================================================
    localparam integer PHASE = CLK_HZ / (3 * SCL_HZ);
    localparam integer PWRUP = (CLK_HZ / 1000) * PWRUP_MS;

    localparam [1:0] OP_START = 2'd0, OP_TX = 2'd1, OP_RX = 2'd2, OP_STOP = 2'd3;

    reg        op_go;
    reg  [1:0] op;
    reg  [7:0] op_tx;
    reg        op_nack;
    wire       op_done;
    wire [7:0] op_rx;
    wire       op_ack;

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
    reg         sda_s, sda_s2;
    reg         op_ack_r;

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
            I_ST_LOW : ist_nxt = I_IDLE;
            I_TX_SET : ist_nxt = I_TX_HI;
            I_TX_HI  : ist_nxt = I_TX_LO;
            I_TX_LO  : ist_nxt = (ibit == 4'd7) ? I_ACK_SET : I_TX_SET;
            I_ACK_SET: ist_nxt = I_ACK_HI;
            I_ACK_HI : ist_nxt = I_ACK_LO;
            I_ACK_LO : ist_nxt = I_IDLE;
            I_RX_SET : ist_nxt = I_RX_HI;
            I_RX_HI  : ist_nxt = I_RX_LO;
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
            sda_s  <= sccb_sda;
            sda_s2 <= sda_s;

            if (ist == I_IDLE) begin
                itick <= 16'd0;
                if (op_go) begin
                    ibit    <= 4'd0;
                    ish     <= op_tx;
                    irx     <= 8'd0;
                    ack_n   <= op_nack;
                    sda_bit <= op_tx[7];
                    op_ack_r <= 1'b0;          // 每个新事务清零，避免陈旧 ACK
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
        if (!rst_n) op_done_r <= 1'b0;
        else op_done_r <= clk_tick && ((ist == I_ST_LOW) || (ist == I_ACK_LO) ||
                                       (ist == I_NK_LO) || (ist == I_SP_REL));
    end
    assign op_done = op_done_r;
    assign op_rx   = irx;
    assign op_ack  = op_ack_r;

    //==========================================================================
    // 3. 定序器：两台器件 × (dummy write + 重发起 + 连读 NBYTES + STOP)
    //==========================================================================
    localparam [4:0] QI_START0 = 5'd0;
    localparam [4:0] QI_ADDW   = 5'd1;
    localparam [4:0] QI_WAHI   = 5'd2;
    localparam [4:0] QI_WALO   = 5'd3;
    localparam [4:0] QI_START1 = 5'd4;
    localparam [4:0] QI_ADDR   = 5'd5;
    localparam [4:0] QI_D0     = 5'd6;
    localparam [4:0] QI_DLAST  = QI_D0 + (NBYTES - 1);   // = 21
    localparam [4:0] QI_STOP   = QI_DLAST + 5'd1;              // = 22

    localparam [2:0] S_PWRUP = 3'd0, S_ISS = 3'd1, S_WAIT = 3'd2, S_DONE = 3'd3;

    reg [2:0]  seq;
    reg [4:0]  qi;
    reg        devi;
    reg [31:0] wt;
    reg        a_ok, b_ok, fail_seen, done_r;
    reg [7:0]  last_qi;
    reg [1:0]  cur_op;      // ★ 发事务时锁存的 op：S_WAIT 里 op 已回到默认值，不能直接比
    reg [7:0]  mem_a [0:15];
    reg [7:0]  mem_b [0:15];
    integer    i;

    wire [7:0] dev_wr = {devi ? DEV_B : DEV_A, 1'b0};
    wire [7:0] dev_rd = {devi ? DEV_B : DEV_A, 1'b1};

    always @(*) begin
        op_go   = 1'b0; op = OP_START; op_tx = 8'h00; op_nack = 1'b0;
        if (seq == S_ISS) begin
            op_go = 1'b1;
            case (qi)
                QI_START0: op = OP_START;
                QI_ADDW  : begin op = OP_TX; op_tx = dev_wr;  end
                QI_WAHI  : begin op = OP_TX; op_tx = 8'h00;   end
                QI_WALO  : begin op = OP_TX; op_tx = 8'h00;   end
                QI_START1: op = OP_START;
                QI_ADDR  : begin op = OP_TX; op_tx = dev_rd;  end
                default  : begin
                    if ((qi >= QI_D0) && (qi <= QI_DLAST)) begin
                        op = OP_RX; op_nack = (qi == QI_DLAST);
                    end else begin
                        op = OP_STOP;
                    end
                end
            endcase
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            seq <= S_PWRUP; qi <= QI_START0; devi <= 1'b0; wt <= 32'd0;
            a_ok <= 1'b0; b_ok <= 1'b0; fail_seen <= 1'b0; done_r <= 1'b0;
            last_qi <= 8'd0; cur_op <= OP_START;
            for (i = 0; i < 16; i = i + 1) begin
                mem_a[i] <= 8'hFF;
                mem_b[i] <= 8'hFF;
            end
        end else begin
            case (seq)
                S_PWRUP: begin
                    if (wt == (PWRUP - 1)) begin
                        wt  <= 32'd0;
                        seq <= S_ISS;
                    end else begin
                        wt <= wt + 32'd1;
                    end
                end
                S_ISS: begin
                    cur_op <= op;
                    seq    <= S_WAIT;
                end
                S_WAIT: if (op_done) begin
                    last_qi <= {3'b000, qi};
                    if (cur_op == OP_TX) begin
                        if (!op_ack) begin
                            // 该器件不在（或地址被拒）⇒ 不做后面的读，直接换下一个
                            fail_seen <= 1'b1;
                            qi  <= QI_START0;
                            if (devi == 1'b0) begin
                                devi <= 1'b1; seq <= S_ISS;
                            end else begin
                                seq <= S_DONE; done_r <= 1'b1;
                            end
                        end else begin
                            if (qi == QI_ADDR) begin
                                if (devi == 1'b0) a_ok <= 1'b1; else b_ok <= 1'b1;
                            end
                            qi  <= qi + 5'd1;
                            seq <= S_ISS;
                        end
                    end else if (cur_op == OP_RX) begin
                        if (devi == 1'b0) mem_a[qi - QI_D0] <= op_rx;
                        else              mem_b[qi - QI_D0] <= op_rx;
                        qi  <= qi + 5'd1;
                        seq <= S_ISS;
                    end else if (qi == QI_STOP) begin
                        qi  <= QI_START0;
                        if (devi == 1'b0) begin
                            devi <= 1'b1; seq <= S_ISS;
                        end else begin
                            seq <= S_DONE; done_r <= 1'b1;
                        end
                    end else begin
                        qi  <= qi + 5'd1;
                        seq <= S_ISS;
                    end
                end
                S_DONE: ;
                default: seq <= S_DONE;
            endcase
        end
    end

    //==========================================================================
    // 4. 打包到 4 条 dbg 总线 + LED
    //==========================================================================
    assign dbg0 = { 16'hCAFE, last_qi, done_r, a_ok, b_ok,
                    mclk_seen, fail_seen, 2'b00, devi };

    assign dbg1 = { mem_a[7], mem_a[6], mem_a[5], mem_a[4],
                    mem_a[3], mem_a[2], mem_a[1], mem_a[0] };
    assign dbg2 = { mem_a[15], mem_a[14], mem_a[13], mem_a[12],
                    mem_a[11], mem_a[10], mem_a[9], mem_a[8] };
    assign dbg3 = { mem_b[7], mem_b[6], mem_b[5], mem_b[4],
                    mem_b[3], mem_b[2], mem_b[1], mem_b[0] };

    reg [24:0] hb;
    always @(posedge clk) begin
        if (!rst_n) hb <= 25'd0;
        else        hb <= hb + 25'd1;
    end

    assign led[0] = ~hb[23];        // 心跳 ≈ 3 Hz
    assign led[1] = ~done_r;        // 跑完了就灭
    assign led[2] = ~a_ok;          // 0x50 应答过
    assign led[3] = ~b_ok;          // 0x58 应答过

endmodule
