# ldtool: text pipeline for the Pokopia es-419 mod

`ldtool` turns the Spain-Spanish text of Pokémon Pokopia (v2.0.1) into gettext
`.po` files for translators, then builds the translated text into
`LINKDATA_PATCH.IDX` + `LINKDATA_PATCH.BIN`, the data file of the es-419
module (see `docs/README.md`). It can also write a standalone archive in the
game's own LINKDATA format (`--layout archive`).

It needs Python 3.10+ and the standard library only.

```
tools/
  ldtool/            Python package (python -m ldtool ...)
    container.py     IDX/BIN archive and chunked-zlib containers
    table.py         text-table record parse / serialize
    escape.py        reversible text <-> .po escaping
    po.py            .po reader / writer
    gamedata.py      record numbers, language slots
    mapping.py       output index mapping
    pipeline.py      extract / build / verify / roundtrip
    cli.py           command line
text/es-419/         one .po per Spanish table (table_<record>.po): Latin-American text
                     gender.json: item-name gender changes (see below)
text/always-on/      *.json: row changes applied in every language (see below)
tests/               python tests/run_tests.py
```

## Workflow

Run every command from `tools/`:

```
cd tools
python -m ldtool extract     # game -> text/es-419/*.po
#   ... translate: fill msgstr in the .po files (Poedit or any text editor) ...
python -m ldtool build       # .po -> LINKDATA_PATCH.IDX / LINKDATA_PATCH.BIN
python -m ldtool verify      # re-read the built pair and check it against the .po files
```

`scripts/build_all.py` runs `build` and `verify`, builds the module, and
installs everything.

The source romfs defaults to `boot/romfs` in the mod folder (a junction to
your v2.0.1 romfs; see `docs/README.md`, *Build*). Change it with `--romfs`
or the `LDTOOL_ROMFS` environment variable (`assettool` also reads
`ASSETTOOL_ROMFS` first). The tools only read it.

### extract

Writes `text/es-419/table_<record>.po` for each of the 368 Spanish-slot records
and for record 1474 (species names, Spanish column only). That is 369 files and
41,014 entries.

* `msgctxt` is `"<record>:<row>"`, or `"<record>:<row>:<col>"` for tables with
  more than one string per row. `col` counts string columns from 0. Item names
  have six forms per row, and record 1474 has one column per language
  (Spanish is column 5).
* `msgid` is the original Spain-Spanish text.
* `#. en: ...` is the English text of the same row, for reference.
* `msgstr` starts empty. An empty `msgstr` keeps the original text, so only
  fill it where the Latin-American text differs.
* Empty rows are skipped.

Running `extract` again merges with the existing files. Translations are kept.
If the game text of a translated entry changed, the entry is marked `fuzzy` and
the old text is kept as `#| msgid`. Translations whose row no longer exists are
kept as obsolete `#~` entries. `--force` overwrites the files instead of
merging. Use `--record N` (repeatable) to extract only some records.

### Translating

* Keep every `<...>` tag exactly as it is, including its arguments. Examples:
  `<br>` is a line break. `<clear>` starts a new text box page. `<slide>`,
  `<oneline>` and `<wait(0.8)>` control text flow. `<$cdef(27)>…</color>`
  highlights text. `<pname;0>` and `<$ifull;0(lnum1)>` are names and other
  values the game fills in. `<$voicel(1,7)>`, `<$motion(…)>` and
  `<$emotion(…)>` drive voice and animation.
* Grammar tags pick a word ending from the value they refer to:
  `<gen;0(o,a)>` by gender, `<qty;0(é,én)>` by number, `<genqty;0(o,a,os,as)>`
  by both. Keep their arguments in the same order.
* Use `<br>` for line breaks. Never use a real newline.
* Fuzzy entries are ignored by `build` unless you pass `--include-fuzzy`.
* `python -m ldtool po-import file.json` sets many `msgstr` values at once
  from a JSON object `{"<msgctxt>": "<msgstr>", ...}`.

`build` warns about tags that are missing or extra compared with `msgid`. Layout
tags (`<br> <clear> <slide> <oneline>`) are not checked. It also warns about raw
newlines, unbalanced `<` or `>`, and `msgid`s that no longer match the game
(stale files). `--strict` turns these warnings into errors.

### Item-name gender (`text/es-419/gender.json`)

The game picks articles and endings for an item name from its gender, stored
in bits `0x00100010` of the first attribute word of the row (set = feminine,
clear = masculine). When the es-419 name changes gender (nevera →
refrigerador), list the row in this optional file:

```json
{
  "about": "free text, ignored",
  "rows": {"4650:583": "m"}
}
```

Keys are `<record>:<row>` of a record with the item-name layout
(`32:ssssssii`: 4650 and 4164). `f` sets the bits and `m` clears them; every
other bit is kept. Only the Latin-American records change, never Español
(España) or the always-on entries. A record with a gender change is written
even if none of its text changes. A bad key or value, another layout, an
unknown row or a record without a Latin-American copy stops `build`. Listing
a row that already has that gender is a warning.

### Model-assisted review (`scripts/latam_assist.py`)

A local Ollama model (default `translategemma:12b`) proposes es-419 versions of
untranslated entries, and a person accepts, edits or rejects each one. Run it
from the mod root:

```
python scripts/latam_assist.py suggest --spain-only      # batch proposals, resumable
python scripts/latam_assist.py review --spain-only       # a=accept k=keep e=edit r=regenerate s=skip q=quit
python scripts/latam_assist.py stats                     # progress
python scripts/latam_assist.py unload                    # free the GPU
```

* The model never sees tags. `scripts/latam_text.py` replaces them with `{n}`
  placeholders, maps them back, re-wraps changed lines to the original width,
  and runs the `build` tag check plus stricter checks. A proposal that fails is
  shown in red and is never saved without an explicit `yes`.
* `--spain-only` takes only entries whose `msgid` contains a Spain-only word or
  form (vosotros, `-áis`, coger, vale, guay, tío, ...). The same list warns
  about Spain-isms left in a proposal.
* Proposals go to `text/review/suggestions.jsonl` and decisions to
  `text/review/decisions.jsonl`. Both are append-only, so quitting or a crash
  loses nothing. Accepted text goes straight into the `.po` `msgstr`.
  `apply` writes the saved decisions again (for example after `extract`).
* `--workers N` (default 4 for `suggest`) sends N requests at once. Ollama runs
  them in parallel only if the server was started with
  `OLLAMA_NUM_PARALLEL` set to N or more (`setx OLLAMA_NUM_PARALLEL 4`, then
  restart the Ollama app). Otherwise the requests wait in line.
* `--po-dir` and `--review-dir` point at other folders (for tests on copies).

### Escape format

Game text is UTF-8. Tags are ordinary printable text, so they appear unchanged
in the `.po` files. The only characters that are escaped are the invisible or
ambiguous ones, written as `<U+XXXX>` (4 to 6 hex digits, upper-case `U`):

* Unicode categories Cc (controls such as TAB and LF), Cf (format characters:
  soft hyphen, zero-width characters, BOM), Co (private use: the game's icon
  glyphs, such as `<U+E01B>` in Korean text), Cs, Cn, Zl and Zp.
* A literal `<` that would otherwise start a `<U+XXXX>` sequence is written as
  `<U+003C>`.

The mapping is fully reversible: `from_display(to_display(s)) == s`. On top of
this, the usual `.po` string escapes apply (`\"`, `\\`, `\n`). The v2.0.1
Spanish text contains no character that needs escaping. NBSP and U+3000 stay
literal.

### build

For each Spanish-slot record, `build` takes the original record from the
romfs, replaces the cells that have a non-empty, non-fuzzy `msgstr`, applies
`gender.json`, and serializes the record.

#### Default: `--layout patch` (LINKDATA_PATCH)

| option | default | meaning |
|---|---|---|
| `--out-dir` | `%APPDATA%\eden\load\0400A3A01EE4C000\ES-LATAM\romfs` | output folder |
| `--idx-name` / `--bin-name` | `LINKDATA_PATCH.IDX` / `LINKDATA_PATCH.BIN` | file names |
| `--records` | `changed` | only records with translations (`spanish` = all 369) |
| `--always-on` | `text/always-on` | always-on overrides (`--no-always-on`: none) |
| `--serializer` | `canonical` | the game's own record layout |
| `--no-species` | off | leave record 1474 out |

The game reads `romfs/LINKDATA_PATCH.IDX/.BIN` on top of its own archive: entry
*i* replaces record *i*. The output always has the full 12,298 entries; unused
entries are empty. Every record is stored raw (uncompressed, flag 0). There
are two kinds of entries:

* **Latin-American records** (from the `.po` files). Their `size` field has
  `'LATM'` (0x4C41544D) in the upper 32 bits, and the real size in the lower 32
  bits. The patched game skips them; the module keeps them and swaps them in
  while es-419 is the language, so Español (España) keeps its text. An entry
  sits at its own record index. If that index already holds an always-on
  version of the same record, the Latam entry goes to the first free index of
  the unused range 8953–9540, with the target record in the upper 32 bits of
  `flag`, and it carries the always-on changes too.
* **Always-on overrides** (from `text/always-on/*.json`). These are plain
  entries that the game applies in every language. The mod uses them for row
  80 of records 2323–2331, the confirmation prompt of the es-419 button
  ("Are you sure you want to play in Spanish (Latin America)?" in the 9 UI
  languages).

Always-on files are JSON:

```json
{
  "about": "free text, ignored",
  "rows": {
    "2324:80": {"text": "Are you sure you want to play in Spanish (Latin America)?", "ints": [1]}
  }
}
```

Keys are `<record>:<row>[:<col>]`, as in the `.po` `msgctxt`, but any
localized record is allowed (all 9 language slots, and record 1474). `text`
uses the `.po` notation (`<U+XXXX>` escapes). `ints` replaces all integer
(attribute) fields of the row; row 80 needs `[1]` like the prompts in rows
74–79.

The `--map`, `--idx-entries` and `--fill` options must keep their defaults
with this layout.

#### `--layout archive`

Writes a standalone pair in the game's own format, with compressed records
(`LI_C0_R_LATAM.IDX` / `LD_C0_R_LATAM.BIN` by default). Earlier versions of
the mod used it; the module does not read it. Here `--records` defaults to
`spanish`, always-on files are not used, and these options apply:

| option | default | meaning |
|---|---|---|
| `--map` | `identity` | output index = source record id |
| `--idx-entries` | `full` | 12,298 entries, like the original |
| `--fill` | `empty` | every other entry is empty |

Index mapping (`--map`):

* `identity`: the output index is the source record id.
* `sequential:START`: the sorted source records are written to START,
  START+1, and so on. For example, `sequential:8953` targets the empty
  8953–9540 range; 369 records fit.
* `offset:DELTA`: the output index is the source record id + DELTA.
* `file:map.json`: an explicit `{"<source record>": <output index>}` object.

IDX size (`--idx-entries`): `full` (same as the source), `compact` (highest
used index + 1), or a number.

`--fill source` copies every other entry verbatim from the source archive.
This gives a complete replacement pair (a patch overlay of the original
`LI_C0_R.IDX`/`LD_C0_R.BIN`, about 247 MB). With no translations it rebuilds
the original files byte for byte.

`--serializer append` (both layouts) keeps each original record byte for
byte, appends the changed strings and repoints them. This is the layout the
PT-BR community mod uses. `--manifest out.json` writes the source-to-index
table and the sizes.

### verify

`verify` takes the same layout options as `build`, and they must match. It
re-reads the built pair and checks that:

* the IDX has the expected entry count;
* blobs are 256-aligned, do not overlap, and stay inside the BIN;
* every record decodes and parses, with its schema inferred independently;
* the header and schema match the original, and the attribute fields too
  (unless an always-on override sets them; Latam entries may differ only in the
  gender bits of the rows in `gender.json`);
* every string matches its always-on override, its `.po` `msgstr` (Latam
  entries only), or else the original text;
* patch layout: Latam entries carry the `'LATM'` marker and the right target,
  always-on entries carry neither, and all are stored raw;
* the record bytes equal a fresh serialization;
* every other entry is empty (or identical to the source with `--fill source`).

It exits with code 1 on failure.

### Other commands

* `python -m ldtool dump 2328 --rows 70-80` prints the rows of a record. Add
  `--idx/--bin` to read a built pair.
* `python -m ldtool roundtrip [--mod <romfs>]` checks that
  `serialize(parse(rec)) == rec` and that every container re-packs identically
  for all 3,313 localized records. With `--mod`, it also checks every record a
  mod changed.

## File formats (v2.0.1, verified)

### Archive

`LI_C0_R.IDX` is 12,298 entries of 32 bytes with no header:

```
u64 offset, u64 size (unpacked), u64 stored_size (0 = empty), u64 flag (1 = chunked container)
```

`LINKDATA_PATCH.IDX` uses the same entries, plus the `'LATM'` marker (upper
half of `size`) and the target record (upper half of `flag`) described under
*build*. The game itself only knows the lower halves.

`LD_C0_R.BIN` holds the blobs at 256-byte aligned offsets. Empty entries point
at the next write position, and 443 empty entries carry flag 1.

A chunked container has this layout:

```
u32 block_size (0x10000), u32 chunk_count, u32 total, u32 lengths[count]
pad to 128
chunks, each padded to 128:
  [u32 zlib_len][zlib level 9]     (lengths[i] == zlib_len + 4)
  or the raw block                 (lengths[i] == block length; used when zlib would not shrink it)
```

`stored_size` includes the padding of the last chunk. Re-packing with level 9
reproduces all 7,686 compressed containers of the game byte for byte.

### Localized text

Records 1909–5220 are 368 groups of 9 language slots:
`record = 1909 + 9*group + slot`. The slots are 0 ja, 1 en, 2 fr, 3 it, 4 de,
5 es-ES, 6 zh-Hant, 7 ko, 8 zh-Hans. All slots of a group share the same row
count and schema.

Record 1474 holds the species names, with 9 language columns in one record:
ja, en, fr, it, de, es, zh-Hans, zh-Hant, ko.

Every text record is a table with this layout:

```
0x00 u32 count        rows
0x04 u32 mode         2 or 0 (kept as is)
0x08 56 bytes         reserved, all zero
0x40 count rows of `stride` bytes, each a sequence of u32 fields:
     string field  = offset of a NUL-terminated UTF-8 string, relative to the field itself
     integer field = attribute (message-box flags 0/1/8/9/10 on dialogue; gender/article bits on item names)
string pool, zero padding to a multiple of 8
```

String fields come first in a row. These schemas occur (stride and fields):

* `4:s`
* `8:si` (most tables)
* `12:ssi` (place names)
* `32:ssssssii` (item names: 6 forms plus 2 attribute words)
* `40:sssssssssi` (record 1474)

The game writes the pool in row order, then field order. Each cell gets its own
string, with no de-duplication: even every empty string has its own NUL.
`ldtool` writes the pool the same way, and all 3,313 localized records
round-trip byte for byte. The game also accepts strings anywhere after the row
table, with no padding (`--serializer append`, the PT-BR mod's layout).

## Tests

```
python tests/run_tests.py        # from the mod root; add -v for details
```

Tests that need the game data are skipped when the romfs (`boot/romfs` or
`LDTOOL_ROMFS`) or the PT-BR mod (`LDTOOL_PTBR_ROMFS`) cannot be found.
