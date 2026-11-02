#!/usr/bin/env bash
# wsl_build.sh —— 在 WSL2 / Ubuntu 22.04 里把 W7 的 rootfs + 内核 + U-Boot 一次编出来
#
# ⚠️ 【本脚本未在真机跑过】（写它时 WSL 处于拒访状态，见 docs/41 §16.1）。
#    第一次跑请加 `bash -x` 或分段执行，别在没看输出的情况下等一小时。
#
# 目录约定（在 WSL 里）：
#   ~/vigilens/
#     ├── wsl_build.sh              # 本脚本
#     ├── buildroot_w7.config       # 增量配置
#     ├── kernel_uvc.fragment       # 内核片段
#     └── dts/
#         ├── system-top.dts        # ← sdtgen 生成（docs/41 §15.2）
#         ├── pcw.dtsi  pl.dtsi  zynq-7000.dtsi
#         └── system-user.dtsi      # ← 仓库里的板级覆盖
#
# 准备命令（Windows 侧 sdtgen 跑完后再进 WSL）：
#   mkdir -p ~/vigilens/dts && cd ~/vigilens
#   cp /mnt/d/Desktop/AMD/board/w7_buildroot/{wsl_build.sh,buildroot_w7.config,kernel_uvc.fragment,system-user.dtsi} .
#   cp /mnt/d/Desktop/AMD/board/w7_buildroot/dt/*.dts* dts/ 2>/dev/null || true
#   mv dts/system-user.dtsi dts/ 2>/dev/null || cp system-user.dtsi dts/
#   bash wsl_build.sh
#
# 环境变量：WORK（默认 ~/vigilens）、BRANCH（默认 2025.02.x）、JOBS（默认 nproc）

set -euo pipefail

# WSL 会把 Windows 的 PATH 追加进 Linux 的 PATH（条目里带空格，如
# "/mnt/c/Program Files/..."），Buildroot 的 dependencies 检查直接报
#   Your PATH contains spaces, TABs, and/or newline (\n) characters.
# 这里把"含空格/TAB"的条目整个丢掉（/usr/local/bin:/usr/bin 这些留着）。
_clean_path=""
IFS=':' read -r -a _path_parts <<< "${PATH:-}"
for _p in "${_path_parts[@]}"; do
    case "$_p" in
        *[[:space:]]*) continue ;;
        "") continue ;;
    esac
    _clean_path="${_clean_path:+$_clean_path:}$_p"
done
export PATH="$_clean_path"

# Ubuntu 26.04 默认把 coreutils 换成了 Rust 版 uutils，`install` 的行为差异会让
# Buildroot 直接拒跑：
#   You have an uutils 'install' version installed which is affected by ...
# GNU 版在这台机器上是 /usr/bin/gnuinstall，改成指向它即可（幂等，可反复跑）。
if [ -x /usr/bin/gnuinstall ]; then
    if ! /usr/bin/install --version 2>/dev/null | head -1 | grep -q "GNU coreutils"; then
        update-alternatives --install /usr/bin/install install /usr/bin/gnuinstall 100 >/dev/null 2>&1 || true
        update-alternatives --set install /usr/bin/gnuinstall >/dev/null 2>&1 || true
        echo "== [0/6] install -> $(/usr/bin/install --version | head -1)"
    fi
fi

# 同一个坑还会以 **dd** 的形式出现（U-Boot 的 SPL 对齐步用它）：
#   dd: conv=block or conv=unblock specified without cbs=N   ⇒ spl/u-boot-spl-align.bin Error 1
# 这是 uutils dd 与 GNU dd 的语义差异，不是我们配置错。GNU 版是 /usr/bin/gnudd。
for _t in dd; do
    if [ -x "/usr/bin/gnu$_t" ] && ! "/usr/bin/$_t" --version 2>/dev/null | head -1 | grep -q "GNU coreutils"; then
        update-alternatives --install "/usr/bin/$_t" "$_t" "/usr/bin/gnu$_t" 100 >/dev/null 2>&1 || true
        update-alternatives --set "$_t" "/usr/bin/gnu$_t" >/dev/null 2>&1 || true
        echo "== [0/6] $_t -> $("/usr/bin/$_t" --version | head -1)"
    fi
done

WORK="${WORK:-$HOME/vigilens}"
BRANCH="${BRANCH:-2025.02.x}"
JOBS="${JOBS:-$(nproc)}"

die() { echo "ERROR: $*" >&2; exit 1; }

echo "== work=$WORK branch=$BRANCH jobs=$JOBS"

# ---------- 0. 前置体检（缺东西就别等一小时才发现）----------
echo "== [0/6] 前置检查"
[ -f "$WORK/buildroot_w7.config" ]    || die "缺 $WORK/buildroot_w7.config"
[ -f "$WORK/kernel_uvc.fragment" ]    || die "缺 $WORK/kernel_uvc.fragment（没拷进来的话 FRAG 会指向空气）"
for f in system-top.dts pcw.dtsi pl.dtsi zynq-7000.dtsi system-user.dtsi; do
    [ -f "$WORK/dts/$f" ] || die "缺 dts/$f —— 前四个由 sdtgen 生成（docs/41 §15.2），第五个在仓库 board/w7_buildroot/"
done
avail_gb=$(df -Pk "$HOME" | awk 'NR==2{printf "%d", $4/1024/1024}')
[ "$avail_gb" -ge 25 ] || die "$HOME 只剩 ${avail_gb} GB，Buildroot 首次编译要 8~15 GB（留 25 GB 保险）"
echo "   磁盘余量 ${avail_gb} GB，dts/ 五件套齐全"

# ---------- 1. 依赖 ----------
echo "== [1/6] apt 依赖"
sudo apt-get update
# 后三个是 make_sd.sh 的依赖：sgdisk(gdisk)、mkfs.vfat(dosfstools)、mkimage(u-boot-tools)。
# 少了它们，编完到"做卡"那一步才报 command not found。
sudo apt-get install -y --no-install-recommends \
    build-essential git bison flex libssl-dev libncurses-dev \
    bc rsync cpio unzip file wget ca-certificates \
    python3 device-tree-compiler \
    gdisk dosfstools u-boot-tools

for t in gcc make bison flex python3 dtc sgdisk mkfs.vfat mkimage; do
    command -v "$t" >/dev/null || die "装完仍找不到 $t"
done

# ---------- 2. 源码 ----------
cd "$WORK"
if [ ! -d buildroot/.git ]; then
    echo "== [2/6] clone buildroot $BRANCH"
    # 官方源优先（国内多数网络能连 git.buildroot.net，但连不上 github.com:443 —— 2026-10-25 实测）
    if ! git clone --depth 1 -b "$BRANCH" https://git.buildroot.net/buildroot; then
        echo "   git.buildroot.net 失败，改试 github 镜像"
        rm -rf buildroot
        git clone --depth 1 -b "$BRANCH" https://github.com/buildroot/buildroot.git
    fi
else
    echo "== [2/6] 复用已有 buildroot"
    # 版本不一致会得到"配置项名字对不上"的怪现象，提醒一句
    ( cd buildroot && git describe --tags 2>/dev/null || true )
fi

# ---------- 3. 基础 defconfig ----------
cd "$WORK/buildroot"
echo "== [3/6] zynq_zed_defconfig"
make zynq_zed_defconfig

# ---------- 4. 顶层 DTS ----------
# 关键：**必须把 system-user.dtsi 也 include 进来**。
# system-top.dts 只 include zynq-7000.dtsi / pl.dtsi / pcw.dtsi ——
# 少了这一行，usb0 永远是 disabled，板上就是"没有 /dev/video0"，而编译一切正常。
echo "== [4/6] 生成 dts/mizar-z7.dts"
{
    echo '/* 由 wsl_build.sh 生成，别手改 */'
    echo '#include "system-top.dts"'
    echo '#include "system-user.dtsi"'
} > "$WORK/dts/mizar-z7.dts"

DTS_PATH="$WORK/dts/mizar-z7.dts $WORK/dts/system-top.dts $WORK/dts/pcw.dtsi $WORK/dts/pl.dtsi $WORK/dts/zynq-7000.dtsi $WORK/dts/system-user.dtsi"
FRAG="$WORK/kernel_uvc.fragment"

# ---------- 5. 打增量配置 ----------
echo "== [5/6] merge w7 配置"
support/kconfig/merge_config.sh -m .config "$WORK/buildroot_w7.config"

# DTS/fragment 是绝对路径（每台机器不同），merge_config 里写死不好用，这里直接改 .config
# ⚠️ 这两条在 2025.02 的 .config 里**已经存在但为空**（`BR2_..._CUSTOM_DTS_PATH=""`），
#    所以不能只处理 `# ... is not set` 那种写法（第一版就是这样，sed 没匹配上、grep 又认为已存在，
#    结果值永远是空 —— 回读直接报 FAIL，见 docs/41 §22.2）。
sed -i "s|^BR2_LINUX_KERNEL_CUSTOM_DTS_PATH=.*|BR2_LINUX_KERNEL_CUSTOM_DTS_PATH=\"$DTS_PATH\"|" .config
sed -i "s|^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=.*|BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$FRAG\"|" .config
grep -q '^BR2_LINUX_KERNEL_CUSTOM_DTS_PATH="' .config || \
    echo "BR2_LINUX_KERNEL_CUSTOM_DTS_PATH=\"$DTS_PATH\"" >> .config
grep -q '^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="' .config || \
    echo "BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES=\"$FRAG\"" >> .config

make olddefconfig

# ---------- 6. 回读校验（不看回读就等于没配）----------
echo "== [6/6] 回读关键项"
fail=0
check() {
    local k="$1" want="$2" got
    got=$(grep -m1 "^$k=" .config | cut -d= -f2- || true)
    if [ "$got" = "$want" ]; then
        printf '   PASS  %-42s = %s\n' "$k" "$got"
    else
        printf '   FAIL  %-42s = %s (want %s)\n' "$k" "${got:-<缺失>}" "$want"
        fail=1
    fi
}
check BR2_LINUX_KERNEL                        y
check BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE   '"6.6.158"'
check BR2_TARGET_UBOOT                        y
check BR2_TARGET_UBOOT_FORMAT_ELF             y
check BR2_TARGET_UBOOT_CUSTOM_VERSION_VALUE   '"2024.10"'
check BR2_TARGET_UBOOT_BOARD_DEFCONFIG        '"xilinx_zynq_virt"'
check BR2_TARGET_ROOTFS_EXT2_4                y
check BR2_TARGET_ROOTFS_EXT2_SIZE             '"512M"'
check BR2_PACKAGE_LIBUSB                      y
check BR2_TARGET_GENERIC_GETTY_PORT           '"ttyPS0"'
# 路径型：只断言非空
if grep -q '^BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES="[^"]' .config; then
    printf '   PASS  %-42s = 已填\n' BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES
else
    printf '   FAIL  %-42s = 空\n' BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES; fail=1
fi
[ "$fail" -eq 0 ] || die "回读有 FAIL —— 别继续编译，先看上面哪条不对"

echo "== 开始编译（首次约 40~90 min；日志写到 $WORK/build.log）"
make -j"$JOBS" 2>&1 | tee "$WORK/build.log" | tail -n 30
echo "== 产物："
ls -lh output/images/

cat <<EOF

下一步（回 Windows 侧）：
 1) 需要的三样（路径按实际）：
      output/images/zImage                        -> kImage
      output/images/system.dtb                    -> system.dtb（名字对不上就找 output/build/linux-*/arch/arm/boot/dts/mizar-z7.dtb）
      output/build/uboot-*/u-boot                 -> 改名 u-boot.elf（bootgen 要 ELF，不是 u-boot.img）
 2) FSBL：Vitis 2026.1 里用同一份 XSA 新建 Application Project -> Zynq FSBL -> 编出 fsbl.elf
 3) 打包（在 board\\\\w7_buildroot 目录里跑，bif 用的是相对路径）：
      call D:\\Xilinx\\2026.1\\Vitis\\settings64.bat
      bootgen -image boot.bif -arch zynq -o boot.bin -w on
 4) 做卡：sudo ./make_sd.sh /dev/sdX boot.bin zImage system.dtb rootfs.ext4
EOF
