#!/bin/bash
# Repack the existing rootfs; do not rebuild or replace the kernel/toolchain.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
images=${SOURCE_IMAGES:-/mnt/d/w7/images}
out=${1:?usage: repack_usb_initrd.sh output-directory}
mkdir -p "$out"
out=$(cd "$out" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# Work on an independent rootfs, preserving the original Buildroot target.
(cd "$stage" && gzip -dc "$images/rootfs.cpio.gz" | cpio -idm --quiet)
install -m 755 "$repo/board/w7_buildroot/init/S99vigi" "$stage/etc/init.d/S99vigi"
install -m 755 "$repo/board/w7_buildroot/init/S98usb_host" "$stage/etc/init.d/S98usb_host"
"$br/output/host/bin/arm-linux-gcc" -std=c11 -D_DEFAULT_SOURCE -Os -Wall -Wextra -Werror \
    "$repo/board/w7_buildroot/uvc_probe.c" -o "$stage/usr/bin/uvc_probe"
(cd "$stage" && find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --quiet | gzip -n -9) > "$out/rootfs.cpio.gz"
"$br/output/host/bin/mkimage" -A arm -O linux -T ramdisk -C gzip \
    -n 'VigiLens USB reset fix' -d "$out/rootfs.cpio.gz" "$out/uInitrd"
sha256sum "$out/uInitrd"
for image in zImage u-boot-dtb.bin boot_jtag.scr; do
    cp "$images/$image" "$out/$image"
done
