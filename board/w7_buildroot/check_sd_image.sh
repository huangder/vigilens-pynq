#!/usr/bin/env bash
# check_sd_image.sh —— 非破坏性核验 vigilens_sd.img 是否具备冷启动条件。
#
# 用法：bash check_sd_image.sh /mnt/d/w7/vigilens_sd.img
# 需要：losetup / mount / fdtget（device-tree-compiler）
# 只读挂载，不写盘；退出码 0 = 全部检查通过。
set -euo pipefail

IMG=${1:?usage: $0 /path/to/vigilens_sd.img}
[ -f "$IMG" ] || { echo "FAIL: no image $IMG" >&2; exit 1; }
command -v fdtget >/dev/null || { echo "FAIL: fdtget missing" >&2; exit 1; }

LOOP=$(losetup -Pf --show "$IMG")
BOOT=$(mktemp -d)
ROOT=$(mktemp -d)
cleanup() {
    umount "$BOOT" 2>/dev/null || true
    umount "$ROOT" 2>/dev/null || true
    losetup -d "$LOOP" 2>/dev/null || true
    rmdir "$BOOT" "$ROOT" 2>/dev/null || true
}
trap cleanup EXIT

mount -o ro "${LOOP}p1" "$BOOT"
mount -o ro "${LOOP}p2" "$ROOT"

fail=0
need() { [ -e "$1" ] || { echo "FAIL: missing $1"; fail=1; }; }
need "$BOOT/BOOT.BIN"
need "$BOOT/zImage"
need "$BOOT/system.dtb"
need "$BOOT/boot.scr"
need "$ROOT/sbin/init"
need "$ROOT/etc/inittab"

status=$(fdtget "$BOOT/system.dtb" /axi/mmc@e0100000 status 2>/dev/null || echo missing)
[ "$status" = "okay" ] || { echo "FAIL: sdhci0 status=$status"; fail=1; }
bootargs=$(fdtget "$BOOT/system.dtb" /chosen bootargs 2>/dev/null || echo missing)
case "$bootargs" in
    *root=/dev/mmcblk0p2*) ;;
    *) echo "FAIL: bootargs=$bootargs"; fail=1 ;;
esac
grep -q "ttyPS0::respawn" "$ROOT/etc/inittab" || { echo "FAIL: no getty on ttyPS0"; fail=1; }
grep -q "VigiLens boot" <(strings "$BOOT/boot.scr") || { echo "FAIL: boot.scr not a VigiLens script"; fail=1; }
# 必须是 Xilinx FSBL（能加载 U-Boot 分区），不是不搬 U-Boot 的 mini_fsbl。
grep -aq "mini_fsbl" "$BOOT/BOOT.BIN" && { echo "FAIL: BOOT.BIN still uses mini_fsbl (cannot load U-Boot)"; fail=1; }
grep -aq "U-Boot 2024" "$BOOT/BOOT.BIN" || { echo "FAIL: BOOT.BIN does not contain U-Boot"; fail=1; }

[ "$fail" -eq 0 ] && {
    echo "PASS: boot files, dtb(sdhci0=okay), bootargs, rootfs init/getty, boot.scr"
    sha256sum "$IMG"
}
exit "$fail"
