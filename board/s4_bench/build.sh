#!/usr/bin/env bash
# 编译 S4 裸机自检固件（Cortex-A9，无 libc）。
#
#   bash board/s4_bench/build.sh
#
# 产物（不入库，见 .gitignore）：
#   s4_bench.bin       640×480 档（3/5 抽取 → 384×288 灰度）
#   s4_bench_720.bin   720p60 档（3/8 抽取 → 480×270 灰度）
#
# ⚠️ `-Wl,--build-id=none` 不是可选项：不加的话镜像最前面会多出 36 字节
#    `.note.gnu.build-id`，而驱动是按 `rwr pc 0x00100000` 直接跳的
#    ⇒ CPU 把 build-id 当指令执行，数据中止到异常向量 0x10（日志区一个字都不写）。
#    2026-10-23 就是这么浪费了一轮上板时间。
set -euo pipefail
cd "$(dirname "$0")"

GCC="${GCC:-D:/Xilinx/2026.1/Vitis/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe}"
OCP="${OCP:-D:/Xilinx/2026.1/Vitis/gnu/aarch32/nt/gcc-arm-none-eabi/bin/arm-none-eabi-objcopy.exe}"

FLAGS=(-mcpu=cortex-a9 -marm -nostdlib -nostartfiles -ffreestanding -O2
       -Wl,--build-id=none -Wl,-T,link.ld)

"$GCC" "${FLAGS[@]}" -o s4_bench.elf start.S s4_bench.c
"$OCP" -O binary s4_bench.elf s4_bench.bin

"$GCC" "${FLAGS[@]}" -DFRAME_W=1280u -DFRAME_H=720u -DGRAY_W=480u -DGRAY_H=270u \
       -o s4_bench_720.elf start.S s4_bench.c
"$OCP" -O binary s4_bench_720.elf s4_bench_720.bin

ls -l s4_bench.bin s4_bench_720.bin
