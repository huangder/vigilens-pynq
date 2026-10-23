`timescale 1ns / 1ps
//=============================================================================
// tb_pl_cam_detect.v —— 用三个从机模型证明探针能把三种结论分开
//-----------------------------------------------------------------------------
//   S1 相机 + 板载 EEPROM 都在，CAM_CLK 也在
//        ⇒ cam_ack=1、命中 3 个（0x20 相机 / 0xA0 / 0xB0）、MODEL_ID=0x0219
//   S2 只有 EEPROM（相机没插 / 排线断 / 相机坏）
//        ⇒ cam_ack=0、命中 2 个、相机寄存器全 0
//   S3 相机在总线上，但 CAM_CLK 没到
//        ⇒ cam_ack=0、命中 2 个、**mclk_seen=0**（这一条才能指向"时钟"这个因）
//
//  ★ 这三个场景就是判据本身：**只有 S1 全绿才算"连上了"**。
//    报告里绝不能只写"ILA 有数据"，必须写清楚是哪一种签名。
//
//  运行（完整权限终端）：
//    cd /d D:\Desktop\AMD\board\cam_detect
//    xvlog pl_cam_detect.v tb_pl_cam_detect.v
//    xelab -debug typical tb_pl_cam_detect -s tb_sim
//    xsim tb_sim -R
//=============================================================================
module tb_pl_cam_detect;

    // ---- 与 DUT 一致的参数（TB 里缩小，仿真才快）------------------------------
    localparam integer CLK_HZ   = 50_000_000;
    localparam integer SCL_HZ   = 500_000;   // 真机 100k；仿真里 5 倍加速
    localparam integer PWRUP_MS = 1;         // 真机 200；仿真里 1 ms
    localparam integer MEAS_N   = 64;        // 真机 4096

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #10 clk = ~clk;                   // 50 MHz

    tri  mclk, scl, sda;
    reg  drive_mclk = 1'b0;
    reg  mclk_clk   = 1'b0;
    assign mclk = drive_mclk ? mclk_clk : 1'bz;
    always #10 mclk_clk = ~mclk_clk;         // 50 MHz ⇒ 真机上 mclk_period 应 = MEAS_N

    pullup(scl);                             // 板上 4.7K 上拉（probe_pins.xdc 头注）
    pullup(sda);

    wire [3:0]  led;
    wire [31:0] dbg0;
    wire [63:0] dbg1, dbg2, dbg3;

    pl_cam_detect #(
        .CLK_HZ     (CLK_HZ),
        .SCL_HZ     (SCL_HZ),
        .PWRUP_MS   (PWRUP_MS),
        .CAM_ADDR   (7'h10),
        .DRIVE_MCLK (0),
        .MCLK_DIV   (2),
        .MEAS_N     (MEAS_N),
        .RESET_MS   (1)          // 与 PWRUP_MS(1) 一起缩小，仿真才快
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .mclk(mclk), .sccb_scl(scl), .sccb_sda(sda),
        .led(led), .dbg0(dbg0), .dbg1(dbg1), .dbg2(dbg2), .dbg3(dbg3)
    );

    //==========================================================================
    // SCCB 从机模型
    //   蓝本是 board/imx219_probe/tb_pl_imx219_probe.v 里【已仿真通过】的那份；
    //   本版多做了三件事：①按地址过滤（总线上不止一个器件）②支持 1/2 字节寄存器
    //   地址 ③支持 present=0 表示"这个器件不在"。
    //   ★ 关键：STOP 之后**保留寄存器指针**（真实器件就是这样）。这一点若写错，
    //     模型会谎报 DUT 有 bug —— 因为 DUT 的每个 op 之间都夹着 STOP：
    //     写地址 → STOP → 写寄存器号 → STOP → 再 START + 读地址 → 读。
    //==========================================================================
    reg eep0_on = 1'b0, eep1_on = 1'b0, cam_on = 1'b0, mclk_ok = 1'b0;

    sccb_slave #(.RW_ADDR(8'hA0), .R0(8'h11), .R1(8'h22), .R2(8'h33), .R3(8'h44))
        eep0 (.present(eep0_on), .scl(scl), .sda(sda));

    sccb_slave #(.RW_ADDR(8'hB0), .R0(8'h55), .R1(8'h66), .R2(8'h77), .R3(8'h88))
        eep1 (.present(eep1_on), .scl(scl), .sda(sda));

    // 相机：MODEL_ID 高字节在 reg0x0000 ⇒ {0x02,0x19}（IMX219 真值）
    //       ★ present 里带上 mclk_ok：**没时钟的器件不会应答 SCCB**（S3 的意义所在）
    sccb_slave #(.RW_ADDR(8'h20), .R0(8'h02), .R1(8'h19), .R2(8'h00), .R3(8'h00))
        cam (.present(cam_on && mclk_ok), .scl(scl), .sda(sda));

    //==========================================================================
    // 记分板
    //==========================================================================
    integer errs = 0;
    reg [4:0]  s_hitn;
    reg [7:0]  s_h0, s_h1, s_h2;
    reg [7:0]  s_h5, s_h6;      // ★ 2026-10-20：总线负载测量值（STEP_RISE 写进 hit_a[5]/[6]）
    reg        s_camack, s_seen;
    reg [15:0] s_period;
    reg [7:0]  s_npulse;
    reg [31:0] s_rec0, s_rec1, s_cam16, s_cam8;

    task snapshot;
        begin
            s_hitn   = dut.hit_n;
            s_h0     = dut.hit_a[0];
            s_h1     = dut.hit_a[1];
            s_h2     = dut.hit_a[2];
            s_h5     = dut.hit_a[5];
            s_h6     = dut.hit_a[6];
            s_camack = dut.cam_ack;
            s_seen   = dut.mclk_seen;
            s_period = dut.mclk_period;
            s_npulse = dut.mclk_npulse;
            s_rec0   = dut.rec0;
            s_rec1   = dut.rec1;
            s_cam16  = dut.cam16_rec;
            s_cam8   = dut.cam8_rec;
        end
    endtask

    task chk;
        input [255:0] name;
        input [31:0]  got;
        input [31:0]  exp;
        begin
            if (got !== exp) begin
                $display("  [FAIL] %0s: got 0x%h, want 0x%h", name, got, exp);
                errs = errs + 1;
            end else begin
                $display("  [ ok ] %0s = 0x%h", name, got);
            end
        end
    endtask

    task chk_range;
        input [255:0] name;
        input integer got, lo, hi;
        begin
            if (got < lo || got > hi) begin
                $display("  [FAIL] %0s: got %0d, want %0d..%0d", name, got, lo, hi);
                errs = errs + 1;
            end else begin
                $display("  [ ok ] %0s = %0d (在 %0d..%0d 内)", name, got, lo, hi);
            end
        end
    endtask

    task reset_pulse;
        begin
            rst_n = 1'b0;
            #200;
            rst_n = 1'b1;
        end
    endtask

    // 等 STEP_DONE（4'd7），超时 = 定序器卡死 ⇒ 直接判 FAIL
    task wait_done;
        input integer maxcyc;
        integer n;
        begin
            n = 0;
            while (dut.step !== 4'd7 && n < maxcyc) begin
                @(posedge clk);
                n = n + 1;
            end
            if (dut.step !== 4'd7) begin
                $display("  [FAIL] 定序器超时未到 DONE：step=%0d ms=%0d scan_a=0x%h hit_n=%0d",
                         dut.step, dut.ms, dut.scan_a, dut.hit_n);
                errs = errs + 1;
            end
            repeat (10) @(posedge clk);   // 让最后一拍的写回落地
        end
    endtask

    task show;
        input [255:0] tag;
        begin
            $display("%0s: step=%0d hit_n=%0d hit=[%02h %02h %02h] cam_ack=%b mclk_seen=%b period=%0d npulse=%0d",
                     tag, dut.step, s_hitn, s_h0, s_h1, s_h2, s_camack, s_seen, s_period, s_npulse);
            $display("        rec0=%08h rec1=%08h cam16=%08h cam8=%08h",
                     s_rec0, s_rec1, s_cam16, s_cam8);
        end
    endtask

    //==========================================================================
    // 主流程
    //==========================================================================
    initial begin
        $display("===== tb_pl_cam_detect start (CLK_HZ=%0d SCL_HZ=%0d MEAS_N=%0d) =====",
                 CLK_HZ, SCL_HZ, MEAS_N);

        //----------------------------------------------------------------------
        // S1：相机 + EEPROM + 时钟 都在  ⇒ 应判"连上了"
        //----------------------------------------------------------------------
        $display("");
        $display("---- S1: 相机 + EEPROM 都在，CAM_CLK 也在 ----");
        eep0_on = 1'b1; eep1_on = 1'b1; cam_on = 1'b1; mclk_ok = 1'b1; drive_mclk = 1'b1;
        reset_pulse;
        wait_done(3_000_000);
        snapshot;
        show("S1");
        chk("S1.cam_ack",      {31'b0, s_camack}, 32'd1);
        chk("S1.hit_n",        {27'b0, s_hitn},   32'd3);
        chk("S1.hit_a[0]",     {24'b0, s_h0},     32'h20);   // 相机 7bit 0x10 ⇒ 8bit 0x20
        chk("S1.hit_a[1]",     {24'b0, s_h1},     32'hA0);   // 板载 EEPROM
        chk("S1.hit_a[2]",     {24'b0, s_h2},     32'hB0);
        chk("S1.mclk_seen",    {31'b0, s_seen},   32'd1);
        chk_range("S1.mclk_period", s_period, MEAS_N - 4, MEAS_N + 8);
        chk("S1.rec0=MODEL_ID",  s_rec0 >> 16,    32'h0219); // 扫描命中槽 0 = 相机
        chk("S1.rec1=EEPROM0",   s_rec1,          32'h11223344);
        chk("S1.cam16=MODEL_ID", s_cam16 >> 16,   32'h0219);
        chk("S1.cam8=MODEL_ID",  s_cam8  >> 16,   32'h0219);

        //----------------------------------------------------------------------
        // S2：只有 EEPROM（相机不在）  ⇒ cam_ack 必须为 0
        //----------------------------------------------------------------------
        $display("");
        $display("---- S2: 只有板载 EEPROM（相机不在/排线断/相机坏）----");
        eep0_on = 1'b1; eep1_on = 1'b1; cam_on = 1'b0; mclk_ok = 1'b1; drive_mclk = 1'b1;
        reset_pulse;
        wait_done(3_000_000);
        snapshot;
        show("S2");
        chk("S2.cam_ack",   {31'b0, s_camack}, 32'd0);
        chk("S2.hit_n",     {27'b0, s_hitn},   32'd2);
        chk("S2.hit_a[0]",  {24'b0, s_h0},     32'hA0);
        chk("S2.hit_a[1]",  {24'b0, s_h1},     32'hB0);
        chk("S2.rec0=EEPROM0", s_rec0, 32'h11223344);
        chk("S2.rec1=EEPROM1", s_rec1, 32'h55667788);
        chk("S2.cam16=0",      s_cam16, 32'h00000000);  // 相机那两站被跳过
        chk("S2.cam8=0",       s_cam8,  32'h00000000);
        // ★ 2026-10-20 新增：相机不在时，STEP_RISE 会把"释放后仍被采成低"的拍数
        //   写进 hit_a[5]/hit_a[6]。仿真里上拉是理想的 ⇒ 只该有几拍（同步器延迟）。
        //   上板时这个数会明显变大（4.7k×总线电容），从而能对比"插/拔模组"两轮。
        chk_range("S2.rise_scl", {24'b0, s_h5}, 1, 12);
        chk_range("S2.rise_sda", {24'b0, s_h6}, 1, 12);

        //----------------------------------------------------------------------
        // S3：相机在总线上，但 CAM_CLK 没到  ⇒ mclk_seen=0
        //----------------------------------------------------------------------
        $display("");
        $display("---- S3: 相机在，但 CAM_CLK 没到（50M_OSC/R156/pin12 那条路）----");
        eep0_on = 1'b1; eep1_on = 1'b1; cam_on = 1'b1; mclk_ok = 1'b0; drive_mclk = 1'b0;
        reset_pulse;
        wait_done(3_000_000);
        snapshot;
        show("S3");
        chk("S3.cam_ack",    {31'b0, s_camack}, 32'd0);
        chk("S3.hit_n",      {27'b0, s_hitn},   32'd2);
        chk("S3.mclk_seen",  {31'b0, s_seen},   32'd0);   // ★ 这一条把"时钟"钉成嫌疑
        chk("S3.mclk_period",{16'b0, s_period}, 32'd0);
        chk("S3.cam16=0",    s_cam16, 32'h00000000);

        //----------------------------------------------------------------------
        $display("");
        if (errs == 0) $display("==== RESULT: PASS ====");
        else           $display("==== RESULT: FAIL (%0d 项) ====", errs);
        $finish;
    end

    // 兜底看门狗：真卡死了也要给出可读结论，而不是挂到天荒地老
    initial begin
        #50_000_000;   // 50 ms 仿真时间
        $display("==== RESULT: FAIL (仿真总超时 50ms，定序器未跑完) ====");
        $finish;
    end

endmodule


//=============================================================================
// SCCB（I2C）从机模型
//   · 只实现"被读"这一半 + 地址/寄存器地址的 ACK；不实现写数据（我们的探针不写）
//   · 寄存器空间按 256 字节实现：够读 0x00..0x03
//   · 支持 1 字节 或 2 字节 寄存器地址（探针两种口径各读一遍）
//=============================================================================
module sccb_slave #(
    parameter [7:0] RW_ADDR = 8'hA0,   // 8bit 写地址；读地址 = RW_ADDR | 1
    parameter [7:0] R0 = 8'h00,
    parameter [7:0] R1 = 8'h00,
    parameter [7:0] R2 = 8'h00,
    parameter [7:0] R3 = 8'h00
)(
    input  wire present,               // 0 = 这个器件不在总线上
    inout  wire scl,
    inout  wire sda
);
    integer i;
    reg [7:0] mem [0:255];
    initial begin
        for (i = 0; i < 256; i = i + 1) mem[i] = 8'h00;
        mem[0] = R0; mem[1] = R1; mem[2] = R2; mem[3] = R3;
    end

    reg drive_low = 1'b0;
    assign sda = (present && drive_low) ? 1'b0 : 1'bz;

    wire [7:0] rd_addr = {RW_ADDR[7:1], 1'b1};

    reg       in_frame  = 1'b0;
    reg [2:0] rbit      = 3'd0;
    reg [7:0] rxsh      = 8'h00;
    reg       ack_slot  = 1'b0;
    reg       ack_done  = 1'b0;
    reg [3:0] byte_i    = 4'd0;    // 本帧第几个字节（0 = 地址字节）
    reg       matched   = 1'b0;
    reg       rd_mode   = 1'b0;    // 处于读相
    // ⚠️ 2026-10-12 修正：原版用 `reg [3:0] n_addr` 累计"收到的寄存器地址字节数"，
    //    它只有 4 位、又从不复位 ⇒ 跨场景（S1→S2→S3）与多次读取后**回绕到 0**，
    //    于是 if (n_addr == 0) 的分支把"本次写指针的第二个字节"错当成 a_hi 丢掉，
    //    指针停在上一次的 0x00 ⇒ 第 4 个字节读成 R0（S2 got 0x11223311）。
    //    **这是测试台模型的 bug，不是 DUT 的**：dbg_rec3 里模型是全新的（n_addr 从 0 开始）
    //    同样四个字节全对；DUT 发出的字节流（a0,00,03,a1）从头到尾都是对的。
    //    改为：本帧地址字节数 `awr`（START 清零）+ 持久化的指针 hi/lo（STOP 保留 = 真实器件）。
    reg [1:0] awr       = 2'd0;    // 本帧已收到的寄存器地址字节数
    reg [7:0] ptr_hi    = 8'h00;   // 指针高字节（8bit 短地址口径下它本身就是指针）
    reg [7:0] ptr_lo    = 8'h00;   // 指针低字节
    reg       use_lo    = 1'b0;    // 上一次写指针用的是"2 字节口径"（用 ptr_lo）
                                   // ★ 不能用本帧的 awr 判断：重复 START 会把 awr 清零，
                                   //   于是读相会被误判成"0 字节地址帧"⇒ 读到陈旧的 ptr_lo。
    reg       t_active  = 1'b0;    // 正在发数据位
    reg [2:0] tbit      = 3'd0;    // 当前摆在总线上的位号（0 = D7）
    reg [7:0] txbyte    = 8'h00;
    reg       wait_ack  = 1'b0;    // 8 位发完，等主机应答
    reg       m_ack     = 1'b0;

    // 上一次写指针的口径决定读哪个字节：
    //   2 字节地址（IMX219 / 24C32 的常规口径）⇒ ptr_lo
    //   1 字节地址（探针的 8bit 变体）        ⇒ ptr_hi
    //   纯读帧（本帧没写地址）⇒ 沿用上一次的口径（真实器件 STOP/重复 START 后保留指针）
    wire [7:0] areg = use_lo ? ptr_lo : ptr_hi;

    // ---- START ----
    always @(negedge sda) if (present && scl === 1'b1) begin
        in_frame <= 1'b1; rbit <= 3'd0; ack_slot <= 1'b0; ack_done <= 1'b0;
        byte_i <= 4'd0; matched <= 1'b0; rd_mode <= 1'b0; awr <= 2'd0;
        t_active <= 1'b0; wait_ack <= 1'b0; drive_low <= 1'b0;
        // ★ 刻意不清 ptr_hi/ptr_lo：重复 START 后要接着用刚写进去的寄存器号
    end

    // ---- STOP ----
    always @(posedge sda) if (present && in_frame && scl === 1'b1) begin
        in_frame <= 1'b0; t_active <= 1'b0; wait_ack <= 1'b0; drive_low <= 1'b0;
        // ★ 刻意不清 ptr_hi/ptr_lo：真实器件 STOP 之后保留寄存器指针 ——
        //   探针每个 op 之间都夹 STOP（写地址 STOP 写寄存器号 STOP 再 START 读），
        //   若这里清掉，模型就会谎报"DUT 读不到"。
    end

    // ---- 收位（SCL 上升沿采样）----
    always @(posedge scl) if (present && in_frame && !ack_slot && !t_active) begin
        rxsh <= {rxsh[6:0], sda};
        if (rbit == 3'd7) begin rbit <= 3'd0; ack_slot <= 1'b1; ack_done <= 1'b0; end
        else              rbit <= rbit + 3'd1;
    end
    // ---- 采样主机在数据相末的应答位（低 = ACK = 还要下一字节）----
    always @(posedge scl) if (present && in_frame && wait_ack) m_ack <= ~sda;

    // ---- 驱动 / 应答（SCL 下降沿）----
    always @(negedge scl) if (present && in_frame) begin
        if (ack_slot && !ack_done) begin
            // ==== 一个字节刚收完：决定 ACK 还是不理 ====
            ack_done <= 1'b1;
            if (byte_i == 4'd0) begin
                if (rxsh == RW_ADDR)     begin matched <= 1'b1; rd_mode <= 1'b0; drive_low <= 1'b1; end
                else if (rxsh == rd_addr)begin matched <= 1'b1; rd_mode <= 1'b1; drive_low <= 1'b1; end
                else                     begin matched <= 1'b0; drive_low <= 1'b0; end
                byte_i <= byte_i + 4'd1;
            end else if (!matched) begin
                drive_low <= 1'b0;                      // 不是给我的，整帧都别碰总线
            end else if (!rd_mode) begin
                // 写相：收寄存器地址字节（第 1 个进 hi，第 2 个进 lo）
                if (awr == 2'd0) begin
                    ptr_hi <= rxsh;
                    use_lo <= 1'b0;
                end else begin
                    ptr_lo <= rxsh;
                    use_lo <= 1'b1;
                end
                awr       <= awr + 2'd1;
                byte_i    <= byte_i + 4'd1;
                drive_low <= 1'b1;                      // ACK
            end else begin
                drive_low <= 1'b0;
            end
        end else if (ack_slot && ack_done) begin
            // ==== 应答位结束：若是读相，**此刻**就得把 D7 摆上总线 ====
            //      因为应答位之后 SCL 一直停在低电平，等下一个下降沿才摆就晚一位。
            ack_slot <= 1'b0; ack_done <= 1'b0;
            if (matched && rd_mode) begin
                txbyte    <= mem[areg];
                drive_low <= ~mem[areg][7];
                tbit      <= 3'd0;
                t_active  <= 1'b1;
            end else begin
                drive_low <= 1'b0;
            end
        end else if (t_active) begin
            if (wait_ack) begin
                wait_ack <= 1'b0;
                if (m_ack) begin                        // 主机 ACK ⇒ 还有下一字节
                    txbyte    <= mem[areg + 8'd1];
                    drive_low <= ~mem[areg + 8'd1][7];
                    tbit      <= 3'd0;
                end else begin                          // 主机 NACK ⇒ 收工，等 STOP
                    t_active  <= 1'b0;
                    drive_low <= 1'b0;
                end
            end else if (tbit == 3'd7) begin
                drive_low <= 1'b0;                      // 8 位发完，释放总线给主机应答
                wait_ack  <= 1'b1;
            end else begin
                tbit      <= tbit + 3'd1;
                drive_low <= ~txbyte[6 - tbit];
            end
        end
    end

endmodule
