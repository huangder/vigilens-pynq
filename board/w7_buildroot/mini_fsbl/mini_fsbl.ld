/*
 * mini_fsbl.ld —— 链接脚本：一级引导必须落在 OCM
 *
 * Zynq-7000 的低 192 KB OCM = 0x00000000..0x0002FFFF。
 * BootROM 从 SD/QSPI/JTAG 取 BOOT.BIN，把 [bootloader] 分区（本 ELF）载到它的
 * PT_LOAD 地址，然后跳到 ELF 入口。**入口必须是 0x0**，否则 BootROM 跳错地方。
 *
 * 栈放在 0x0002F000（OCM 里，离顶留 4 KB 给 on-chip RAM 尾部的对齐/保留）。
 * 注意：此时 DDR 还没初始化（ps7_init 之后才有），所以栈**只能**在 OCM。
 */

ENTRY(_start)

MEMORY
{
    OCM (rwx) : ORIGIN = 0x00000000, LENGTH = 0x00030000
}

SECTIONS
{
    .text : {
        *(.vectors)
        *(.text)
        *(.text.*)
    } > OCM

    .rodata : {
        *(.rodata)
        *(.rodata.*)
    } > OCM

    .data : {
        *(.data)
        *(.data.*)
    } > OCM

    __bss_start = .;
    .bss : {
        *(.bss)
        *(.bss.*)
        *(COMMON)
    } > OCM
    __bss_end = .;

    . = ALIGN(8);
    __stack_top = ORIGIN(OCM) + LENGTH(OCM) - 0x1000;   /* 0x0002F000 */

    /DISCARD/ : { *(.comment) *(.note*) *(.ARM.attributes) }
}
