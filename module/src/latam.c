/*
 * Espanol (Latinoamerica) as a 10th language for Pokemon Pokopia 2.0.1.
 *
 * The game knows 9 languages (ids 0-8; Spanish = 5). The mod adds id 9 on the
 * first-boot language grid and in the save file only. Everywhere else the game
 * keeps running in Spanish (5): the current-language object never holds 9, so
 * the ~320 places that read it keep working. "Latam" is a module flag that
 * swaps the Spanish text records for the Latin-American ones:
 *
 *   romfs/LINKDATA_PATCH.IDX entries whose size has 'LATM' in its upper 32
 *   bits are not applied by the game (hook_patch_entry). The module keeps the
 *   original record-table entry and the Latam one, and writes one or the
 *   other into the table when the flag changes; the game then reloads every
 *   localized table through its own language-change callback.
 *
 * Flow:
 *   grid shows 10 buttons (on_grid_entries) -> user picks 9 -> confirm ->
 *   on_lang_compare maps 9 to 5, turns the flag on, and forces the game's own
 *   change callback (store + reload) -> on_lang_store writes 9, not 5, to the
 *   save. At boot the saved 9 passes the range check (patched) and goes
 *   through on_lang_compare the same way.
 */
#include "module.h"

#define TABLE_ENTRY_SIZE 24      /* LinkData record table entry */
#define MAX_CAPTURED 1024        /* Latam records (369 Spanish-slot records + room) */

struct captured {
    u32 record;
    u32 reserved;
    u8 latam[TABLE_ENTRY_SIZE];  /* entry pointing at the Latam blob in LINKDATA_PATCH.BIN */
    u8 orig[TABLE_ENTRY_SIZE];   /* entry as the game had it (Spain text) */
};

static struct {
    uptr table;                  /* LinkData record table (12,298 entries) */
    u32 count;
    u32 active;                  /* Latam text is in the table */
    u32 dropped;                 /* entries that did not fit or were invalid */
    struct captured e[MAX_CAPTURED];
} g;

int latam_active(void) { return g.active; }
u32 latam_captured(void) { return g.count; }

/* ------------------------------------------------------------------------- */
/* LINKDATA_PATCH entries                                                    */
/* ------------------------------------------------------------------------- */

static void copy24(uptr dst, const u8 *src)
{
    u64 *d = (u64 *)dst;
    const u64 *s = (const u64 *)src;
    d[0] = s[0];
    d[1] = s[1];
    d[2] = s[2];
}

static void set_active(u32 on)
{
    if (on == g.active)
        return;
    for (u32 i = 0; i < g.count; i++) {
        struct captured *c = &g.e[i];
        copy24(g.table + (uptr)c->record * TABLE_ENTRY_SIZE, on ? c->latam : c->orig);
    }
    g.active = on;
    DBG("latam: %s (%d records swapped)", on ? "ON" : "OFF", g.count);
}

/*
 * A 'LATM'-marked LINKDATA_PATCH.IDX entry (hook_patch_entry).
 * IDX entry: u64 offset, u64 size ('LATM' << 32 | real size), u64 stored size,
 * u64 flag (low 32 bits: compressed; high 32 bits: target record, 0 = the
 * entry's own index). The game's own write of a patch entry (0x11238e0) is:
 *   +0x00 u32 size, +0x04 u32 stored size, +0x08 u64 offset,
 *   +0x12 u8 compressed, +0x13 u8 from LINKDATA_PATCH (= 1)
 * and leaves +0x10, +0x11, +0x14..+0x17 alone. The Latam entry is built the
 * same way on top of the original.
 */
void HIDDEN on_latam_entry(struct regs *r, uptr loop_base)
{
    const uptr table = loop_base + GAME_PATCH_TABLE_FROM_X22;
    const uptr slot = r->x[9] - 0x13;
    const u64 *idx = (const u64 *)(r->x[10] - 0x10);
    u32 record = (u32)(idx[3] >> 32);

    if (slot < table || (slot - table) % TABLE_ENTRY_SIZE) {
        g.dropped++;
        return;
    }
    if (record == 0)
        record = (u32)((slot - table) / TABLE_ENTRY_SIZE);
    if (record >= LINKDATA_RECORDS) {
        g.dropped++;
        return;
    }
    g.table = table;
    const uptr dst = table + (uptr)record * TABLE_ENTRY_SIZE;

    struct captured *c = 0;
    for (u32 i = 0; i < g.count; i++)
        if (g.e[i].record == record)
            c = &g.e[i];
    if (!c) {
        if (g.count >= MAX_CAPTURED) {
            g.dropped++;
            return;
        }
        c = &g.e[g.count++];
        c->record = record;
        memcpy(c->orig, (const void *)dst, TABLE_ENTRY_SIZE);
    }
    memcpy(c->latam, c->orig, TABLE_ENTRY_SIZE);
    *(u32 *)(c->latam + 0x00) = (u32)idx[1];          /* real size */
    *(u32 *)(c->latam + 0x04) = (u32)idx[2];
    *(u64 *)(c->latam + 0x08) = idx[0];
    c->latam[0x12] = (u32)idx[3] != 0;
    c->latam[0x13] = 1;
    if (g.active)
        copy24(dst, c->latam);
}

/* ------------------------------------------------------------------------- */
/* language id                                                               */
/* ------------------------------------------------------------------------- */

/*
 * H_CMP: `ldr w10, [x9]` before `cmp w10, w0; b.eq skip; str w0, [x9];
 * call [obj+8](w0)`. x9 = current-language object, w0 = requested language.
 * 9 becomes 5 with the Latam flag on; 0-8 turn it off. If only the flag
 * changed (Spain <-> Latam), w10 is made to differ so the game still runs its
 * store + reload callback.
 */
void HIDDEN on_lang_compare(struct regs *r)
{
    const u32 current = rd32(r->x[9]);
    const u32 asked = (u32)r->x[0];

    r->x[10] = current;
    if (asked > LANG_LATAM)
        return;                                   /* not a language (e.g. -1) */
    const u32 want = asked == LANG_LATAM;
    const u32 request = want ? LANG_ES : asked;
    r->x[0] = request;
    DBG("lang: request %d, current %d, latam %d -> %d", asked, current, g.active, want);
    if (want != g.active) {
        set_active(want);
        r->x[10] = ~request;                      /* force the change callback */
    }
}

/* H4: `str w0, [x8, x9]` stores m_Language in the save. */
void HIDDEN on_lang_store(struct regs *r)
{
    u32 value = (u32)r->x[0];
    if (g.active && value == LANG_ES)
        value = LANG_LATAM;
    wr32(r->x[8] + r->x[9], value);
    DBG("lang: save m_Language = %d", value);
}

/* H5: `ldr w8, [x8]` reads the current language for the grid / confirm logic. */
void HIDDEN on_lang_current(struct regs *r)
{
    u32 value = rd32(r->x[8]);
    if (g.active && value == LANG_ES)
        value = LANG_LATAM;
    r->x[8] = value;
}

/* ------------------------------------------------------------------------- */
/* language grid                                                             */
/* ------------------------------------------------------------------------- */

/* H1: x20 + 8 holds the entry list {entry *data; u64 count; u64 capacity},
 * entry = {u32 0x2f, u32 position, u32 language}. */
void HIDDEN on_grid_entries(struct regs *r, uptr holder)
{
    (void)r;
    const uptr list = rd64(holder + 8);
    if (!list)
        return;
    const u64 count = rd64(list + 8), capacity = rd64(list + 16);
    if (count >= capacity)
        return;
    u32 *entry = (u32 *)(rd64(list) + count * 12);
    entry[0] = 0x2f;
    entry[1] = (u32)count;
    entry[2] = LANG_LATAM;
    *(u64 *)(list + 8) = count + 1;
#ifdef LATAM_DEBUG
    dbg_grid_shown();
#endif
}

/* ------------------------------------------------------------------------- */
/* freestanding helpers (the compiler may emit calls to these)               */
/* ------------------------------------------------------------------------- */

void *memcpy(void *dst, const void *src, u64 n)
{
    u8 *d = dst;
    const u8 *s = src;
    while (n--)
        *d++ = *s++;
    return dst;
}

void *memset(void *dst, int c, u64 n)
{
    u8 *d = dst;
    while (n--)
        *d++ = (u8)c;
    return dst;
}
