//=============================================================================
// tb_pl_imx219_probe.v -- pl_imx219_probe 的自检 testbench
//-----------------------------------------------------------------------------
// 仿真里放一个 IMX219 从机模型，按真机的 SCCB 时序应答：
//   · 只在 7bit 地址 0x10 上应答（其余 127 个地址一律不 ACK）
//   · 寄存器 0x0000 = 0x02，0x0001 = 0x19（真机 chip ID）
//   · 同时接受 16bit 与 8bit 两种寄存器地址写法（因为这两条口径都还是 UNVERIFIED）
// TB 还把 UART 输出解回来打印，并对关键结论做断言。
//=============================================================================
`timescale 1ns / 1ps

module tb_pl_imx219_probe;

    //--------------------------------------------------------------------------
    // 时钟与复位
    //--------------------------------------------------------------------------
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #10 clk = ~clk;               // 50 MHz

    //--------------------------------------------------------------------------
    // 总线（三态 + 上拉：与真实板上一致）
    //--------------------------------------------------------------------------
    tri sccb_scl;
    tri sccb_sda;
    pullup pu_scl (sccb_scl);
    pullup pu_sda (sccb_sda);

    wire       mclk;
    wire       uart_tx;
    wire [3:0] led;

    pl_imx219_probe #(
        .CLK_HZ    (50_000_000),
        .BAUD      (115200),
        .SCL_HZ    (100_000),
        .PROBE_ADDR(7'h10),
        .PWRUP_MS  (1)                    // 仿真里缩短上电等待
    ) dut (
        .clk      (clk),
        .rst_n    (rst_n),
        .mclk     (mclk),
        .sccb_scl (sccb_scl),
        .sccb_sda (sccb_sda),
        .uart_tx  (uart_tx),
        .led      (led)
    );

    //--------------------------------------------------------------------------
    // IMX219 从机模型
    //--------------------------------------------------------------------------
    localparam [6:0] SLV_ADDR = 7'h10;

    reg [7:0] slv_regs [0:255];
    integer   z;
    initial begin
        for (z = 0; z < 256; z = z + 1) slv_regs[z] = 8'h00;
        slv_regs[8'h00] = 8'h02;          // MODEL_ID_H
        slv_regs[8'h01] = 8'h19;          // MODEL_ID_L
        slv_regs[8'h02] = 8'h00;          // REVISION
    end

    reg  slv_sda_low = 1'b0;
    assign sccb_sda = slv_sda_low ? 1'b0 : 1'bz;

    reg       in_frame = 1'b0;
    reg [7:0] rxsh     = 8'd0;
    reg [2:0] rbit     = 3'd0;
    reg       ack_slot = 1'b0;
    reg       ack_done = 1'b0;
    reg [2:0] st       = 3'd0;   // 0=等地址 1=等reg_hi 2=等reg_lo 3=等读地址 4=发数据
    reg [15:0] raddr   = 16'd0;
    reg [7:0] tsh      = 8'd0;
    reg [2:0] tbit     = 3'd0;
    //  上一次写相里写了几个寄存器地址字节。IMX219 用 16 位寄存器地址（见
    //  board/imx219_driver.py 的 _addr_bytes），但探针里另有一条 8 位变体路径，
    //  从机要能分辨：写相只发了 1 个地址字节就重复起始读，那是 8 位口径。
    //  ⚠️ START 检测清 st 但【不清 n_addr】，判别就靠它。
    reg [1:0] n_addr   = 2'd0;

    // START / STOP 检测
    always @(negedge sccb_sda) if (sccb_scl === 1'b1) begin
        in_frame <= 1'b1; rbit <= 3'd0; ack_slot <= 1'b0; ack_done <= 1'b0; st <= 3'd0;
    end
    always @(posedge sccb_sda) if (sccb_scl === 1'b1 && in_frame) begin
        // 真的 STOP 只在帧尾发生；如果这里出现的是帧中途，就是主机 SDA 时序错了
        $display("[%0t ns] *** STOP detect: dut.ist=%0d itick=%0d sda_bit=%b op=%0d scl=%b sda=%b",
                 $time, dut.ist, dut.itick, dut.sda_bit, dut.op, sccb_scl, sccb_sda);
        in_frame <= 1'b0; slv_sda_low <= 1'b0;
    end

    // 前 40 个总线事件逐条打印：START / SCL 上升沿时从机看到的状态
    integer trace_n = 0;
    always @(negedge sccb_sda) if (sccb_scl === 1'b1 && trace_n < 40) begin
        trace_n = trace_n + 1;
        $display("[%0t ns] #%0d START   in_frame=%b rbit=%0d ack_slot=%b st=%0d",
                 $time, trace_n, in_frame, rbit, ack_slot, st);
    end
    always @(posedge sccb_scl) if (trace_n < 40) begin
        trace_n = trace_n + 1;
        $display("[%0t ns] #%0d SCL^    in_frame=%b rbit=%0d ack_slot=%b ack_done=%b sda=%b",
                 $time, trace_n, in_frame, rbit, ack_slot, ack_done, sccb_sda);
    end

    // 采样（SCL 上升沿）
    always @(posedge sccb_scl) if (in_frame && !ack_slot) begin
        rxsh <= {rxsh[6:0], sccb_sda};
        if (rbit == 3'd7) begin rbit <= 3'd0; ack_slot <= 1'b1; ack_done <= 1'b0; end
        else rbit <= rbit + 3'd1;
    end

    // 驱动（SCL 下降沿）
    reg ack_this;
    always @(negedge sccb_scl) if (in_frame) begin
        if (ack_slot && !ack_done) begin
            // 刚收完一个字节：决定怎么应答并推进状态
            case (st)
            3'd0: begin
                ack_this = (rxsh[7:1] == SLV_ADDR);
                if (ack_this) begin
                    if (rxsh[0]) begin
                        // 读地址：START 检测刚把 st 清成 0，而重复起始之后的这个字节
                        // 本身就是读地址，紧接着就是数据相——必须在这里就把寄存器内容
                        // 装上并转 st=4，否则它会被当成"待收的第二个地址字节"留在 st=3，
                        // 数据相永远不驱动 SDA，主机只能读到释放的高电平（实测 0xFF）。
                        //  地址口径由上一次写相的字节数决定：2 字节 = 16 位（真实
                        //  IMX219 的口径），1 字节 = 探针里的 8 位变体路径。
                        if (n_addr == 2'd1) tsh <= slv_regs[raddr[15:8]];
                        else                tsh <= slv_regs[raddr];
                        st <= 3'd4;
                    end else st <= 3'd1;      // 写地址：后面跟寄存器地址
                end
            end
            3'd1: begin raddr[15:8] <= rxsh; ack_this = 1'b1; n_addr <= 2'd1; st <= 3'd2; end
            3'd2: begin raddr[7:0]  <= rxsh; ack_this = 1'b1; n_addr <= 2'd2; st <= 3'd0; end
            3'd3: begin ack_this = (rxsh[7:1] == SLV_ADDR) && rxsh[0];
                       if (ack_this) begin tsh <= slv_regs[raddr]; st <= 3'd4; end
                       else st <= 3'd0; end
            default: ack_this = 1'b1;
            endcase
            slv_sda_low <= ack_this;       // ACK = 拉低；NACK = 释放
            ack_done    <= 1'b1;
        end else if (ack_slot && ack_done) begin
            ack_slot    <= 1'b0;
            ack_done    <= 1'b0;
            rbit        <= 3'd0;
            if (st == 3'd4) begin
                // 读事务：ACK 之后 SCL 一直停在低电平直到第一个数据位，
                // 所以 D7 必须在这里就摆上总线，等下一个下降沿就晚了整整一位。
                slv_sda_low <= ~tsh[7];
                tsh         <= {tsh[6:0], 1'b0};
                tbit        <= 3'd1;
            end else begin
                slv_sda_low <= 1'b0;           // ACK 位结束，释放
            end
        end else if (st == 3'd4) begin
            // 读操作：发数据位。tbit 0..7 依次是 D7..D0，共 8 位都要发；
            // 发完转 st=5 释放 SDA，否则主机的 NACK/STOP 边沿会被顶住。
            slv_sda_low <= ~tsh[7];
            tsh         <= {tsh[6:0], 1'b0};
            tbit        <= tbit + 3'd1;
            if (tbit == 3'd7) st <= 3'd5;
        end else if (st == 3'd5) begin
            slv_sda_low <= 1'b0;               // 8 位发完，释放 SDA
            st          <= 3'd0;
        end
    end

    //--------------------------------------------------------------------------
    // UART 解码（115200 8N1）
    //--------------------------------------------------------------------------
    localparam real BIT_NS = 1_000_000_000.0 / 115200.0;   // 8680.55 ns
    reg [7:0] uart_buf [0:255];
    integer   uart_n = 0;
    integer   u;
    reg [7:0] ub;

    initial begin
        forever begin
            @(negedge uart_tx);
            #(BIT_NS * 1.5);
            for (u = 0; u < 8; u = u + 1) begin
                ub[u] = uart_tx;
                #(BIT_NS);
            end
            uart_buf[uart_n] = ub;
            uart_n = uart_n + 1;
        end
    end

    //--------------------------------------------------------------------------
    // 调试监视：把 DUT 内部状态周期性打出来（只在仿真里用）
    //--------------------------------------------------------------------------
    integer scl_fall = 0;
    integer sda_fall = 0;
    always @(negedge sccb_scl) scl_fall = scl_fall + 1;
    always @(negedge sccb_sda) sda_fall = sda_fall + 1;

    // ACK 链路定位：从机应答了几次？主机采到几次？断在哪一环一看便知。
    integer slv_ack_cnt = 0;
    integer dut_ack_cnt = 0;
    always @(posedge slv_sda_low) begin
        slv_ack_cnt = slv_ack_cnt + 1;
        $display("[%0t ns] SLAVE ACK #%0d  rxsh=%02h st=%0d scl=%b sda=%b",
                 $time, slv_ack_cnt, rxsh, st, sccb_scl, sccb_sda);
    end
    always @(posedge clk) if (dut.op_ack) begin
        if (dut_ack_cnt == 0) $display("[%0t ns] DUT op_ack went 1 (first time)", $time);
        dut_ack_cnt = dut_ack_cnt + 1;
    end

    initial begin
        #1000;
        forever begin
            #2_000_000;
            $display("[%0t ns] sq=%0d ustep=%0d ist=%0d itick=%0d op_go=%b op_done=%b op_ack=%b scan_a=%0d hit_n=%0d rd_i=%0d fill_i=%0d dump_i=%0d tx_busy=%b | scl_fall=%0d sda_fall=%0d",
                $time, dut.sq, dut.ustep, dut.ist, dut.itick, dut.op_go, dut.op_done,
                dut.op_ack, dut.scan_a, dut.hit_n, dut.rd_i, dut.fill_i, dut.dump_i,
                dut.tx_busy, scl_fall, sda_fall);
        end
    end

    //--------------------------------------------------------------------------
    // 主流程
    //--------------------------------------------------------------------------
    integer i;
    initial begin
        $display("=== tb_pl_imx219_probe ===");
        #200 rst_n = 1'b1;

        // 等消息打完：算术上全程约 25 ms，给到 60 ms 足够
        #60_000_000;

        $display("---- UART bytes received: %0d ----", uart_n);
        $display("---- SLAVE ACK count=%0d ; DUT op_ack count=%0d ----", slv_ack_cnt, dut_ack_cnt);
        for (i = 0; i < uart_n; i = i + 1) $write("%c", uart_buf[i]);
        $write("\n");
        $display("------------------------------");

        // ---- 断言 ----
        if (uart_n < 91) begin
            $display("FAIL: UART got only %0d bytes, expected 91", uart_n);
            $finish;
        end
        // 头部
        if (!(uart_buf[0]=="V" && uart_buf[1]=="I" && uart_buf[2]=="G")) begin
            $display("FAIL: message header wrong");
            $finish;
        end
        // 命中数应为 1，命中地址应为 20
        if (uart_buf[25] !== "1") begin
            $display("FAIL: hit count = '%c', expected '1' (only 0x10 should ACK)", uart_buf[25]);
            $finish;
        end
        if (!(uart_buf[28]=="2" && uart_buf[29]=="0")) begin
            $display("FAIL: hit addr = '%c%c', expected '20'", uart_buf[28], uart_buf[29]);
            $finish;
        end
        // 16bit 寄存器地址读到的 chip ID
        if (!(uart_buf[59]=="0" && uart_buf[60]=="2" &&
              uart_buf[62]=="1" && uart_buf[63]=="9")) begin
            $display("FAIL: ID16 = %c%c %c%c %c%c, expected 02 19 00",
                     uart_buf[59],uart_buf[60],uart_buf[62],uart_buf[63],uart_buf[65],uart_buf[66]);
            $finish;
        end
        // 8bit 寄存器地址读到的 chip ID
        if (!(uart_buf[75]=="0" && uart_buf[76]=="2" &&
              uart_buf[78]=="1" && uart_buf[79]=="9")) begin
            $display("FAIL: ID8 = %c%c %c%c %c%c, expected 02 19 00",
                     uart_buf[75],uart_buf[76],uart_buf[78],uart_buf[79],uart_buf[81],uart_buf[82]);
            $finish;
        end

        $display("");
        $display("==== RESULT: PASS ====");
        $display("SCAN hit 0x10; both 16bit and 8bit register addressing read chip ID 02 19");
        $finish;
    end

    // 看门狗
    initial begin
        #400_000_000;
        $display("FAIL: timeout (400 ms sim time) -- sequencer is probably stuck");
        $finish;
    end

endmodule
