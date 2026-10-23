ENTRY(_start)
SECTIONS
{
  . = 0x00100000;
  .text : { *(.text.start) *(.text*) }
  .rodata : { *(.rodata*) }
  .data : { *(.data*) }
  .bss : { __bss_start = .; *(.bss*) *(COMMON) __bss_end = .; }
  /DISCARD/ : { *(.comment) *(.ARM.attributes) }
}
