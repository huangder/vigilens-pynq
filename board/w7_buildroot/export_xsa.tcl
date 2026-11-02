# export_xsa.tcl —— 从现有 Vivado 工程导出 XSA
#
# 为什么需要它：PetaLinux 2026.1 的 `--get-hw-description` 只吃 `.xsa`（`.hdf` 是旧格式），
#               Vitis 的 device-tree-generator（生成 pcw.dtsi）同样吃 `.xsa`。
#
# 用法（Windows，Vivado 2026.1）：
#   cd /d D:\Desktop\AMD\board
#   vivado -mode batch -source w7_buildroot\export_xsa.tcl -nojournal -nolog
# 可选环境变量：
#   set VIGILENS_XSA_OUT=D:\Desktop\AMD\board\bitstream\system_720p60.xsa
#
# 2026-10-25 实测记录（重要，别再踩）：
#   只调 write_hw_platform（不 open_run）会失败：
#     ERROR: [Hsi 55-2023] No IPI block design hardware handoff files present in the hardware definition file .../temp.hdf
#     ERROR: [Common 17-99] Failed to open zip archive ...xsa
#   ⇒ 必须先 `open_run impl_1` 再导出（本脚本已内置）。

set SCRIPT_DIR [file normalize [file dirname [info script]]]
set TOP_DIR    [file normalize [file join $SCRIPT_DIR ..]]
set PROJ       [file join $TOP_DIR vivado_project vigilens_bd.xpr]
set OUT        [file join $TOP_DIR bitstream system.xsa]
if {[info exists ::env(VIGILENS_XSA_OUT)] && $::env(VIGILENS_XSA_OUT) ne ""} {
    set OUT [file normalize $::env(VIGILENS_XSA_OUT)]
}

if {![file exists $PROJ]} {
    puts "ERROR: 找不到工程 $PROJ"
    exit 1
}

open_project $PROJ
puts "== project: $PROJ"
puts "== part   : [get_property PART [current_project]]"

# ── 关键：把 XSA 标记成「嵌入式平台」 ─────────────────────────────────────────────
# 没有这一步，导出的 XSA 在 xsa.json 里是 "embedded": false / "dataCenter": false，
# 于是 Vitis 建 platform 时报：
#   Cannot create platform, status = StatusCode.INTERNAL, details = 'Error in generating Processor List.'
#   （`client.get_processor_os_list(xsa=...)` 同错 ⇒ 是 XSA 的元数据，不是调用姿势）
# 这三条属性等价于 Vivado GUI「Export Hardware」自动写入的设计意图。
# 用 catch 包住：老版本 Vivado 没有这些属性名，不该因此中断导出。
foreach {prop val} {
    platform.design_intent.embedded        true
    platform.design_intent.datacenter      false
    platform.design_intent.external_host   false
    platform.vendor                        xilinx.com
    platform.board_id                      mizar_z7
    platform.default_output_type           hw_export
} {
    if {[catch {set_property $prop $val [current_project]} m]} {
        puts "WARN: set_property $prop $val 失败（可忽略）：$m"
    } else {
        puts "== intent: $prop = $val"
    }
}

# 2026-10-25 追加实测（同一台机器，两条容易误判的现象）：
#   ① 在受限沙箱里跑 vivado.bat 会先报 `ERROR: [Common 17-354] Could not open 'C' for writing`，
#      严重时 open_project/create_project 直接失败并抛 `tclapp::load_apps`。
#      原因是 %APPDATA%/%LOCALAPPDATA%/%HOME% 不可写；把这三者指到一个可写目录后现象消失
#      （实测：`$env:APPDATA=$env:TEMP\appdata_roam` 之后 create_project/open_project 全部正常）。
#      ⇒ **普通终端里不会有这个问题**，不要据此判断脚本坏了。
#   ② 加了上面这些 intent 之后，导出的 XSA 里 `xsa.json` 确实是 `"embedded": {"value": "true"}`
#      （实测 2026-10-25，800824 B），`ERROR: [Hsi 55-2023]` 也消失了。**但 Vitis 仍可能报**
#      `Error in generating Processor List.` —— 那时的病根在 `system.hwh`：
#      PS7 那个 MODULE 被写成 `IPTYPE="PERIPHERAL"` 而不是 `IPTYPE="PROCESSOR"`，
#      Vitis 因此扫不到 `ps7_cortexa9_0`（细节见 docs/41 §18.5）。

# 实现是否完成：决定能不能 -include_bit
set has_bit 0
if {[llength [get_runs impl_1]] > 0} {
    set st [get_property STATUS [get_runs impl_1]]
    puts "== impl_1 : $st"
    if {[string match "*Complete*" $st]} { set has_bit 1 }
}

if {$has_bit} {
    # 关键：不 open_run 时 write_hw_platform 拿不到 IPI handoff（见文件头实测记录）
    if {[catch {open_run impl_1} msg]} {
        puts "WARN: open_run impl_1 失败：$msg（继续尝试导出）"
    }
}

set ok 1
if {$has_bit} {
    set ok [catch {write_hw_platform -fixed -include_bit -force -file $OUT} msg]
} else {
    puts "WARN: impl_1 未完成 ⇒ 只导**不含 bitstream**的 XSA（PetaLinux 仍可用）"
    set ok [catch {write_hw_platform -fixed -force -file $OUT} msg]
}
if {$ok} {
    puts "ERROR: write_hw_platform 失败：$msg"
    puts "       兜底：先 `launch_runs impl_1 -to_step write_bitstream -jobs 2`，"
    puts "       或沿用旧的 HDF 路线（board/build_bd.tcl:435 的 write_hwdef）"
    close_project
    exit 1
}

puts "== XSA: $OUT"
if {[file exists $OUT]} {
    puts "== size: [expr {[file size $OUT] / 1024}] KB"
    if {[file size $OUT] < 1024} {
        puts "ERROR: XSA 太小（可疑），检查上一条 ERROR"
        close_project
        exit 1
    }
} else {
    puts "ERROR: 没有生成 XSA"
    close_project
    exit 1
}
close_project
