#!/bin/bash
# Preserve the COMPLETE linked SPL + appended DTB byte-for-byte. Bootgen needs
# an ELF load segment to infer the length at address zero; the original SPL
# ELF omits the appended control DTB and cannot be used directly.
set -euo pipefail
work=$(realpath "${1:?work directory required}")
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
cd "$work/boot"
cp uboot/spl/u-boot-spl.bin spl_payload.bin
printf 'ENTRY(_start)\nSECTIONS { . = 0; _start = .; .text : { *(.data) } }\n' > spl_payload.lds
"$br/output/host/bin/arm-linux-objcopy" -I binary -O elf32-littlearm -B arm spl_payload.bin spl_payload.o
"$br/output/host/bin/arm-linux-ld" -T spl_payload.lds spl_payload.o -o spl_payload.elf
"$br/output/host/bin/arm-linux-objcopy" -O binary spl_payload.elf spl_roundtrip.bin
cmp spl_payload.bin spl_roundtrip.bin
echo 'SPL_ELF_ROUNDTRIP_PASS: complete SPL including control DTB'
