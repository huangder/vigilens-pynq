/*
 * link.ld —— 把裸机程序放到 DDR 0x00100000
 * （Zynq-7000 的 DDR 从 0x0 开始；0x0~0x100000 留出来，避免和 QEMU/BootROM 的用法撞）
 */
ENTRY(_start)
SECTIONS
{
    . = 0x00100000;
    .text   : { *(.text.start) *(.text*) }
    .rodata : { *(.rodata*) }
    .data   : { *(.data*) }
    .bss    : { *(.bss*) *(COMMON) }
    . = ALIGN(8);
    _end = .;
}
