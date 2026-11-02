#!/usr/bin/env bash
# make_sd.sh —— 把 W7 Buildroot 产物写进 SD 卡（【未上板验证】，在 Linux 主机上跑）
#
# 用法：
#   sudo ./make_sd.sh /dev/sdX boot.bin zImage system.dtb rootfs.ext4
#
# 设计原则：**先把要写的目标设备完整打印出来，再要求输入 YES**。
# 写错盘是不可逆事故，宁可多一道确认。
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

[ $# -eq 5 ] || die "用法: $0 /dev/sdX boot.bin zImage system.dtb rootfs.ext4"
DEV="$1"; BOOT_BIN="$2"; ZIMAGE="$3"; DTB="$4"; ROOTFS="$5"

for f in "$BOOT_BIN" "$ZIMAGE" "$DTB" "$ROOTFS"; do
    [ -f "$f" ] || die "找不到 $f"
done
[ -b "$DEV" ] || die "$DEV 不是块设备"

# 防止把 rootfs 写到"正在当根用"的那块盘上
ROOTDEV=$(findmnt -n -o SOURCE / | sed 's/[0-9]*$//')
if [ "$(readlink -f "$DEV")" = "$(readlink -f "$ROOTDEV")" ]; then
    die "$DEV 看起来是系统盘（/ 在上面），拒绝执行"
fi

echo "=============================================="
echo " 即将整盘擦除并重建分区："
echo "   目标设备 : $DEV"
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT "$DEV" || true
echo "   写入文件 : $BOOT_BIN / $ZIMAGE / $DTB / $ROOTFS"
echo "=============================================="
read -r -p "确认无误请输入 YES： " ans
[ "$ans" = "YES" ] || die "已取消"

# 0) 卸掉该盘所有已挂载分区
for p in "${DEV}"*; do
    [ -b "$p" ] && sudo umount "$p" 2>/dev/null || true
done

# 1) MBR: p1 FAT32 64M（boot）, p2 ext4 512M（rootfs）
sudo sgdisk -o "$DEV"
sudo sgdisk -n1:0:+64M  -t1:0c -c1:boot   "$DEV"
sudo sgdisk -n2:0:+512M -t2:83 -c2:rootfs "$DEV"
sudo partprobe "$DEV"
sleep 1

P1="${DEV}1"; P2="${DEV}2"
# SD 读卡器通常是 /dev/sdb（分区 /dev/sdb1），但 eMMC/SD 直连会是 /dev/mmcblk0（分区 /dev/mmcblk0p1）、
# NVMe 同理（/dev/nvme0n1p1）。别在"分区节点没出现"上浪费半小时。
case "$DEV" in
    */mmcblk*|*/nvme*|*/loop*) P1="${DEV}p1"; P2="${DEV}p2" ;;
esac
[ -b "$P1" ] && [ -b "$P2" ] || die "分区节点未出现（$P1 / $P2）—— 先 lsblk 确认命名"

# 2) 校验 rootfs 大小不超过 p2（dd 超长会截断 image 或写坏分区表）
ROOTFS_BYTES=$(stat -c %s "$ROOTFS")
P2_BYTES=$(sudo blockdev --getsize64 "$P2")
if [ "$ROOTFS_BYTES" -gt "$P2_BYTES" ]; then
    die "rootfs.ext4（$ROOTFS_BYTES B）大于 p2（$P2_BYTES B）—— 把 sgdisk -n2 的 +512M 调大后重来"
fi

sudo mkfs.vfat -F 32 -n boot "$P1"
sudo mkfs.ext4 -F -L rootfs  "$P2"

# 3) boot 分区
MNT=$(mktemp -d)
sudo mount "$P1" "$MNT"
sudo cp "$BOOT_BIN" "$MNT/boot.bin"
sudo cp "$ZIMAGE"   "$MNT/zImage"
sudo cp "$DTB"      "$MNT/system.dtb"
if command -v mkimage >/dev/null 2>&1; then
    mkimage -A arm -O linux -T script -C none -d "$(dirname "$0")/boot.cmd" "$MNT/boot.scr" >/dev/null
    echo "已生成 boot.scr"
else
    echo "warn: 无 mkimage，只拷 boot.cmd；U-Boot 里手测 'run uenvcmd'"
    sudo cp "$(dirname "$0")/boot.cmd" "$MNT/boot.cmd"
fi
sync
sudo umount "$MNT"

# 4) rootfs：ext4 镜像直接 dd 到 p2（比 tar 解包可靠）
echo "rootfs.ext4 → $P2（dd）"
sudo dd if="$ROOTFS" of="$P2" bs=4M conv=fsync status=progress
sync
rmdir "$MNT" 2>/dev/null || true

echo
echo "完成。板子上：J1 跳 SD，串口 115200 看 U-Boot。"
echo "验收判据：v4l2-ctl --list-devices 见到相机（docs/37 §W7）"
