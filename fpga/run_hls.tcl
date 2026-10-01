# =============================================================================
#  run_hls.tcl —— C 线 IP 的 C 仿真 + C 综合（多 IP 通用）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#
#  执行（在 fpga/ 目录下，且已 call settings64.bat）：
#      vitis-run --mode hls --tcl run_hls.tcl
#
#  选择要跑的 IP（环境变量 HLS_IP，默认 roi_statistic）：
#      set HLS_IP=motion_quality
#      vitis-run --mode hls --tcl run_hls.tcl
#
#  前置（生成测试向量与 Python 黄金参考；数据变化时才需重跑）：
#      python fpga/sim/gen_frames.py           -> sim/data/        (roi_statistic)
#      python fpga/sim/gen_motion_vectors.py   -> sim/data_motion/ (rgb2gray, motion_quality)
#      python fpga/sim/gen_fir_vectors.py      -> sim/data_fir/    (fir_filter)
#      python fpga/sim/gen_mipi_vectors.py     -> sim/data_mipi/   (raw10_unpack, bayer_demosaic)
#      python fpga/sim/gen_scale_vectors.py    -> sim/data_scale/  (frame_scale)
#
#  契约：docs/interface.md —— 器件/时钟/寄存器映射/比对口径均已冻结。
# =============================================================================

# ---- 选择 IP ---------------------------------------------------------------
set ip_name "roi_statistic"
if {[info exists ::env(HLS_IP)] && $::env(HLS_IP) ne ""} {
    set ip_name $::env(HLS_IP)
}

# ⚠️ raw10_unpack / bayer_demosaic 是 docs/19 的**新像素源链路** IP：
#    接口口径尚未进入 docs/interface.md（提案见 docs/19 §6，待 A/B 会签），
#    故它们可以 csim/csynth，但在契约冻结前**不得**被当作已冻结接口对接。
set known_ips [list roi_statistic rgb2gray motion_quality fir_filter raw10_unpack bayer_demosaic frame_scale]
if {[lsearch -exact $known_ips $ip_name] < 0} {
    puts "ERROR: unknown HLS_IP '$ip_name'. Known: $known_ips"
    exit 1
}

# IP -> 默认数据目录
set default_data "sim/data"
if {$ip_name eq "rgb2gray" || $ip_name eq "motion_quality"} {
    set default_data "sim/data_motion"
} elseif {$ip_name eq "fir_filter"} {
    set default_data "sim/data_fir"
} elseif {$ip_name eq "raw10_unpack" || $ip_name eq "bayer_demosaic"} {
    set default_data "sim/data_mipi"
} elseif {$ip_name eq "frame_scale"} {
    set default_data "sim/data_scale"
}

# ---- fir_filter 的 60 Hz 档（2026-09-29 新增）-------------------------------
#   用法：  set "FIR_FS=60"   然后照常跑 run_hls.tcl（HLS_IP=fir_filter）
#   60 Hz 必须用 N=127 的系数表（63 抽头在 60 Hz 下停止带会从 -16 dB 退化到 -6 dB），
#   所以它有自己的黄金参考目录；不设这个变量时一切照旧走 30 Hz 的 sim/data_fir。
set fir_60 0
if {$ip_name eq "fir_filter" && [info exists ::env(FIR_FS)] && $::env(FIR_FS) eq "60"} {
    set fir_60 1
    set default_data "sim/data_fir_60hz"
    puts "INFO: fir_filter 60 Hz 档（N=127 系数表 + sim/data_fir_60hz 黄金参考）"
}

# ---- 测量口径档位（2026-09-30 新增）----------------------------------------
#  契约依据：docs/interface.md §0 的 **v1.5 草案**（测量口径档位化，待 A/B 会签）
#            + docs/20（720p60 落地）；拍板记录见 docs/24 §5.1。
#
#  用法（只影响 rgb2gray 的抽取比与默认黄金参考目录，**不设就一切照旧**）：
#      set "VIGILENS_TIER=720p60"     -> 抽取 3/8，灰度 480x270，数据目录 sim/data_motion_720p60
#      set "VIGILENS_TIER=1080p45"    -> 抽取 1/4，灰度 480x270，数据目录 sim/data_motion_1080p45
#      （不设）                        -> 抽取 3/5，灰度 384x288，数据目录 sim/data_motion（既有档，不变）
#
#  为什么两档都要做成 480x270：129600 像素 ≤ 2^17 ⇒ BRAM 台阶不跨，
#  motion_quality 的片内帧缓存仍是 64 个 BRAM18（23%）—— 见 fpga/sim/link_budget.py 实测表。
set tier_num 3
set tier_den 5
if {[info exists ::env(VIGILENS_TIER)] && $::env(VIGILENS_TIER) ne ""} {
    if {$::env(VIGILENS_TIER) eq "720p60"} {
        set tier_num 3 ; set tier_den 8
        set default_data "sim/data_motion_720p60"
    } elseif {$::env(VIGILENS_TIER) eq "1080p45"} {
        set tier_num 1 ; set tier_den 4
        set default_data "sim/data_motion_1080p45"
    } else {
        puts "ERROR: unknown VIGILENS_TIER '$::env(VIGILENS_TIER)'. Known: 720p60 / 1080p45"
        exit 1
    }
    puts "INFO: 档位 VIGILENS_TIER=$::env(VIGILENS_TIER) → 抽取 $tier_num/$tier_den，数据目录 $default_data"
}

set src_file "src/$ip_name.cpp"
set tb_file  "sim/tb_$ip_name.cpp"
if {![file exists $src_file] || ![file exists $tb_file]} {
    puts "ERROR: missing $src_file or $tb_file (run from the fpga/ directory)"
    exit 1
}

puts "INFO: HLS_IP    = $ip_name"
puts "INFO: source    = $src_file"
puts "INFO: testbench = $tb_file"

# Create project / component
# -----------------------------------------------------------------------------
# ⚠️ 组件目录名可用环境变量覆盖（2026-09-30 新增；**缺省行为与以前完全一致**）：
#      set "HLS_COMPONENT=component_roi_statistic_run2"
#      vitis-run --mode hls --tcl run_hls.tcl
#   为什么要有这个后门：`open_component -reset` 需要**先删掉**旧组件目录，
#   而某些环境里旧目录中的 `*.hlsrun_csim_summary` / `hls/.autopilot/db/a.g*`
#   会因 NTFS 拒绝项而删不掉 —— 实测：
#      error deleting "D:/Desktop/AMD/fpga/component_roi_statistic/hls/hls.aps": permission denied
#   于是 csim 连启动都做不到。那是**环境问题**，不该逼人改共享脚本或删仓库文件；
#   指定一个新目录名即可绕开。（`fpga/component_*/` 本就在 .gitignore 里，不入库。）
# -----------------------------------------------------------------------------
set comp_name "component_$ip_name"
if {[info exists ::env(HLS_COMPONENT)] && $::env(HLS_COMPONENT) ne ""} {
    set comp_name $::env(HLS_COMPONENT)
    puts "INFO: HLS_COMPONENT override -> $comp_name"
}
open_component -reset $comp_name -flow_target vivado

if {$fir_60} {
    add_files $src_file -cflags "-DFIR_FS_60HZ"
} elseif {$ip_name eq "rgb2gray"} {
    # 档位化的抽取比（缺省 3/5 = 既有档，与以前完全一致）。
    # ⚠️ **同一组宏必须同时给测试台**：测试台的 Layer 1 断言与朴素参考都按抽取比推导，
    #    只给 src 不给 tb 会让两边口径不一致（实测过：tb 按 3/5 断言、IP 按 3/8 输出，
    #    csim 报 "read while empty" 并以退出码 3 失败）。
    set decim_flags "-DRGB2GRAY_DECIM_NUM=$tier_num -DRGB2GRAY_DECIM_DEN=$tier_den"
    add_files $src_file -cflags $decim_flags
    add_files -tb $tb_file -cflags $decim_flags
} else {
    add_files $src_file
}
add_files -tb $tb_file
set_top $ip_name

# Solution: device + clock（docs/interface.md 第 0 节冻结：PYNQ-Z2 / 100 MHz）
set_part  {xc7z020clg400-1}
create_clock -period 10

# ---- 定位黄金参考数据目录 ---------------------------------------------------
# 测试台通过 argv[1] 收到该目录；找不到时测试台会硬失败（防止"假通过"）。
set golden_files [list golden_roi.csv golden_motion.csv golden_fir.csv \
                       golden_mipi.csv golden_scale.csv]

proc has_golden {dir files} {
    foreach f $files {
        if {[file exists [file join $dir $f]]} { return 1 }
    }
    return 0
}

set data_dir ""
set cand_list [list]

# ① 显式指定（ROI_DATA_DIR）—— ⚠️ **指定了就必须可用**（2026-10-01 修）：
#    原实现把它当"候选之一"：目录里没有黄金参考就**静默跳到下一个候选**，
#    后果是"你以为在测 480x270 档，其实跑的是 384x288 档"，而日志只有一行
#    `INFO: data dir = <另一个目录>` —— 正是本项目最忌讳的静默错
#    （2026-10-01 实测踩到：-D 给了 720p60 的数据目录，却跑成了 640x480 档，
#     并因此把一次 cosim 的结论张冠李戴）。故改为**硬失败**。
if {[info exists ::env(ROI_DATA_DIR)] && $::env(ROI_DATA_DIR) ne ""} {
    set forced [file normalize $::env(ROI_DATA_DIR)]
    if {![has_golden $forced $golden_files]} {
        puts "ERROR: ROI_DATA_DIR is set but unusable: '$::env(ROI_DATA_DIR)'"
        puts "       expected one of: $golden_files"
        puts "       Refusing to silently fall back to the default data dir --"
        puts "       that would test a DIFFERENT measurement tier than requested."
        exit 1
    }
    puts "INFO: ROI_DATA_DIR -> $forced  (explicit, verified)"
    set data_dir $forced
}

# ② 否则按"默认档位目录"找（可用 VIGILENS_TIER 改档位，见上文）。
if {$data_dir eq ""} {
    catch {
        lappend cand_list [file normalize "[file dirname [file normalize [info script]]]/$default_data"]
    }
    lappend cand_list [file normalize "[pwd]/$default_data"]
    lappend cand_list [file normalize "[pwd]/fpga/$default_data"]

    foreach c $cand_list {
        if {[has_golden $c $golden_files]} {
            set data_dir $c
            break
        }
    }
    if {$data_dir eq ""} {
        puts "WARNING: golden reference not found. Candidates tried:"
        foreach c $cand_list { puts "         $c" }
        puts "WARNING: run the matching gen_*.py first."
        set data_dir [lindex $cand_list 0]
    } else {
        puts "INFO: data dir  = $data_dir  (default for tier '$default_data')"
    }
}

# =============================================================================
#  hls_exec: 1 = 仅 C 综合; 2 = 综合 + RTL 协同仿真(cosim); 3 = 再加导出
# -----------------------------------------------------------------------------
#  csynth 的验收只需 1。
#  ⚠️ csim **不建模 hls::stream 的 FIFO 深度**，流深度不足导致的死锁只有 cosim 才暴露。
#     M3 上板前必须至少跑一次 hls_exec = 2（用**小尺寸向量**，见 fpga/README.md「跑 cosim」）。
#
#  可用环境变量覆盖，免去改文件：
#     set "HLS_EXEC=2"
# =============================================================================
set hls_exec 1
if {[info exists ::env(HLS_EXEC)] && $::env(HLS_EXEC) ne ""} {
    set hls_exec $::env(HLS_EXEC)
}
puts "INFO: hls_exec  = $hls_exec  (1=csim+csynth, 2=+cosim, 3=+export)"

# C 仿真（两层验证：内嵌边界用例 + 跨语言黄金参考比对）
# 若怀疑测试台没重新编译，改成:  csim_design -clean -argv "$data_dir"
csim_design -argv "$data_dir"

if {$hls_exec == 1} {
    csynth_design
} elseif {$hls_exec == 2} {
    csynth_design
    # -rtl verilog: 明确用 Verilog RTL；-tool auto 会去找 Vivado 的 xsim
    # ⚠️ -argv 必须也给，否则 cosim 里的测试台拿不到数据目录，
    #    Layer 2（跨语言黄金参考）会被静默跳过，只剩 Layer 1 的玩具用例 ——
    #    那就等于"用 6 个小用例冒充 RTL 正确性"，是不可接受的弱验证。
    cosim_design -rtl verilog -tool auto -argv "$data_dir"
} elseif {$hls_exec == 3} {
    csynth_design
    cosim_design -rtl verilog -tool auto -argv "$data_dir"
    export_design
} else {
    csynth_design
}

exit
