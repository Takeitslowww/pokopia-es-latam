/*
 * Hook entry points. Each one replaces a single instruction of `main`
 * (see module/patches.py) and hands the work to a C handler in latam.c.
 *
 * The C handlers get a `struct regs *` with x0-x17, x29 and x30 as they were
 * at the hook site; they may change any of them, and the change is written
 * back before returning to the game. Callee-saved registers (x19-x28) are
 * preserved by the C code itself; the few handlers that need one of them get
 * it as the second argument.
 *
 * Game addresses are reached with plain PC-relative branches computed from
 * the module base (GAME(x)), so the module needs no relocations.
 */
#include "game_offsets.h"

#define GAME(off) (__module_start + ((off) - MODULE_OFFSET))

    .macro SAVE_REGS
    sub  sp, sp, #0xa0
    stp  x0, x1, [sp, #0x00]
    stp  x2, x3, [sp, #0x10]
    stp  x4, x5, [sp, #0x20]
    stp  x6, x7, [sp, #0x30]
    stp  x8, x9, [sp, #0x40]
    stp  x10, x11, [sp, #0x50]
    stp  x12, x13, [sp, #0x60]
    stp  x14, x15, [sp, #0x70]
    stp  x16, x17, [sp, #0x80]
    stp  x29, x30, [sp, #0x90]
    .endm

    .macro RESTORE_REGS
    ldp  x0, x1, [sp, #0x00]
    ldp  x2, x3, [sp, #0x10]
    ldp  x4, x5, [sp, #0x20]
    ldp  x6, x7, [sp, #0x30]
    ldp  x8, x9, [sp, #0x40]
    ldp  x10, x11, [sp, #0x50]
    ldp  x12, x13, [sp, #0x60]
    ldp  x14, x15, [sp, #0x70]
    ldp  x16, x17, [sp, #0x80]
    ldp  x29, x30, [sp, #0x90]
    add  sp, sp, #0xa0
    .endm

    /* call a C handler: handler(struct regs *r, u64 extra) */
    .macro CALL_C handler, extra=xzr
    SAVE_REGS
    mov  x1, \extra
    mov  x0, sp
    bl   \handler
    RESTORE_REGS
    .endm

    .text
    .p2align 2

/* H1 @ 0xffa520 (was `bl 0xffb060`, x0 = screen, x20 = holder of the language
 * list). Append the Latam entry, then tail-call the grid builder; x30 already
 * points back to 0xffa524. */
    .global hook_grid_entries
hook_grid_entries:
    CALL_C on_grid_entries, x20
    b    GAME(GAME_GRID_BUILD)

/* H_IDX @ 0x11238dc (was `cbz x12, 0x11238c8`) inside the LINKDATA_PATCH
 * apply loop: x12 = entry size (u64), x10 = &entry.stored_size, x9 = &record
 * table[i] + 0x13, x22 = LinkData + 0x100c8. Called once per IDX entry, so the
 * common cases stay here: empty entries are skipped, unmarked entries return
 * to the game (0x11238e0), and only 'LATM'-marked ones reach the C handler. */
    .global hook_patch_entry
hook_patch_entry:
    cbz  x12, 1f
    lsr  x16, x12, #32
    movz w17, #(PATCH_MARKER_LATAM & 0xFFFF)
    movk w17, #(PATCH_MARKER_LATAM >> 16), lsl #16
    cmp  w16, w17
    b.eq 2f
    ret
2:  CALL_C on_latam_entry, x22
1:  b    GAME(GAME_PATCH_LOOP_NEXT)

/* H_CMP @ 0x230f74 (boot) and 0xffaa38 (grid confirm), both `ldr w10, [x9]`
 * with x9 = current-language object and w0 = requested language. */
    .global hook_lang_compare
hook_lang_compare:
    CALL_C on_lang_compare
    ret

/* H4 @ 0x2306e0 (was `str w0, [x8, x9]`, the m_Language save store). Reached
 * with `b` because x30 is live there; resumes at 0x2306e4. */
    .global hook_lang_store
hook_lang_store:
    CALL_C on_lang_store
    b    GAME(GAME_LANG_STORE_RESUME)

/* H5 @ 0xffaca4 and 0xffb11c (was `ldr w8, [x8]`, x8 = current-language
 * object). */
    .global hook_lang_current
hook_lang_current:
    CALL_C on_lang_current
    ret

#ifdef LATAM_DEBUG
/* Debug builds: replace the pad reads of player 1 (`bl` to the nn::hid PLT
 * stubs) so a built-in script can press buttons. */
    .global hook_dbg_npad_style
hook_dbg_npad_style:
    CALL_C on_dbg_npad_style
    ret

    .global hook_dbg_npad_state
hook_dbg_npad_state:
    CALL_C on_dbg_npad_state
    ret
#endif
