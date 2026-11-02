#!/usr/bin/env bash
# build_sd_dtb.sh —— 生成「SD 卡启动」用的设备树，不改 JTAG 默认树。
#
# 背景（docs/61）：
#   * JTAG + RAM rootfs 的默认树故意把 sdhci0 关掉：卡槽空时控制器会刷中断风暴，
#     串口被寄存器 dump 淹没（2026-10-26 实测）。
#   * SD 启动时必须把 sdhci0 打开，并用 broken-cd 让内核轮询（板子没接 CD/WP）。
#   * 因此 SD 镜像里的 system.dtb 必须由本脚本单独生成，而不是复用 JTAG 那份。
#
# 用法：
#   bash build_sd_dtb.sh /root/vigilens/dts ./system-sd.dtb
# 依赖：cpp、dtc、fdtget（WSL/Ubuntu 自带 dtc 包）
set -euo pipefail

DTS_DIR=${1:-/root/vigilens/dts}
OUT=${2:-./system-sd.dtb}
[ -d "$DTS_DIR" ] || { echo "ERROR: no dts dir $DTS_DIR" >&2; exit 1; }
command -v dtc >/dev/null || { echo "ERROR: dtc missing (apt install device-tree-compiler)" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp "$DTS_DIR"/*.dts "$DTS_DIR"/*.dtsi "$WORK"/

# 只在 sdhci0 节点内把 disabled 改成 okay，并补 broken-cd；其它节点不动。
sed -i '/&sdhci0 {/,/};/ s/status = "disabled";/status = "okay";\n\tbroken-cd;/' "$WORK/system-user.dtsi"

cpp -nostdinc -I"$WORK" -undef -x assembler-with-cpp "$WORK/mizar-z7.dts" -o "$WORK/expanded.dts"
dtc -I dts -O dtb -o "$OUT" "$WORK/expanded.dts" 2>"$WORK/dtc.log" || {
    cat "$WORK/dtc.log" >&2; exit 1; }

status=$(fdtget "$OUT" /axi/mmc@e0100000 status 2>/dev/null || true)
[ "$status" = "okay" ] || { echo "ERROR: sdhci0 status=$status (expected okay)" >&2; exit 1; }
echo "OK: $OUT sdhci0=okay size=$(stat -c %s "$OUT")"
