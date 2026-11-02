/* S4 裸机入口：A9 由 xsdb 用 rwr pc 跳进来，栈放 DDR 0x0F000000。
 * 注意：-nostdlib 没有 CRT0，.bss 必须在这里手工清零，否则 static 变量是 DDR 上电垃圾
 * （上一版没清，导致 loglen 初值是 0x806808A0，日志一个字都没写出来）。 */
    .section .text.start, "ax"
    .global _start
    .arm
_start:
    ldr sp, =0x0F000000
    ldr r0, =__bss_start
    ldr r1, =__bss_end
    mov r2, #0
1:
    cmp r0, r1
    strlo r2, [r0], #4
    blo 1b
    bl main
2:
    b 2b
