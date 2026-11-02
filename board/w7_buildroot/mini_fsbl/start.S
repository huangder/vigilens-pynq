/*
 * start.S —— mini_fsbl 的入口（AArch32 / Cortex-A9）
 *
 * BootROM 把一级引导载入 OCM（0x00000000 起）并从 ELF 入口跳进来。
 * 这里只做三件事：设栈 → 清 .bss → 调 C 的 main()；main 不返回。
 *
 * 【与 Xilinx FSBL 的区别】这是**自己写的**最小一级引导，不是 Xilinx 的 zynq_fsbl；
 * 它只做 ps7_init（DDR/时钟/MIO）+ 串口报活 + 跳 U-Boot，没有 SD/QSPI/NAND 启动、
 * 没有镜像校验、没有 MD5/RSA。够用来回答"板子能不能起来、串口通不通"。
 */

	.syntax unified
	.arm
	.text
	.global _start
	.type _start, %function

_start:
	/* CPU 处于 SVC 模式、IRQ/FIQ 关闭（BootROM 的既定状态），只需设栈 */
	ldr	sp, =__stack_top

	/* 清 .bss（C 的静态变量必须从 0 开始） */
	ldr	r0, =__bss_start
	ldr	r1, =__bss_end
	mov	r2, #0
1:	cmp	r0, r1
	bge	3f
	str	r2, [r0], #4
	b	1b
3:
	bl	main

	/* main 正常返回也不该发生：原地停住，别跑飞 */
4:	b	4b
	.size _start, .-_start
	.ltorg
