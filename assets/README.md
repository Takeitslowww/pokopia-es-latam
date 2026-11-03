# Image assets for es-419

The game has 49 images that contain Spain-Spanish (es-ES) text, listed in
`manifest.json`. `scripts/build_all.py` builds the edited ones back into the
game (see `docs/README.md`, *Assets*).

```
es-419/                 <- YOUR EDITS: only the images changed for Latin America
reference/es-ES/        pristine Spain-Spanish originals (generated, do not edit)
reference/en/           English version of the same image, for context (48 of 49; generated)
manifest.json           what each image is, where it comes from, sprite rectangles
```

`reference/` holds game files, so it is not in the repo. `build_all.py`
extracts it from the romfs when any image is missing. To do it by hand, from
`tools/`: `python -m assettool extract --reference-only`.

## Editing an image

1. Find it in `reference/es-ES/` (the table below and `manifest.json` say
   what each one is; `reference/en/` has the English version).
2. Copy it to `es-419/` with the same name.
3. Edit the copy, following the rules below.
4. Run `python scripts/build_all.py`.

An image without a copy in `es-419/` stays as it is.

| category | count | what |
|---|---|---|
| `tutorial` | 39 | screenshots in the help pages |
| `title` | 4 | game logo with the expansion subtitle, title screen, expansion artwork |
| `banner` | 3 | big pop-up banners (new creation, requests, access opened) |
| `minigame` | 1 | mini-game banners (one atlas) |
| `hud` | 1 | "MAX" counter labels |
| `language_select` | 1 | the "Español (España)" button label |

## Rules

* **Edit only files in `es-419/`.** Keep the file names. The `reference/`
  folders are overwritten on every export.
* **Keep the exact size** (width × height) of every PNG.
* **Keep transparency.** Alpha matters: transparent areas stay transparent
  and soft edges should stay soft. Work in RGBA, without a background layer.
* **Atlases** (names ending in `_atlas`, and the banners) hold several
  sprites. `manifest.json` → `es-ES.sprites` gives each sprite's rectangle
  `[x0, y0, x1, y1]`. Keep each piece of text inside its rectangle. The game
  shows only those areas.
* **Tutorial screenshots** (1024×512) use only the top-left 844×472 area,
  their sprite rectangle. The transparent margin to the right and below is
  not shown.
* **Text style.** Match the font, size and outline of the original. The images
  are re-compressed (BC3/BC7) when built, so very thin details may blur a
  little.
* **Copy only the images you change.** The build compares each `es-419/`
  file with `reference/es-ES/` and packs only the ones that differ; a copy
  left unchanged is ignored.
* **Edited images replace es-ES for everyone (for now).** Apart from the
  language label below, a changed image replaces the es-ES texture, which
  Español (España) uses too: the game has no Latam-only image swap yet. The
  build prints a warning naming each such image.

## Special cases

* `language_select__0030_prt_language_2.png` (360×64, RGBA): **the label of
  the 10th (es-419) language button.** The es-ES reference is the
  "Español (España)" label; the copy in `es-419/` drives only the 10th
  button. The build adds it to the game as a new sprite
  (`0030_prt_language_3`), so the Spain button keeps its own label and no
  global-replacement warning is printed for it. If the file is missing or
  equals the reference, the 10th button shows the Spain label. The game tints labels
  with the button colour (dark grey, or the highlight colour when selected),
  so white text on transparent looks like the other labels; other colours are
  tinted too.
* `tutorial__still_tutorial_133.png` and `tutorial__still_tutorial_133.mp1.png`
  are two different versions of the same screenshot (base data and update
  data). Edit both.
* `title__8000_dlc_logo_atlas.t0/.t1` are the two halves of one texture.
  `.t1` has no English reference.

## Re-exporting

From `tools/`:

```
python -m assettool extract --reference-only   # reference/ only
python -m assettool extract                    # reference/, es-419/ and manifest.json
```

Both read `boot/romfs` unless `--romfs` says otherwise.

* `--reference-only` never touches `es-419/` and keeps `manifest.json`. It
  warns about (and exits with code 1 for) any image that differs from the
  manifest.
* A full extract writes an es-419 copy of every image that has none (the
  build ignores unchanged copies; delete them to keep `es-419/` to edited
  images). Files already in `es-419/` are never overwritten. `--force`
  resets them to the es-ES originals, so do not use it after you start
  editing.

## Building

`python scripts/build_all.py` runs `assettool build` first. To run it alone,
from `tools/`:

```
python -m assettool build --out <mod dir>/romfs
```

It writes `romfs/motor_asset_re/KIDS_PLATFORM_OUNCE/0x4b13d561.fdata` and
`root.rdb` only when an image changed (and removes them otherwise). Details:
`docs/ASSETS-FORMAT.md`, *Writing*.
