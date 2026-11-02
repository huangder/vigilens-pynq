#!/bin/bash
# Repack a local rootfs in a temporary Linux directory. No device/loop access.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
work=$(realpath "${1:?prepared work directory required}")
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
images=${SOURCE_IMAGES:-$repo/.tmp/net_loop}
test -f "$work/boot.env"
test -f "$work/boot/fat/BOOT.BIN"
stage=$(mktemp -d -t vigilens-cold-rootfs.XXXXXXXX)
trap 'rm -rf "$stage"' EXIT
(cd "$stage" && gzip -dc "$images/rootfs.cpio.gz" | cpio -idm --quiet)
# Old S99vigi consumed camera frames as a bring-up self-report. Only remove
# this file inside the freshly extracted private stage, never the source image.
rm -f "$stage/etc/init.d/S99vigi"
mkdir -p "$stage/etc/vigilens" "$stage/usr/sbin" "$stage/mnt/vigilens-boot"
cp "$work/boot.env" "$stage/etc/vigilens/boot.env"
cp "$repo/board/cold_boot/vigilens-service" "$stage/usr/sbin/vigilens-service"
cp "$repo/board/cold_boot/S99vigilens" "$stage/etc/init.d/S99vigilens"
cp "$repo/board/w7_buildroot/init/S98usb_host" "$stage/etc/init.d/S98usb_host"
chmod 755 "$stage/usr/sbin/vigilens-service" "$stage/etc/init.d/S99vigilens" "$stage/etc/init.d/S98usb_host"
sh -n "$stage/usr/sbin/vigilens-service" "$stage/etc/init.d/S99vigilens"
(cd "$stage" && find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --quiet | gzip -n -9) > "$work/rootfs.cpio.gz"
"$br/output/host/bin/mkimage" -A arm -O linux -T ramdisk -C gzip -n VigiLens-SD-autonomy \
    -d "$work/rootfs.cpio.gz" "$work/boot/fat/uInitrd"
mkdir -p "$work/dts"
printf '#include "system-top.dts"\n#include "%s/board/cold_boot/system.dtsi"\n' "$repo" > "$work/dts/entry.dts"
gcc -E -nostdinc -undef -x assembler-with-cpp -I "$repo/board/w7_buildroot/dt" \
    -I "$repo/board/w7_buildroot" "$work/dts/entry.dts" -o "$work/dts/expanded.dts"
"$br/output/host/bin/dtc" -I dts -O dtb -o "$work/boot/fat/system.dtb" "$work/dts/expanded.dts" 2> "$work/dts/warnings.log"
cp "$repo/board/cold_boot/boot.cmd" "$work/boot/fat/boot.cmd"
"$br/output/host/bin/mkimage" -A arm -O linux -T script -C none -n VigiLens-SD-autonomy \
    -d "$work/boot/fat/boot.cmd" "$work/boot/fat/boot.scr"
python3 "$repo/board/cold_boot/verify_linux.py" "$work" "$br"
python3 "$repo/board/cold_boot/verify_fit.py" "$work" "$br"
echo 'COLD_BOOT_LINUX_PREPARED; physical SD power-on remains unverified'
