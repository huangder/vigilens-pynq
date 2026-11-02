// =============================================================================
// tb_pl_chain.v —— ROI -> rgb2gray -> motion_quality 的 RTL 数据通路仿真
// -----------------------------------------------------------------------------
// 这不是上板，也不是 PYNQ Overlay：
//   直接例化三个 Vitis HLS 导出的 Verilog IP，绕开 PS7 / DMA / AXI 互联，
//   用 AXI-Stream 逐像素喂帧，用 AXI-Lite 配置并读回结果。
//
// 目的：在 PC 上验证三条冻结口径
//   1) RGB 通道顺序 R=[7:0], G=[15:8], B=[23:16]
//   2) 灰度 Y=(77R+150G+29B+128)>>8，3/5 相位抽取
//   3) motion_quality：第 1 帧无效；|cur-prev| > thresh 才算运动；ratio 向下取整
//
// 输入：
//   frames.bin（当前目录）  frame-major RGB888
//   sim_config.vh（`include）  尺寸与帧数；由 board/sim/gen_sim_frames.py 生成
// 输出（可被 check_xsim_log.py 解析）：
//   SIM f=<n> roi_sum_r=.. roi_sum_g=.. roi_sum_b=.. roi_count=..
//           rgb_pixels=.. rgb_sum=.. mot_diff=.. mot_pixels=.. mot_ratio=..
//           mot_count=.. roi_fid=.. rgb_fid=.. mot_fid=..
// =============================================================================
`timescale 1 ns / 1 ps
`include "sim_config.vh"

module tb_pl_chain;

    localparam integer W  = `SIM_W;
    localparam integer H  = `SIM_H;
    localparam integer GW = `SIM_GW;
    localparam integer GH = `SIM_GH;
    localparam integer FRAME_BYTES = W * H * 3;
    localparam integer NFRAMES = `SIM_NFRAMES;

    reg clk = 1'b0;
    reg rst_n = 1'b0;
    always #5 clk = ~clk;  // 100 MHz

    // ---- 输入 AXI-Stream -----------------------------------------------------
    reg  [23:0] in_tdata = 24'd0;
    reg         in_tvalid = 1'b0;
    wire        in_tready;
    reg  [2:0]  in_tkeep = 3'b111;
    reg  [2:0]  in_tstrb = 3'b111;
    reg         in_tuser = 1'b0;
    reg         in_tlast = 1'b0;

    // ---- 中间/输出 AXI-Stream ------------------------------------------------
    wire [23:0] roi_tdata;
    wire        roi_tvalid;
    wire        roi_tready;
    wire [2:0]  roi_tkeep;
    wire [2:0]  roi_tstrb;
    wire        roi_tuser;
    wire        roi_tlast;

    wire [7:0]  gray_tdata;
    wire        gray_tvalid;
    wire        gray_tready;
    wire        gray_tkeep;
    wire        gray_tstrb;
    wire        gray_tuser;
    wire        gray_tlast;

    wire [7:0]  mot_tdata;
    wire        mot_tvalid;
    wire        mot_tready;
    wire        mot_tkeep;
    wire        mot_tstrb;
    wire        mot_tuser;
    wire        mot_tlast;

    // ---- 单一 AXI-Lite 主口，按 bus_sel 切换到三个 IP -----------------------
    reg  [1:0]  bus_sel = 2'd0;
    reg  [6:0]  m_awaddr = 7'd0;
    reg         m_awvalid = 1'b0;
    wire        m_awready;
    reg  [31:0] m_wdata = 32'd0;
    reg  [3:0]  m_wstrb = 4'hF;
    reg         m_wvalid = 1'b0;
    wire        m_wready;
    wire        m_bvalid;
    reg         m_bready = 1'b0;

    reg  [6:0]  m_araddr = 7'd0;
    reg         m_arvalid = 1'b0;
    wire        m_arready;
    wire        m_rvalid;
    reg         m_rready = 1'b0;
    wire [31:0] m_rdata;

    // ROI AXI-Lite
    wire roi_awready, roi_wready, roi_bvalid, roi_arready, roi_rvalid;
    wire [31:0] roi_rdata;
    // RGB AXI-Lite
    wire rgb_awready, rgb_wready, rgb_bvalid, rgb_arready, rgb_rvalid;
    wire [31:0] rgb_rdata;
    // MOT AXI-Lite
    wire mot_awready, mot_wready, mot_bvalid, mot_arready, mot_rvalid;
    wire [31:0] mot_rdata;

    wire sel_roi = (bus_sel == 2'd0);
    wire sel_rgb = (bus_sel == 2'd1);
    wire sel_mot = (bus_sel == 2'd2);

    assign m_awready = sel_roi ? roi_awready : sel_rgb ? rgb_awready : mot_awready;
    assign m_wready  = sel_roi ? roi_wready  : sel_rgb ? rgb_wready  : mot_wready;
    assign m_bvalid  = sel_roi ? roi_bvalid  : sel_rgb ? rgb_bvalid  : mot_bvalid;
    assign m_arready = sel_roi ? roi_arready : sel_rgb ? rgb_arready : mot_arready;
    assign m_rvalid  = sel_roi ? roi_rvalid  : sel_rgb ? rgb_rvalid  : mot_rvalid;
    assign m_rdata   = sel_roi ? roi_rdata   : sel_rgb ? rgb_rdata   : mot_rdata;

    // ---- 三个 HLS IP ---------------------------------------------------------
    roi_statistic u_roi (
        .ap_clk(clk), .ap_rst_n(rst_n),
        .video_in_TDATA(in_tdata), .video_in_TVALID(in_tvalid),
        .video_in_TREADY(in_tready), .video_in_TKEEP(in_tkeep),
        .video_in_TSTRB(in_tstrb), .video_in_TUSER(in_tuser),
        .video_in_TLAST(in_tlast), .video_in_TID(1'b0), .video_in_TDEST(1'b0),
        .video_out_TDATA(roi_tdata), .video_out_TVALID(roi_tvalid),
        .video_out_TREADY(roi_tready), .video_out_TKEEP(roi_tkeep),
        .video_out_TSTRB(roi_tstrb), .video_out_TUSER(roi_tuser),
        .video_out_TLAST(roi_tlast), .video_out_TID(), .video_out_TDEST(),
        .s_axi_ctrl_AWVALID(sel_roi & m_awvalid), .s_axi_ctrl_AWREADY(roi_awready),
        .s_axi_ctrl_AWADDR(m_awaddr),
        .s_axi_ctrl_WVALID(sel_roi & m_wvalid), .s_axi_ctrl_WREADY(roi_wready),
        .s_axi_ctrl_WDATA(m_wdata), .s_axi_ctrl_WSTRB(m_wstrb),
        .s_axi_ctrl_ARVALID(sel_roi & m_arvalid), .s_axi_ctrl_ARREADY(roi_arready),
        .s_axi_ctrl_ARADDR(m_araddr),
        .s_axi_ctrl_RVALID(roi_rvalid), .s_axi_ctrl_RREADY(sel_roi & m_rready),
        .s_axi_ctrl_RDATA(roi_rdata), .s_axi_ctrl_RRESP(),
        .s_axi_ctrl_BVALID(roi_bvalid), .s_axi_ctrl_BREADY(sel_roi & m_bready),
        .s_axi_ctrl_BRESP(), .interrupt()
    );

    rgb2gray u_rgb (
        .ap_clk(clk), .ap_rst_n(rst_n),
        .rgb_in_TDATA(roi_tdata), .rgb_in_TVALID(roi_tvalid),
        .rgb_in_TREADY(roi_tready), .rgb_in_TKEEP(roi_tkeep),
        .rgb_in_TSTRB(roi_tstrb), .rgb_in_TUSER(roi_tuser),
        .rgb_in_TLAST(roi_tlast), .rgb_in_TID(1'b0), .rgb_in_TDEST(1'b0),
        .gray_out_TDATA(gray_tdata), .gray_out_TVALID(gray_tvalid),
        .gray_out_TREADY(gray_tready), .gray_out_TKEEP(gray_tkeep),
        .gray_out_TSTRB(gray_tstrb), .gray_out_TUSER(gray_tuser),
        .gray_out_TLAST(gray_tlast), .gray_out_TID(), .gray_out_TDEST(),
        .s_axi_ctrl_AWVALID(sel_rgb & m_awvalid), .s_axi_ctrl_AWREADY(rgb_awready),
        .s_axi_ctrl_AWADDR(m_awaddr),
        .s_axi_ctrl_WVALID(sel_rgb & m_wvalid), .s_axi_ctrl_WREADY(rgb_wready),
        .s_axi_ctrl_WDATA(m_wdata), .s_axi_ctrl_WSTRB(m_wstrb),
        .s_axi_ctrl_ARVALID(sel_rgb & m_arvalid), .s_axi_ctrl_ARREADY(rgb_arready),
        .s_axi_ctrl_ARADDR(m_araddr),
        .s_axi_ctrl_RVALID(rgb_rvalid), .s_axi_ctrl_RREADY(sel_rgb & m_rready),
        .s_axi_ctrl_RDATA(rgb_rdata), .s_axi_ctrl_RRESP(),
        .s_axi_ctrl_BVALID(rgb_bvalid), .s_axi_ctrl_BREADY(sel_rgb & m_bready),
        .s_axi_ctrl_BRESP(), .interrupt()
    );

    motion_quality u_mot (
        .ap_clk(clk), .ap_rst_n(rst_n),
        .gray_in_TDATA(gray_tdata), .gray_in_TVALID(gray_tvalid),
        .gray_in_TREADY(gray_tready), .gray_in_TKEEP(gray_tkeep),
        .gray_in_TSTRB(gray_tstrb), .gray_in_TUSER(gray_tuser),
        .gray_in_TLAST(gray_tlast), .gray_in_TID(1'b0), .gray_in_TDEST(1'b0),
        .gray_out_TDATA(mot_tdata), .gray_out_TVALID(mot_tvalid),
        .gray_out_TREADY(mot_tready), .gray_out_TKEEP(mot_tkeep),
        .gray_out_TSTRB(mot_tstrb), .gray_out_TUSER(mot_tuser),
        .gray_out_TLAST(mot_tlast), .gray_out_TID(), .gray_out_TDEST(),
        .s_axi_ctrl_AWVALID(sel_mot & m_awvalid), .s_axi_ctrl_AWREADY(mot_awready),
        .s_axi_ctrl_AWADDR(m_awaddr),
        .s_axi_ctrl_WVALID(sel_mot & m_wvalid), .s_axi_ctrl_WREADY(mot_wready),
        .s_axi_ctrl_WDATA(m_wdata), .s_axi_ctrl_WSTRB(m_wstrb),
        .s_axi_ctrl_ARVALID(sel_mot & m_arvalid), .s_axi_ctrl_ARREADY(mot_arready),
        .s_axi_ctrl_ARADDR(m_araddr),
        .s_axi_ctrl_RVALID(mot_rvalid), .s_axi_ctrl_RREADY(sel_mot & m_rready),
        .s_axi_ctrl_RDATA(mot_rdata), .s_axi_ctrl_RRESP(),
        .s_axi_ctrl_BVALID(mot_bvalid), .s_axi_ctrl_BREADY(sel_mot & m_bready),
        .s_axi_ctrl_BRESP(), .interrupt()
    );

    assign mot_tready = 1'b1;  // 最末端永远接收，避免反压死锁

    // ---- frames.bin ----------------------------------------------------------
    reg [7:0] frame_bytes [0:FRAME_BYTES*NFRAMES-1];
    reg [1023:0] frames_path;
    integer fd, nread;

    // ---- AXI-Lite 任务 -------------------------------------------------------
    task axil_write(input [6:0] addr, input [31:0] data, input [1:0] sel);
        begin
            @(posedge clk);
            bus_sel  <= sel;
            m_awaddr <= addr;
            m_awvalid <= 1'b1;
            m_wdata  <= data;
            m_wstrb  <= 4'hF;
            m_wvalid <= 1'b1;
            m_bready <= 1'b1;
            // HLS 的 AWREADY 只在 WRIDLE 高、WREADY 只在 WRDATA 高，
            // 两者不会同时为高，必须分开握手。
            @(negedge clk);
            while (!m_awready) @(negedge clk);
            @(posedge clk);
            m_awvalid <= 1'b0;
            @(negedge clk);
            while (!m_wready) @(negedge clk);
            @(posedge clk);
            m_wvalid <= 1'b0;
            @(negedge clk);
            while (!m_bvalid) @(negedge clk);
            @(posedge clk);
            m_bready <= 1'b0;
        end
    endtask

    task axil_read(input [6:0] addr, input [1:0] sel, output [31:0] data);
        begin
            @(posedge clk);
            bus_sel  <= sel;
            m_araddr <= addr;
            m_arvalid <= 1'b1;
            m_rready <= 1'b1;
            @(negedge clk);
            while (!m_arready) @(negedge clk);
            @(posedge clk);
            m_arvalid <= 1'b0;
            @(negedge clk);
            while (!m_rvalid) @(negedge clk);
            data = m_rdata;
            @(posedge clk);
            m_rready <= 1'b0;
        end
    endtask

    task start_ip(input [1:0] sel);
        begin
            axil_write(7'h00, 32'h1, sel);
        end
    endtask

    task wait_done(input [1:0] sel);
        reg [31:0] ctrl;
        integer t;
        begin
            ctrl = 32'd0;
            t = 0;
            while (!ctrl[1] && t < 2000000) begin
                axil_read(7'h00, sel, ctrl);
                t = t + 1;
            end
            if (!ctrl[1]) begin
                $display("ERROR timeout waiting ap_done sel=%0d ctrl=0x%08X polls=%0d", sel, ctrl, t);
                $finish;
            end
        end
    endtask

    // ---- AXI-Stream 发送 -----------------------------------------------------
    task send_pixel(input [23:0] d, input u, input l);
        begin
            in_tdata  <= d;
            in_tuser  <= u;
            in_tlast  <= l;
            in_tkeep  <= 3'b111;
            in_tstrb  <= 3'b111;
            in_tvalid <= 1'b1;
            @(negedge clk);
            while (!in_tready) @(negedge clk);
            @(posedge clk);
            in_tvalid <= 1'b0;
            in_tuser  <= 1'b0;
            in_tlast  <= 1'b0;
        end
    endtask

    task send_frame(input integer f);
        integer x, y, idx;
        begin
            for (y = 0; y < H; y = y + 1) begin
                for (x = 0; x < W; x = x + 1) begin
                    idx = f * FRAME_BYTES + (y * W + x) * 3;
                    send_pixel(
                        {frame_bytes[idx + 2], frame_bytes[idx + 1], frame_bytes[idx]},
                        (x == 0 && y == 0),
                        (x == W - 1)
                    );
                end
            end
        end
    endtask

    // ---- 主流程 --------------------------------------------------------------
    integer f;
    reg [31:0] roi_sr, roi_sg, roi_sb, roi_cnt, roi_fid;
    reg [31:0] rgb_ow, rgb_oh, rgb_pc, rgb_sum, rgb_fid;
    reg [31:0] mot_diff, mot_pix, mot_ratio, mot_cnt, mot_fid;

    initial begin
        if (!$value$plusargs("FRAMES=%s", frames_path)) begin
            frames_path = "frames.bin";
        end
        fd = $fopen(frames_path, "rb");
        if (fd == 0) begin
            $display("ERROR cannot open %0s", frames_path);
            $finish;
        end
        nread = $fread(frame_bytes, fd);
        $fclose(fd);
        $display("SIM loaded bytes=%0d (expect %0d)", nread, FRAME_BYTES * NFRAMES);

        repeat (10) @(posedge clk);
        rst_n <= 1'b1;
        repeat (10) @(posedge clk);
        // ROI 全帧
        axil_write(7'h10, 0, 2'd0);
        axil_write(7'h18, 0, 2'd0);
        axil_write(7'h20, W, 2'd0);
        axil_write(7'h28, H, 2'd0);
        axil_write(7'h30, W, 2'd0);
        axil_write(7'h38, H, 2'd0);
        // RGB 全帧
        axil_write(7'h10, W, 2'd1);
        axil_write(7'h18, H, 2'd1);
        // MOT 灰度尺寸/阈值
        axil_write(7'h10, GW, 2'd2);
        axil_write(7'h18, GH, 2'd2);
        axil_write(7'h20, 16, 2'd2);
        for (f = 0; f < NFRAMES; f = f + 1) begin
            start_ip(2'd0);
            start_ip(2'd1);
            start_ip(2'd2);
            send_frame(f);
            wait_done(2'd0);
            wait_done(2'd1);
            wait_done(2'd2);

            axil_read(7'h40, 2'd0, roi_sr);
            axil_read(7'h48, 2'd0, roi_sg);
            axil_read(7'h50, 2'd0, roi_sb);
            axil_read(7'h58, 2'd0, roi_cnt);
            axil_read(7'h60, 2'd0, roi_fid);
            axil_read(7'h20, 2'd1, rgb_ow);
            axil_read(7'h28, 2'd1, rgb_oh);
            axil_read(7'h30, 2'd1, rgb_pc);
            axil_read(7'h38, 2'd1, rgb_sum);
            axil_read(7'h40, 2'd1, rgb_fid);
            axil_read(7'h28, 2'd2, mot_diff);
            axil_read(7'h30, 2'd2, mot_pix);
            axil_read(7'h38, 2'd2, mot_ratio);
            axil_read(7'h40, 2'd2, mot_cnt);
            axil_read(7'h48, 2'd2, mot_fid);

            $display("SIM f=%0d roi_sum_r=%0d roi_sum_g=%0d roi_sum_b=%0d roi_count=%0d rgb_pixels=%0d rgb_sum=%0d mot_diff=%0d mot_pixels=%0d mot_ratio=%0d mot_count=%0d roi_fid=%0d rgb_fid=%0d mot_fid=%0d",
                     f + 1, roi_sr, roi_sg, roi_sb, roi_cnt, rgb_pc, rgb_sum,
                     mot_diff, mot_pix, mot_ratio, mot_cnt, roi_fid, rgb_fid, mot_fid);
        end
        $display("SIM done");
        $finish;
    end

    // 防全局挂死
    initial begin
        #30000000;  // 30 ms 仿真时间（3 帧约 18 ms，留余量；防挂死）
        $display("ERROR global timeout");
        $finish;
    end

endmodule
