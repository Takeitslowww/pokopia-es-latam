# Reverse-engineering findings — Pokopia 2.0.1 (update main build-id 76A489F3BD3642FA5E18BD83D32C98BACC5F7D0E)

All addresses are offsets from the `main` module base. IPS offset = 0x100 + main offset (Eden patches the decompressed NSO image, header included).

## Verified in the emulator

- **LINKDATA_PATCH overlay.** `romfs/LINKDATA_PATCH.IDX` + `.BIN` (LayeredFS-added files) replace records at boot. The apply loop is at `0x11238b4`; the record table is at `LinkData obj+0x10718` (12,298 entries × 0x18). Uncompressed entries are verified. An oversized IDX is not bounds-checked, so keep it ≤ 12,298 entries.
- **Custom `subsdk9`.** The game's rtld accepts a freestanding module with:
  - classic header (word0 = 0) and a MOD0 block;
  - a sysv hash table;
  - no relocations;
  - an uncompressed NSO.
  
  Built with LLVM 22 (`C:\Program Files\LLVM\bin`). It loads at main + 0x3E60000. A `bl` main→module and a `b` module→main both work.
- **10th button.** Hook H1 at `0xffa520` appends entry `{0x2f, 9, 9}`. The grid shows 10 buttons. Without a label entry the 10th label falls back to the layout default "English".
- **Labels.** Grid labels do NOT come from records 4663–4671. They come from resource IDs (table at `0x283ebbc`; helper `0xa7c5cc`). The helper looks the ID up in data table #551 (`mgr 0x35618f0 + 0x1148`) = LINKDATA record 1552 (table i = record 1001 + i): 64-byte header, then 8-byte rows `{u32 sprite-name hash, u16 UI asset id, u16 0}`, row = ID. Row 0x101 = hash of `0030_prt_language_2`, asset 10 (the shared UI atlas); row 0x102 is all zero. The hash is `h = (h + c) * 31` over the name from its last byte to its first (matches all 9 label rows). From the data and the disassembly; the new row 0x102 is not yet checked in the emulator.
- **Latam capture.** Hook H_IDX at `0x11238dc` skips IDX entries whose `unc` upper 32 bits == `'LATM'` (0x4C41544D) and keeps them for later swapping. When the module writes a captured entry, the new text appears.

## From static analysis (untested)

- **Current language.** Stored in the object at `0x36ca680`: `+0` holds the language ID, `+8` the callback `0x2306cc`. About 320 readers, so the stored value must never be 9.
- **Language change.** The callback stores the language to the save, then reloads every localized table via `0xa34ac0`.
- **Save field.** `m_Language` is at `[0x3561930]+0x13d40b0`. The menu writes the raw button ID at `0xffaad4`.
- **Boot check.** The only range check is `0x230f5c cmp w0,#8`. Without the mod, a saved 9 falls back to the system language.

## Hook list

| Site | Change / module work | Status |
|---|---|---|
| `0xffb138` | `cmp w27,#9` → `#10` (show button 9) | IPS |
| `0xffb1f0` | `#8` → `#9` (look up a label for button 9) | IPS |
| `0x283ebce` | = `0x0102` (padding after the 9-entry label table). Labels are images in UI asset 10 (`0030_prt_language_<n>`, 360x64); `_3` (es-419) does not exist in the game. LINKDATA_PATCH fills row 0x102 of record 1552 (always on): the new sprite `0030_prt_language_3` that `assettool build` adds, or a copy of the Spain row 0x101 | IPS; `0x0101` (Spain image) verified, `0x0102` not yet run |
| `0xffb010` | `cmp w22,#8` → `#9` (confirm text for button 9) | IPS |
| `0x283ebba` | = `0x0050` (confirm row 80; empty in records 2323–2331) | IPS |
| `0x230f5c` | `cmp w0,#8` → `#9` (boot accepts a saved 9) | IPS |
| H1 `0xffa520` | add the 10th entry, then `b 0xffb060` | verified |
| H_IDX `0x11238dc` | keep LATM entries and the originals for swapping | verified |
| H_CMP `0xffaa38`, `0x230f74` | map 9→5 and set the Latam flag; if the flag changed, swap entries and force the game's own store + reload | verified at both: `0xffaa38` (grid), `0x230f74` (reboot with a saved 9: Latam on, title screen in Latam text) |
| H4 `0x2306e0` | must be `b` (x30 is live); when Latam, store 9 instead of 5, then `b 0x2306e4` | verified (SYDAT.BIN offset 0x67d = 9) |
| H5 `0xffaca4`, `0xffb11c` | report 5 as 9 when Latam (needed to go back from Latam to Spain) | in place, path not exercised |

## Data contract

Each Latam record replacing Spanish record `r = 1914 + 9k` goes in LINKDATA_PATCH at IDX index `r`, stored raw, with `unc = (0x4C41544D << 32) | real_size`. If index `r` holds an always-on (unmarked) entry for the same record, the Latam entry goes to a free index from 8953 on, with `flag = r << 32`. Unmarked entries are applied by the game as usual (the es-419 confirm prompt, row 80 of 2323–2331, attribute 1). See `tools/README.md`.

## Known risks

- **Load address is fixed.** The IPS branches into main+0x3E60000. Booting the original exefs (without subsdk9) while the IPS is installed jumps into `sdk`, so it is expected to crash (not tested).
- **First-boot screen until the intro is saved.** The game writes the language to `S00/SYDAT.BIN` right away, but shows the language grid again on every boot until the intro is finished and the main save (`G00/`) exists. This is the game's own behavior (same with Español (España)); the `0x230f74` boot path only runs after that.
- **Options menu untraced.** The in-game language change path (the "must restart" message, row 72) is not traced.
- **Build-specific.** The module offset is specific to Eden and this build.
