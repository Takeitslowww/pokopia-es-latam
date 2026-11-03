/*
 * Module header for the game's rtld.
 *
 * The rtld of this game accepts a freestanding module: word 0 = 0 (no
 * entry branch), word 1 = offset of the MOD0 block, then MOD0 with the
 * dynamic section, BSS bounds, eh_frame_hdr bounds and a module object.
 * The module has no relocations, so it works at whatever address it is
 * loaded; Eden loads it at main + MODULE_OFFSET (see patches.py).
 */
    .section .text.header, "ax"
    .global __module_start
    .hidden __module_start
__module_start:
    .word 0
    .word mod0 - __module_start
    .word 0, 0

mod0:
    .ascii "MOD0"
    .word _DYNAMIC - mod0
    .word __bss_start - mod0
    .word __bss_end - mod0
    .word __eh_frame_hdr_start - mod0
    .word __eh_frame_hdr_end - mod0
    .word __module_object - mod0

    .section .bss.module_object, "aw", %nobits
    .p2align 4
__module_object:
    .space 0x200
