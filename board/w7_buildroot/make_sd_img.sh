#!/usr/bin/env bash
# make_sd_img.sh -- build a burnable SD image file (no card slot in the machine required).
#
# Why this exists: make_sd.sh writes straight to /dev/sdX, which only works on a host that can
# see the card. On this project's dev machine the card reader is a USB device; WSL2 cannot see it
# without usbipd passthrough, and Windows cannot create an ext4 partition. So we assemble the same
# layout as a plain .img and let the user burn it with Rufus (DD mode) / balenaEtcher / Win32DiskImager.
#
# Layout (matches make_sd.sh):
#   p1  FAT32  64 MiB   BOOT.BIN, zImage, system.dtb, boot.scr
#   p2  ext4   rest     rootfs.ext4 dd'd in place
#
# Usage (inside WSL, as root):
#   bash make_sd_img.sh                       # uses /mnt/d/w7/images + writes /mnt/d/w7/vigilens_sd.img
#   bash make_sd_img.sh /mnt/d/w7/images /mnt/d/w7/sd.img
#
# NOT verified on hardware yet: it produces a valid image, it does not prove the board boots.
set -eu

SRC="${1:-/mnt/d/w7/images}"
OUT="${2:-/mnt/d/w7/vigilens_sd.img}"
SIZE_MB="${SIZE_MB:-768}"
P1_MB=64

for f in BOOT.BIN zImage system.dtb boot.scr rootfs.ext4; do
    [ -f "$SRC/$f" ] || { echo "missing: $SRC/$f" >&2; exit 1; }
done
[ "$(id -u)" = "0" ] || { echo "run as root (losetup)" >&2; exit 1; }

WORK=$(mktemp -d /tmp/sdimg.XXXXXX)
IMG="$WORK/disk.img"
LOOP=""
cleanup() {
    set +e
    mountpoint -q "$WORK/mnt" && umount "$WORK/mnt"
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

echo "== 1/6 create $SIZE_MB MiB sparse image"
truncate -s "${SIZE_MB}M" "$IMG"

echo "== 2/6 partition: p1 FAT32 ${P1_MB}M (boot), p2 ext4 rest"
# 2048 sectors = 1 MiB alignment; 131072 sectors = 64 MiB
sfdisk "$IMG" >/dev/null <<EOF
label: dos
label-id: 0x56494749
unit: sectors

start=2048, size=$((P1_MB * 2048)), type=c, bootable
start=$((2048 + P1_MB * 2048)), type=83
EOF

echo "== 3/6 attach loop device"
LOOP=$(losetup -f --show -P "$IMG")
udevadm settle 2>/dev/null || sleep 1
ls "${LOOP}p1" "${LOOP}p2" >/dev/null || { echo "loop partitions did not appear" >&2; exit 1; }

echo "== 4/6 mkfs"
mkfs.vfat -F 32 -n boot "${LOOP}p1" >/dev/null
mkfs.ext4 -q -F -L rootfs "${LOOP}p2"

echo "== 5/6 fill p1 (boot) and p2 (rootfs)"
mkdir -p "$WORK/mnt"
mount "${LOOP}p1" "$WORK/mnt"
cp "$SRC/BOOT.BIN" "$SRC/zImage" "$SRC/system.dtb" "$SRC/boot.scr" "$WORK/mnt/"
sync
umount "$WORK/mnt"
dd if="$SRC/rootfs.ext4" of="${LOOP}p2" bs=4M conv=fsync status=none
sync

echo "== 6/6 detach + move"
losetup -d "$LOOP"; LOOP=""
mv "$IMG" "$OUT"
ls -lh "$OUT" | awk '{print "   " $9, $5}'
sha256sum "$OUT" | awk '{print "   sha256:", $1}'
echo "完成。用 Rufus（DD 模式）/ balenaEtcher 把这个 .img 写进 TF 卡。"
