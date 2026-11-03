# Pokémon Pokopia: Español (Latinoamérica)

A personal mod that adds a 10th language, Spanish for Latin America
(es-419), to Pokémon Pokopia v2.0.1 in the Eden / Eglantine emulator. It is
built for your own legal dump.

* On the first boot, the language screen gets a 10th button (bottom right).
  Choosing it asks "Are you sure you want to play in Spanish (Latin
  America)?" and then plays in Spanish with the Latin-American text from
  `text/es-419/*.po`.
* Español (España) and the other languages are unchanged.
* The choice is saved as language 9 in the system save (`S00/SYDAT.BIN`).
  At boot, the module turns the Latin-American text on again.
* Until you finish the intro (the first save of the game itself), the game
  shows the language screen again on every boot. That is normal; it does
  the same with Español (España).

Only the text in the `.po` files changes. Anything not translated there stays
Spain Spanish.

## What it is made of

| part | where | what it does |
|---|---|---|
| text data | `ES-LATAM\romfs\LINKDATA_PATCH.IDX` + `.BIN` | Latin-American records, marked `'LATM'`, plus the es-419 confirmation prompt and the 10th-button label row (always on) |
| images | `ES-LATAM\romfs\motor_asset_re\KIDS_PLATFORM_OUNCE\0x4b13d561.fdata` + `root.rdb` | edited images from `assets/es-419/`, including the 10th-button label; only present when an image changed |
| main patch | `ES-LATAM\exefs\76A489F3BD3642FA5E18BD83D32C98BACC5F7D0E.ips` | 13 small edits to `main` (IPS32): the 10th button, language 9 in the save, and branches into the module |
| module | `boot\exefs\subsdk9` (release zip: `exefs\subsdk9`, next to the player's `main`) | code that keeps the Latam records and swaps them in and out of the game's text table when the language changes |

`ES-LATAM` is `%APPDATA%\eden\load\0400A3A01EE4C000\ES-LATAM`. `boot\` is
the contributor's copy of the game (see *Build*).

How it works: the game knows 9 languages; Spanish is 5. The module only
uses 9 on the language screen and in the save. Everywhere else the game runs
as Spanish (5), so nothing else in the game sees an unknown language. When
9 is chosen (or loaded at boot), the module switches the Spanish text records
to the Latin-American ones, and the game reloads its text through its own
language-change callback. Details: `docs/RE-FINDINGS.md`, `module/patches.py`
and `module/src/latam.c`.

## Build

Requirements: Python 3.10+ with numpy and Pillow, and LLVM (`clang`,
`ld.lld`, `llvm-nm`) in `C:\Program Files\LLVM\bin`, on `PATH`, or in
`LLVM_BIN`.

The build reads your own v2.0.1 game from `boot\` (not in the repo). Set it
up once, from the mod folder:

* `boot\exefs\`: copy your game's exefs files (`main`, `main.npdm`, `rtld`,
  `sdk`, `subsdk0`). The build checks every patched byte against `main` and
  installs `subsdk9` here.
* `boot\romfs`: a junction to your v2.0.1 romfs:
  `mklink /J boot\romfs <your romfs>`.

The tools read `boot\romfs` by default (`--romfs`, `LDTOOL_ROMFS` or
`ASSETTOOL_ROMFS` point elsewhere). Then close the emulator and run:

```
python scripts/build_all.py
```

This runs `assettool build` (images), `ldtool build` + `verify`, builds the
module (release), and installs the parts listed above. If the reference
images (`assets/reference/`) are missing, it first extracts them from the
romfs.

| option | what it does |
|---|---|
| `--no-install` | builds and checks only |
| `--package` | release build into `dist\pokopia-es-latam.zip`; installs nothing, so the emulator may stay open |
| `--debug --debug-script N` | builds the test module described under *Testing* |

The zip is the release:

```
0400A3A01EE4C000\ES-LATAM\exefs\<BUILD_ID>.ips
0400A3A01EE4C000\ES-LATAM\romfs\LINKDATA_PATCH.IDX, LINKDATA_PATCH.BIN
0400A3A01EE4C000\ES-LATAM\romfs\motor_asset_re\...   only when an image changed
exefs\subsdk9
LEEME.txt                                             player guide (Spanish), from docs\LEEME.txt
```

`subsdk9` is outside the mod folder because Eglantine does not load it from
there: LayeredExeFS is not applied when the game is opened from a loose
`exefs\main`. Players copy it next to their game's `main`.

## Launch

Always launch from `mods\pokopia-es-latam\boot\exefs\main` (Eden: *Load
file*, or `eden-cli -g <path>\boot\exefs\main`) and keep the ES-LATAM mod
enabled. Only this folder has `subsdk9`. Players of the release zip launch
their own `exefs\main` instead, with `subsdk9` copied next to it (see
`docs/LEEME.txt`).

* Launching the installed game from the game list does not load `subsdk9`.
  The `main` patch then branches into nothing, so expect a crash (not
  tested). Disable ES-LATAM before launching from the game list.
* Launching from `boot\` with ES-LATAM disabled is safe: `subsdk9` is
  loaded but never called, and the game runs unmodified.

## Translating

1. Edit `text/es-419/table_<record>.po` (Poedit or any text editor). Fill
   `msgstr` only where the Latin-American text differs. Keep every `<...>`
   tag.
2. Close the emulator and run `python scripts/build_all.py`.

Details are in `tools/README.md`. `text/always-on/*.json` holds the few rows
that must change in every language (the es-419 prompt on the language
screen).
`text/es-419/gender.json` sets the gender of item names whose es-419 name
changes gender (nevera → refrigerador).

## Assets

Some text is drawn into images: tutorial screenshots, banners, the title
logo, and the "Español (España)" language label. `tools/assettool` exports
the 49 es-ES images that have Spanish text as PNGs, with the English version
of each and a manifest. `assettool build` (run by `build_all.py`) encodes the
edited PNGs back into the game.

* `assets/es-419/` holds only the images that were edited. To edit another
  one, copy it from `assets/reference/es-ES/` to `assets/es-419/` (same
  name) and edit the copy. An image without a copy stays unchanged.
* `assets/reference/es-ES/` and `assets/reference/en/` hold the untouched
  originals. They are game files, so they are not in the repo:
  `build_all.py` extracts them from the romfs when any is missing.
* `assets/manifest.json` lists each image's source: KTID, fdata file,
  G1T format, sprite rectangles and English counterpart.

```
cd tools
python -m assettool extract --reference-only     # reference/ only
python -m assettool extract [--force]            # reference/, es-419/ and manifest.json
python -m assettool list
python -m assettool sheet --out <sheet.png>
python -m assettool build --out <mod dir>/romfs [--reencode <name>]
```

* `--reference-only` never touches `assets/es-419/` and keeps the committed
  `manifest.json`; it checks each image against the manifest instead.
* A full `extract` re-exports everything, including an es-419 copy of every
  image that has none. Existing copies are kept unless `--force`.
* `assets/README.md` has the translator rules.
* `build` re-encodes every changed image in its original format, size and
  mip count. The es-419 copy of the Spain label is the 10th button's own
  label (a new sprite); every other changed image **replaces the es-ES image
  for Español (España) too**, and the build warns about each one. Without
  changes it installs no image files and removes old ones.
* `docs/ASSETS-FORMAT.md` covers the formats, how languages are organized,
  how the build writes images back, how the PT-BR mod swaps images, and an
  unverified plan for showing Latam-only images.

Needs numpy and Pillow (the text tools need only the standard library).

## Starting over

The language screen only appears while the game has no save (or the intro
is not finished yet).

```
python scripts/reset_save.py
```

This deletes the Pokopia save in Eden, including the game progress, after
asking. It keeps the empty save folder.

## Testing

The debug module (`--debug`) writes `[es-latam]` lines to the Eden log
(`%APPDATA%\eden\log\eden_log.txt`). The emulator's own input does not
reach this game, so the debug module can also press buttons itself, through
the game's controller reads. `--debug-script 1` picks es-419 and answers
Yes; `2` does the same for Español (España); `3` picks es-419 and keeps
tapping A, Left and Plus to play through the intro. Use a release build
(`build_all.py` without `--debug`) for playing.

## Limitations

* **Label.** The labels on this screen are images, one per language, and
  the game has no image for es-419. The build adds
  `assets/es-419/language_select__0030_prt_language_2.png` to the UI atlas
  as a new sprite and points the 10th button at it (label table row 0x102).
  While that file is missing or equals the es-ES original, the 10th button
  shows the "Español (España)" label. The game tints labels with the button
  colour. Not yet checked in the emulator.
* **Images are shared with Spain.** Edited images other than the label
  replace the es-ES images for Español (España) as well; there is no
  Latam-only image swap yet.
* **Language change in Options.** Only the first-boot screen and the boot
  load are handled. Changing the language later in the game's options is
  untested; there, es-419 is not offered.
* **One game build, one emulator.** The `main` patch and every address in the
  module are for update v2.0.1 (`main` build-id `76A489F3…`). The module
  load address (`main + 0x3E60000`) comes from how Eden lays out the modules.
  `module/build.py` checks every patched byte against `boot\exefs\main` and
  refuses to build for another version, but another emulator or console
  would need checking.
* **Data without the module.** `LINKDATA_PATCH` is only meant for the
  patched game: its Latin-American entries carry a marker that only the
  module understands.
