/*
 * shim/xil_types.h —— 极简类型定义（替代 BSP 生成的 xil_types.h）
 *
 * 为什么需要这个：真正的 `standalone_v9_5/src/common/xil_types.h` 会
 * `#include "bspconfig.h"` 与 `"xparameters.h"` —— 这两个都是 **BSP 工具生成的**，
 * 我们不想为了编 3 个文件去跑一遍 BSP 生成器。
 *
 * 这里只提供 ps7_init.c / mini_fsbl.c 真正用到的那一小撮定义。
 * 一旦代码开始用 Xil_Assert / XTime / cache 操作，就该回到真 BSP，而不是往这里堆。
 */

#ifndef MINI_XIL_TYPES_H
#define MINI_XIL_TYPES_H

#include <stdint.h>
#include <stddef.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int8_t   s8;
typedef int16_t  s16;
typedef int32_t  s32;
typedef int64_t  s64;
typedef intptr_t INTPTR;
typedef uintptr_t UINTPTR;

#ifndef TRUE
#define TRUE  1U
#endif
#ifndef FALSE
#define FALSE 0U
#endif

#endif /* MINI_XIL_TYPES_H */
