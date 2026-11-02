#!/bin/bash
# Build the RAM-only Linux camera/PL experiment with the existing ARM toolchain.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
source_images=${SOURCE_IMAGES:-$repo/.tmp/w7_usb_images}
out=${1:?usage: repack_direct_loop.sh output-directory}
mkdir -p "$out"
out=$(cd "$out" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cc="$br/output/host/bin/arm-linux-gcc"
if [ ! -f "$br/output/staging/usr/lib/libjpeg.a" ]; then
    echo 'Build jpeg-turbo with ENABLE_STATIC=ON first; see DIRECT_LOOP.md' >&2
    exit 1
fi
# Reuse locally cached upstream zlib sources, compiling an ARM static library
# inside this output directory. Never link the Buildroot host's x86 libz.a.
zprefix=${ZLIB_PREFIX:-$out/zlib-arm}
if [ ! -f "$zprefix/lib/libz.a" ]; then
    zsource=${ZLIB_SOURCE:-$br/output/build/host-libzlib-1.3.2}
    if [ ! -f "$zsource/zlib.h" ]; then
        echo 'Set ZLIB_SOURCE to a local upstream zlib source directory' >&2
        exit 1
    fi
    mkdir -p "$out/zlib-build"
    cp -a "$zsource/." "$out/zlib-build/"
    (cd "$out/zlib-build" && make distclean >/dev/null 2>&1 || true)
    (cd "$out/zlib-build" && CC="$cc" AR="$br/output/host/bin/arm-linux-ar" \
        RANLIB="$br/output/host/bin/arm-linux-ranlib" \
        CFLAGS='-O3 -mcpu=cortex-a9 -mfpu=neon' ./configure --static --prefix="$zprefix" \
        && make -j2 && make install)
fi
(cd "$stage" && gzip -dc "$source_images/rootfs.cpio.gz" | cpio -idm --quiet)
"$cc" -std=c11 -D_DEFAULT_SOURCE -DVIGI_CAMERA_PL -O3 -mcpu=cortex-a9 -mfpu=neon -Wall -Wextra -Werror -pthread \
    "$repo/board/w7_buildroot/uvc_probe.c" "$repo/board/w7_buildroot/camera_pl.c" \
    "$repo/board/w7_buildroot/camera_stream.c" \
    -I"$zprefix/include" "$zprefix/lib/libz.a" \
    -Wl,-Bstatic -ljpeg -Wl,-Bdynamic -lm -o "$stage/usr/bin/vigi_camera_pl"
"$br/output/host/bin/arm-linux-strip" --strip-unneeded "$stage/usr/bin/vigi_camera_pl"
cp "$stage/usr/bin/vigi_camera_pl" "$out/vigi_camera_pl"
sha256sum "$out/vigi_camera_pl"
"$cc" -std=c11 -O2 -Wall -Wextra -Werror \
    "$repo/board/w7_buildroot/network_ctl.c" -o "$stage/usr/bin/network_ctl"
"$br/output/host/bin/arm-linux-strip" --strip-unneeded "$stage/usr/bin/network_ctl"
cp "$stage/usr/bin/network_ctl" "$out/network_ctl"
"$cc" -std=c11 -D_DEFAULT_SOURCE -O2 -Wall -Wextra -Werror \
    "$repo/board/w7_buildroot/uvc_probe.c" -o "$stage/usr/bin/uvc_probe"
(cd "$stage" && find . -print0 | LC_ALL=C sort -z | cpio --null -o -H newc --quiet | gzip -n -9) > "$out/rootfs.cpio.gz"
"$br/output/host/bin/mkimage" -A arm -O linux -T ramdisk -C gzip \
    -n VigiLens-direct-loop -d "$out/rootfs.cpio.gz" "$out/uInitrd"
"$br/output/host/bin/mkimage" -A arm -O linux -T script -C none \
    -n VigiLens-direct-loop -d "$repo/board/w7_buildroot/boot_direct_loop.cmd" "$out/boot_jtag.scr"
for image in zImage u-boot-dtb.bin; do cp "$source_images/$image" "$out/$image"; done
sha256sum "$out/uInitrd" "$out/boot_jtag.scr"
