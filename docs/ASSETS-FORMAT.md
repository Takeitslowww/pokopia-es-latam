# Image assets: formats and localization (Pokopia v2.0.1)

Notes behind `tools/assettool`. "Verified" means checked against the real
files of the v2.0.1 romfs (update 655360); "inferred" means consistent with
the data but not proven.

All paths are under `romfs/motor_asset_re/KIDS_PLATFORM_OUNCE/`.

## Resource database (verified)

| file | content |
|---|---|
| `root.rdb` | index of 60,722 resources (`_DRK0000`) |
| `root.rdx` | 45 records: fdata index → fdata file |
| `*.fdata`, `MountPoint_<n>/*.fdata` | resource data (`PDRK0000`) |
| `data/*.file` | 5 external files (audio banks), not used here |

**`root.rdb` header** (0x20 bytes): `"_DRK0000"`, u32 header size (0x20),
u32 0x18, u32 entry count, u32 KTID, `"data/"` padded to 8 bytes.

**`IDRK` entry** (root.rdb and fdata alike), 4-byte aligned in root.rdb:

| off | type | field |
|---|---|---|
| 0x00 | char[8] | `"IDRK0000"` |
| 0x08 | u64 | entry size |
| 0x10 | u64 | payload size (the last bytes of the entry) |
| 0x18 | u64 | data size (decompressed) |
| 0x20 | u32 | parameter count |
| 0x24 | u32 | KTID (resource ID) |
| 0x28 | u32 | type KTID |
| 0x2c | u32 | flags |
| 0x30 | ... | parameter block, then payload |

In root.rdb the payload is a 13-byte **location blob**: `01 04`, u32 offset,
u32 stored size, u16 fdata index, u8 0. The other kind (`01 0c`) only appears
on the 5 `data/*.file` entries. All 54,651 locations whose fdata exists point
at an `IDRK` with the same KTID, type and data size.

**`root.rdx`**: 8-byte records `u16 index, u8 mount, u8 0xff, u32 KTID`. The
file is `0x<KTID>.fdata` in the root folder (mount 0xff) or in
`MountPoint_<mount>/`. One record has mount 0xfe; its file is absent, so its
meaning is unknown.

**Payload compression**, flag `0x00400000`: chunks of `u16 zlib size`,
8 bytes (not needed for decoding; possibly a checksum), then a zlib stream;
a `u16 0` ends the list. Flag `0x00020000` is set on every root.rdb entry
that points into an fdata file. Raw payloads (flags 0) also load: the PT-BR
mod uses them (below).

**Checksum.** In an fdata entry the first u32 of the parameter block is the
CRC-32 of the stored payload (as stored, compressed or not); in root.rdb it
is 0. Holds for all 520 entries of `0x4B13D561.fdata` and all 560 of the
PT-BR mod's copy (its 40 raw entries carry the CRC of their new payload).

**Missing data.** 23 fdata files named by `root.rdx` are not in the dump: all
of `MountPoint_3`, 7 root files, 5 in MountPoint_1/2, and the mount-0xfe one.
6,066 entries (557 textures) point at them. None of the localized packs
below, or their textures, is among them.

### Types used here

| type KTID | content |
|---|---|
| `0xAFBEC60C` | G1T texture |
| `0xF20DE437` | sprite set: sprite names + UV rectangles of one texture |
| `0x20A6A0BB` | KIDS object database (`_DOK`) |
| `0x5599AA51` | UI layout (`LCSK2600`), not needed for extraction |

**KTIDs** behave like a base-31 polynomial hash of a name (inferred). The
`MountPoint_1/2/3` copies of a file differ by exactly 31³ (0x745F). The
language offsets below scale by 31⁸ between packs. No name → KTID function
was confirmed: plain base-31 variants over names and short codes gave no
matches.

## `_DOK` object databases (verified)

`"_DOK0000"`, u32 header size (0x1c), u32 0x18, u32 object count, u32 KTID,
u32 total size. Then objects: `"IDOK0000"`, u32 size, u32 KTID, u32 type,
u32 property count, `count × (u32 kind, u32 value count, u32 name hash)`,
then the values. Kind 5 values are u32 KTID references. Other kinds have other
widths, so parse values per object.

A **texture binding** is an object of type `0x79991E79` with three kind-5
properties:
1. the texture KTID;
2. the sprite-set KTID;
3. an alias object KTID (alias objects have only this property set).

A *texture pack* is a `_DOK` that holds bindings. There are 34. All 361 UI
screens (objects of type `0x63336873` in `_DOK 0x208BE13A`) list the same
35 packs.

## Sprite sets (verified)

u32 count, then 104 bytes per sprite (not sorted: in the UI atlas set the
language labels come last):
- char[64] name;
- u32 index of the sub-texture inside the G1T;
- 4 corners as `(f32 u, f32 v)`, in the order top-left, top-right,
  bottom-left, bottom-right;
- u32 1.

The pixel rect is `uv × sub-texture size`. The game finds a sprite by the
hash of its name, `h = (h + c) * 31` (mod 2³²) over the bytes from last to
first (matches the label table below and the runtime sprite lists).

## G1T textures (verified)

| off | field |
|---|---|
| 0x00 | `"GT1G"`, 4 ASCII version digits (`0600` for every localized texture) |
| 0x08 | u32 file size, u32 table offset (0x1c), u32 texture count, u32 platform (0x18 = Switch 2), u32 0 |
| table | u32 per texture, offset relative to the table |

Each texture header is 8 bytes:
- u8 `mips << 4`;
- u8 format;
- u8 `log2(h) << 4 | log2(w)`;
- u8 0;
- 4 flag bytes.

When `flags[3] >> 4 != 0`, a 12-byte extended header follows: `u32 12`, then
8 bytes, e.g. `00000000 00000001`. That rule fits all 14,383 texture headers
checked. Pixel data runs to the next texture header, with mip 0 first. A G1T
may hold many sub-textures: the shared UI atlas holds 62.

**Mips.** The levels follow mip 0 back to back; each is swizzled on its own,
with the block height of its own size. This fits all 909 plain 2D textures
with mips. The 49 localized textures all have a single level.

**Formats.**
- `0x62` BC3 and `0x66` BC7: verified by eye and by the smoothness test below.
  These are the only formats among the localized textures.
- `0x60` BC1, `0x63` BC4, `0x64` BC5: assumed from the same family. They are
  decoded but were not checked by eye.
- `0x65`: probably BC6H; not decoded.
- `0x0a`–`0x0d`: uncompressed; not decoded.

**Swizzle: Switch 2 block-linear.** The layout uses NVIDIA GOBs: 64 bytes ×
8 rows, stacked `block_height` GOBs high. `block_height` is the smallest power
of two ≤ 16 with `8 × bh ≥` the height in 4×4 blocks. Blocks go left to right,
then top to bottom. Inside a GOB the bit order is **not** the Tegra X1
(Switch 1) one:

```
byte address bits, low to high, x in bytes, y in block rows
Switch 2 (this game): x0 x1 x2 x3 y0 y1 x4 y2 x5
Tegra X1 (Switch 1):  x0 x1 x2 x3 y0 x4 y1 y2 x5
```

How this was found:
- **Brute force.** All 120 orders of the 5 upper bits were tried on BC3 and
  BC7 textures. Sizes were 64² to 1024×1024, block heights 2–16. The order
  above always gave the smoothest image, together with the standard block
  height.
- **Decode check.** Of the 97 decodes behind the extracted assets, the
  Switch 2 order beats the Tegra order in all 97, by a roughness ratio of at
  least 1.78. The decoded images show clean edges and text.

## How images are localized (verified, with the evidence)

- **Language sets.** Localized images live in **3 language sets** of
  **9 parallel texture packs**, one per language. The packs of a set bind the
  same sprite sets to per-language textures.
  - **set3**: 42 images (tutorial screenshots, title logo, banners).
  - **set1**: 3 images (MountPoint_2 data).
  - **set2**: 2 images (MountPoint_1 data).
- **Language of each pack.** set3 was identified by decoding one sprite
  (`still_tutorial_236`) in all 9 packs and reading the text. That pack is
  ja/en/fr/it/de/es/zh-Hant/ko/zh-Hans as listed in the manifest. The Spanish
  pack is `0xA11C0799`; the English one, `0xDCAF5F9F`, is the pack whose
  textures the PT-BR mod replaced.
- **KTID fingerprint.** Within set3, 40 of 42 textures per language satisfy
  `KTID(L) = KTID(en) + offset(L)` with a fixed per-language offset (table in
  `gamedata.LANG_KTID_OFFSETS`). Sets 1 and 2 match the same offsets × 31⁸
  for all 8 languages at once, which confirms their language order without
  looking at them. The two other set3 images use shared textures: title logo
  and counter labels. There, en/ja/ko/zh share one texture and the European
  languages differ. Their es/en pair is found through the shared sprite set.
- **Sweep.** Every one of the 14,965 texture KTIDs in root.rdb was probed
  with the offsets at scales 31^-12 … 31^19. That includes textures whose
  data is missing. Only the 45 English textures of the 3 sets have language
  siblings. No localized texture exists outside the packs.
- **Shared atlas.** The language-selection labels are not localized. They are
  9 sprites `0030_prt_language_<n>` in one shared atlas (G1T `0xBC2C37F5`,
  sub-texture 32, 4096×2048 BC7), one sprite per language:
  - 1 en, 2 es, 4 fr, 5 de, 6 it, 7 ja, 8 ko, 9 zh-Hans, 10 zh-Hant
    (identified by eye);
  - `_3` (es-419) does not exist; `assettool build` adds it (below).
  - The grid takes each button's label from **label table** LINKDATA record
    1552: 64-byte header, then 8-byte rows `{u32 sprite-name hash, u16 UI
    asset id, u16 0}`. A language's resource ID (main table `0x283ebbc`) is
    its row: es = 0x101 → hash of `0030_prt_language_2`, asset 10 (this
    atlas). Row 0x102 is empty. See `docs/RE-FINDINGS.md`.
  - The sprites are white; the button tints them (dark grey, highlight
    colour when selected).
- **Inferred: pack order.** The order of packs inside the screens' pack list
  is alphabetical by region-style codes: CN, EN, FR, GE, IT, JP, KR, SP, TW.
  This is consistent in all 3 sets. The tool does not rely on it.
- **Inferred: overlay.** Sets 1 and 2 hold MountPoint data and duplicate
  sprite names of set3 (`still_tutorial_133`, a different image). The
  MountPoint copy probably overrides the root one in later game versions.
  Both are extracted.

**Movies** (`movie_asset/ounce/MV01.webm`, `MV02.webm`): one copy for all
languages. Each has a single VP9 1920×1080 track with language `und` and no
audio or subtitle tracks. Frames sampled every 8 s show no text. They are not
localized.

## How the PT-BR mod injects images (verified)

The mod ships two files: `root.rdb` and `0x4B13D561.fdata`.

- **`0x4B13D561.fdata`.** The original 264,309-byte file comes first, byte
  for byte. 40 new `IDRK` entries are appended from offset 0x40880. They are
  stored raw: flags 0, payload = the whole G1T.
- **`root.rdb`.** Same size and entry count as the original. Exactly 40
  entries differ, all of them English textures of the 3 sets: 36 of set3's
  42, 2 of set1's 3 and both of set2's. The 7 English images it left
  alone are flagged `ptbr_translated: false` in the manifest. For each,
  only the location blob changes (fdata index 16, new offset and size) and
  the flags change from 0x420000 to 0x20000 (no compression). KTID, type and
  data size are unchanged.
- **Textures.** The new G1Ts keep the format, size and mip count of the
  originals. Their pixels differ only where the text is (median 0.8% of the
  pixels).

So texture replacement does not need rebuilding big archives: append a new
entry to a small fdata and repoint root.rdb.

## Writing images back: `assettool build` (implemented; in-game check pending)

Inputs: `assets/manifest.json`, `assets/es-419/*.png`,
`assets/reference/es-ES/*.png` and the game romfs.

- **What changes.** An es-419 PNG whose pixels differ from its es-ES
  reference is encoded into its es-ES texture: same format, size and mip
  count. A missing es-419 PNG means unchanged. 4×4 blocks whose pixels did not change keep their original bytes.
  Lower mips are box-filtered from the edited mip 0. The result is decoded
  again and compared (the build warns below 30 dB, premultiplied).
  `--reencode <name>` also re-encodes an unchanged image, for tests.
- **Encoders** (numpy): BC1, BC3 (BC1 colour + BC4-style alpha) and BC7
  **mode 6 only**. They minimise the premultiplied error, so the colour of
  transparent pixels is ignored.
- **These replace es-ES for everyone.** Español (España) uses the same
  textures, so every changed image except the label below replaces the
  Spain image too. The build prints a warning naming each one.
- **The 10th-button label.** `language_select__0030_prt_language_2` is
  special: its es-419 copy is the label of the 10th button, and the Spain
  label is never changed. When it differs from the reference, the build:
  1. pastes it at `(0, 1776)–(360, 1840)` of atlas `0xBC2C37F5` sub-texture
     32, after checking that the area plus a 4-pixel margin is transparent
     and touches no sprite (rows from 1772 down are empty);
  2. appends sprite `0030_prt_language_3` (hash `0xFC5B0505`) to sprite set
     `0x987344AA` (470 → 471 records; existing indices unchanged);
  3. sets row 0x102 of the label table to `{0xFC5B0505, 10, 0}`. Without a
     label it copies the Spain row there. The record goes into
     LINKDATA_PATCH as a raw always-on entry (`ldtool`
     `BuildConfig.extra_records`), and the main patch gives the 10th button
     ID 0x102.
- **Output** (like PT-BR): a copy of `0x4B13D561.fdata` (root mount, rdx
  index 16) with each changed resource appended as a raw entry (flags 0,
  16-byte aligned, sorted by KTID, parameter block copied with a new CRC-32),
  and a `root.rdb` whose entries for them point there (flags 0x420000 →
  0x20000, new data size). Both go to
  `<mod>/romfs/motor_asset_re/KIDS_PLATFORM_OUNCE/`; the build removes
  anything else in that folder, and everything when no image changed.
  Same inputs, same bytes.
- **Cost.** The atlas is stored raw: the label adds about 19 MB to the fdata.

## Activating Latam-only images later (UNVERIFIED, not implemented)

Untested ideas, for a future build step.

- **Same as PT-BR (simple, no coexistence).** Repoint the es-ES texture
  entries (the `es-ES.ktid` of each manifest asset) to re-encoded es-419 G1Ts
  appended to a small fdata. This shows the Latam images for Español
  (España) too. This is what `assettool build` does now (above).
- **Swap at runtime (coexistence).** The mod keeps Spanish (5) as the game's
  language and swaps text records when Latam is active. The module could do
  the same for images:
  - when the Latam flag changes, rewrite the in-memory root.rdb location (or
    the es pack's binding texture KTIDs) of these 49 entries. The new
    targets would be es-419 entries stored under new KTIDs in an extra fdata;
  - then force the UI textures to reload.

  Nobody has located the engine's in-memory resource table or its texture
  cache yet. Textures that are already resident may need a screen reload.
- **The 10th label** and **re-encoding** are done by `assettool build`
  (above).
