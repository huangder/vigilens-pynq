#!/bin/bash
# Only builds ordinary files in a NEW prepared directory. No disks/loop devices.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
out=$(realpath "${1:?prepared output directory required}")
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
source=${UBOOT_SOURCE:-$br/output/build/uboot-2024.10}
here=$(cd "$(dirname "$0")" && pwd)
test -f "$out/manifest.json"
test -f "$out/ps7_validated.c"
if [ -e "$out/uboot" ] && [ "${RESUME_BUILD:-0}" != 1 ]; then
  echo 'private build directory exists; use RESUME_BUILD=1 to resume it' >&2; exit 1
fi
# Work on a private copy: never change the existing Buildroot build.
if [ ! -e "$out/uboot" ]; then cp -a "$source" "$out/uboot"; fi
u="$out/uboot"
cross="$br/output/host/bin/arm-linux-"
cp "$here/uboot_mizar_candidate.dts" "$u/arch/arm/dts/zynq-mizar-candidate.dts"
# Retain the local U-Boot configuration actually used in JTAG boots.
# Replace its PS initializer explicitly; no built-in board DDR is selected.
"$u/scripts/config" --file "$u/.config" --set-str XILINX_PS_INIT_FILE "$out/ps7_validated.c"
"$u/scripts/config" --file "$u/.config" --set-str DEFAULT_DEVICE_TREE zynq-mizar-candidate
"$u/scripts/config" --file "$u/.config" --set-str OF_LIST zynq-mizar-candidate
"$u/scripts/config" --file "$u/.config" --set-str SPL_OF_LIST zynq-mizar-candidate
# The SD appliance has no UEFI capsule use. Avoid the unrelated host uuid
# development dependency which interrupted the previous isolated candidate.
"$u/scripts/config" --file "$u/.config" --disable TOOLS_MKEFICAPSULE
# Use a deterministic read-only boot policy; stale card/flash environments
# must not select a different DDR initializer or bypass our checked boot.scr.
for symbol in ENV_IS_IN_FAT ENV_IS_IN_NAND ENV_IS_IN_SPI_FLASH; do
  "$u/scripts/config" --file "$u/.config" --disable "$symbol"
done
"$u/scripts/config" --file "$u/.config" --enable ENV_IS_NOWHERE
# scripts/config uses a sed replacement and does not escape '&'. Nested hush
# conditionals preserve fail-closed behavior without corrupting the C string.
"$u/scripts/config" --file "$u/.config" --set-str BOOTCOMMAND 'if mmc dev 0; then if mmc rescan; then if fatload mmc 0:1 0x03000000 boot.scr; then source 0x03000000; else echo VIGILENS_SD_SCRIPT_MISSING; fi; fi; fi'
export HOSTCFLAGS="-O2 -I$br/output/host/include"
export HOSTLDFLAGS="-L$br/output/host/lib -Wl,-rpath,$br/output/host/lib"
make -C "$u" ARCH=arm CROSS_COMPILE="$cross" olddefconfig > "$out/uboot_config.log" 2>&1
make -C "$u" ARCH=arm CROSS_COMPILE="$cross" -j"${JOBS:-4}" > "$out/uboot_build.log" 2>&1
cp "$u/spl/boot.bin" "$out/fat/BOOT.BIN"
cp "$u/u-boot.img" "$out/fat/u-boot.img"
"$br/output/host/bin/mkimage" -A arm -O linux -T script -C none -n VigiLens-SD-candidate \
  -d "$out/fat/boot.cmd" "$out/fat/boot.scr" > "$out/mkimage.log"
"$cross"nm "$u/spl/u-boot-spl" | grep -E ' [Tt] (ps7_init|prepare_peripherals|ps7_ddr_init_data)' > "$out/ps_symbols.txt"
grep -q ' T ps7_init$' "$out/ps_symbols.txt"
# The custom XILINX_PS_INIT_FILE rule records gcc dependencies in .o.d,
# unlike the normal per-board Kbuild rule, which produces .o.cmd.
grep -q "$out/ps7_validated.c" "$u/spl/board/xilinx/zynq/.ps_init_gpl.o.d"
sha256sum "$out"/fat/* > "$out/SHA256SUMS"
echo 'UBOOT_BUILD_COMPLETE: generate final BootROM image with board/cold_boot/Generate-BootImage.ps1, then seal/verify; SD boot UNVERIFIED'
