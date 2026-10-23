# =============================================================================
#  export_ip.tcl —— C 线 IP 的「只导出」脚本（不重跑 csim/csynth/cosim）
# -----------------------------------------------------------------------------
#  项目：知倦 / VigiLens（AMD 3.3 自主选题初级组）—— C 线（FPGA/HLS）
#
#  用途：M3 上板（build_bd.tcl）需要 4 个 HLS IP 的 Vivado 打包产物
#        component_<ip>/hls/impl/ip/component.xml（+ .zip）。
#        这 4 个 IP 已各自通过 csim+csynth+cosim（csynth 产物在 hls/syn/ 已就位），
#        **唯一缺的就是 export_design 这一步**，所以本脚本只做导出、不重跑仿真。
#
#  执行（在 fpga/ 目录下，且已 call settings64.bat）：
#      set "HLS_IP=roi_statistic" && vitis-run --mode hls --tcl export_ip.tcl
#      （rgb2gray / motion_quality / fir_filter 同理）
#
#  与 run_hls.tcl 的差别（为什么要单独一个脚本）：
#      1) run_hls.tcl 第 116 行 `open_component -reset` 会**先删掉**旧组件目录再重建，
#         在 NTFS 上可能因 hls.aps 被锁而 `permission denied`（run_hls.tcl:101-109 已记录）。
#         本脚本 `open_component`（无 -reset）直接复用现有组件，不删任何东西。
#      2) run_hls.tcl 第 214 行 `csim_design` 无条件先跑，而 csim 需要命名管道，
#         在受限沙箱里会 `Win32 error 5`（AGENTS.md §6.3）。本脚本完全跳过 csim。
#      3) 导出产物落在 component_<ip>/hls/impl/ip/，与 build_bd.tcl 的 ip_repo 路径一致。
#
#  ⚠️【未验证】本脚本是给"csynth 已就位"场景的快捷路径；Vitis HLS 2026.1 若在
#     open_component 后拒绝 export_design（例如坚持要求本会话重跑 csynth），
#     退回官方路径：`set "HLS_EXEC=3" && set "HLS_IP=<ip>" && vitis-run --mode hls --tcl run_hls.tcl`
#     （若撞 NTFS 删目录问题，再加 HLS_COMPONENT 换新目录，并同步改 build_bd.tcl 的 ip_repo）。
# =============================================================================

set ip_name "roi_statistic"
if {[info exists ::env(HLS_IP)] && $::env(HLS_IP) ne ""} {
    set ip_name $::env(HLS_IP)
}

set known_ips [list roi_statistic rgb2gray motion_quality fir_filter]
if {[lsearch -exact $known_ips $ip_name] < 0} {
    puts "ERROR: export_ip.tcl only handles the 4 core IPs ($known_ips)."
    puts "       For raw10_unpack/bayer_demosaic/frame_scale use run_hls.tcl."
    exit 1
}

set comp_name "component_$ip_name"
if {[info exists ::env(HLS_COMPONENT)] && $::env(HLS_COMPONENT) ne ""} {
    set comp_name $::env(HLS_COMPONENT)
}

if {![file isdirectory $comp_name]} {
    puts "ERROR: component dir '$comp_name' not found under [pwd]."
    puts "       Run `set \"HLS_EXEC=1\" && set \"HLS_IP=$ip_name\" && vitis-run --mode hls --tcl run_hls.tcl` first."
    exit 1
}

puts "INFO: open_component $comp_name (no -reset; reuse existing csynth)"
open_component $comp_name -flow_target vivado

puts "INFO: export_design -format ip_catalog -> $comp_name/hls/impl/ip/"
export_design -format ip_catalog

set xml [file join $comp_name "hls" "impl" "ip" "component.xml"]
if {[file exists $xml]} {
    puts "PASS: exported $xml"
} else {
    puts "ERROR: export did not produce $xml"
    exit 1
}

exit
