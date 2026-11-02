/*
 * shim/xil_io.h —— 极简 MMIO 访问（替代 standalone 的 xil_io.h）
 *
 * 真 xil_io.h 会拉进 xil_types.h → bspconfig.h / xparameters.h（BSP 生成物）。
 * ps7_init.c 只 `#include "xil_io.h"`，实际用的是直接指针写寄存器，
 * 所以我们给一份自足的头就够，且**不再依赖任何 Vitis BSP**。
 *
 * 只保留 32 位访问：Zynq 的 SLCR/DDRC/UART 寄存器都是 32 位对齐的，
 * 多了容易被误用成非对齐访问（Cortex-A9 上非对齐 32 位访问会异常）。
 */

#ifndef MINI_XIL_IO_H
#define MINI_XIL_IO_H

#include "xil_types.h"

static inline void Xil_Out32(UINTPTR Addr, u32 Value)
{
    *(volatile u32 *)Addr = Value;
}

static inline u32 Xil_In32(UINTPTR Addr)
{
    return *(volatile u32 *)Addr;
}

static inline void Xil_Out16(UINTPTR Addr, u16 Value)
{
    *(volatile u16 *)Addr = Value;
}

static inline u16 Xil_In16(UINTPTR Addr)
{
    return *(volatile u16 *)Addr;
}

static inline void Xil_Out8(UINTPTR Addr, u8 Value)
{
    *(volatile u8 *)Addr = Value;
}

static inline u8 Xil_In8(UINTPTR Addr)
{
    return *(volatile u8 *)Addr;
}

#endif /* MINI_XIL_IO_H */
