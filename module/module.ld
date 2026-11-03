/*
 * Layout of subsdk9: three page-aligned segments (text, ro, data+bss), as an
 * NSO needs them. Linked with `ld.lld -shared -Bsymbolic`; the build checks
 * that the result has no dynamic relocations.
 */
PHDRS {
  text PT_LOAD FLAGS(5);
  ro   PT_LOAD FLAGS(4);
  data PT_LOAD FLAGS(6);
  dyn  PT_DYNAMIC FLAGS(6);
}

SECTIONS {
  . = 0;
  .text : {
    KEEP(*(.text.header))
    *(.text .text.*)
  } :text

  . = ALIGN(0x1000);
  .rodata : { *(.rodata .rodata.*) } :ro
  .hash : { *(.hash) } :ro
  .gnu.hash : { *(.gnu.hash) } :ro
  .dynsym : { *(.dynsym) } :ro
  .dynstr : { *(.dynstr) } :ro
  .rela.dyn : { *(.rela.dyn) } :ro
  .eh_frame_hdr : {
    __eh_frame_hdr_start = .;
    *(.eh_frame_hdr)
    __eh_frame_hdr_end = .;
  } :ro
  .eh_frame : { *(.eh_frame) } :ro

  . = ALIGN(0x1000);
  .data : { *(.data .data.*) } :data
  .dynamic : { *(.dynamic) } :data :dyn
  .got : { *(.got .got.*) } :data
  . = ALIGN(16);
  __bss_start = .;
  .bss : { *(.bss .bss.*) *(COMMON) } :data
  . = ALIGN(16);
  __bss_end = .;

  /DISCARD/ : { *(.comment) *(.note*) *(.ARM.attributes) *(.interp) }
}
