/*
 * start.S —— 裸机入口（QEMU xilinx-zynq-a9 / 真板 Cortex-A9 都能用）
 *
 * QEMU 用 `-kernel <elf>` 时会把 ELF 装到它自己的地址并跳到 entry，
 * 所以我们只需要自己搭栈，然后进 C。
 */
    .section .text.start, "ax"
    .global _start
_start:
    ldr sp, =0x00200000        /* DDR 1 MB 处，离代码 0x00100000 留出 1 MB 栈空间 */
    bl  main
1:  b   1b                     /* main 返回就停在这里，别跑飞 */
    .size _start, . - _start
