from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .container import DEFAULT_BLOCK_SIZE, DEFAULT_ENTRY_ALIGN, LinkData
from .escape import from_display, to_display
from .gamedata import DEFAULT_ROMFS
from .pipeline import (DEFAULT_ALWAYS_ON_DIR, DEFAULT_OUT_DIR, DEFAULT_PO_DIR, BuildConfig,
                       PipelineError, build, compare_mod, extract, parse_ctxt, po_filename,
                       roundtrip_records, verify)
from .po import read_po, write_po
from .table import parse_table


def _add_build_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--romfs", default=DEFAULT_ROMFS, help="source romfs with LI_C0_R.IDX/LD_C0_R.BIN")
    p.add_argument("--po-dir", default=DEFAULT_PO_DIR, help="directory with the .po files")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="output directory")
    p.add_argument("--layout", choices=("patch", "archive"), default="patch",
                   help="patch: LINKDATA_PATCH.IDX/.BIN for the es-419 module (default); "
                        "archive: standalone LI_C0_R_LATAM.IDX/LD_C0_R_LATAM.BIN")
    p.add_argument("--always-on", dest="always_on_dir", default=DEFAULT_ALWAYS_ON_DIR,
                   help="directory with always-on overrides (*.json; patch layout)")
    p.add_argument("--no-always-on", dest="always_on_dir", action="store_const", const=None,
                   help="leave the always-on overrides out")
    p.add_argument("--bin-name", help="output BIN name (default: by layout)")
    p.add_argument("--idx-name", help="output IDX name (default: by layout)")
    p.add_argument("--map", dest="map_spec", default="identity",
                   help="archive layout: identity | sequential:START | offset:DELTA | "
                        "file:map.json")
    p.add_argument("--idx-entries", default="full",
                   help="archive layout: full | compact | <N>")
    p.add_argument("--records", choices=("spanish", "changed"),
                   help="every Spanish-slot record, or only records with changes "
                        "(default: changed for patch, spanish for archive)")
    p.add_argument("--no-species", dest="include_species", action="store_false",
                   help="leave record 1474 (species names, all languages) out")
    p.add_argument("--fill", choices=("empty", "source"), default="empty",
                   help="archive layout, other entries: empty, or copied from the source")
    p.add_argument("--include-fuzzy", action="store_true", help="also apply fuzzy translations")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument("--serializer", choices=("canonical", "append"), default="canonical",
                   help="canonical = game layout; append = keep original bytes, append strings")
    p.add_argument("--entry-align", type=int, default=DEFAULT_ENTRY_ALIGN)
    p.add_argument("--block-size", type=lambda s: int(s, 0), default=DEFAULT_BLOCK_SIZE)
    p.add_argument("--manifest", help="write a JSON manifest of the output layout here")


def _config(a: argparse.Namespace) -> BuildConfig:
    return BuildConfig(romfs=a.romfs, po_dir=a.po_dir, out_dir=a.out_dir, layout=a.layout,
                       bin_name=a.bin_name, idx_name=a.idx_name, map_spec=a.map_spec,
                       idx_entries=a.idx_entries, records=a.records,
                       include_species=a.include_species, fill=a.fill,
                       include_fuzzy=a.include_fuzzy, strict=a.strict, serializer=a.serializer,
                       entry_align=a.entry_align, block_size=a.block_size,
                       always_on_dir=a.always_on_dir, manifest=getattr(a, "manifest", None))


def _parse_rows(spec: str | None, count: int) -> range:
    if not spec:
        return range(count)
    lo, _, hi = spec.partition("-")
    return range(int(lo), min(count, int(hi or lo) + 1))


def cmd_extract(a: argparse.Namespace) -> int:
    records = [int(r) for r in a.record] if a.record else None
    extract(a.romfs, a.out, include_species=a.include_species, force=a.force, records=records)
    return 0


def cmd_build(a: argparse.Namespace) -> int:
    build(_config(a))
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    return 0 if verify(_config(a)) else 1


def cmd_dump(a: argparse.Namespace) -> int:
    if a.idx:
        ld = LinkData(a.idx, a.bin)
    else:
        ld = LinkData.open_dir(a.romfs)
    with ld:
        data = ld.read(a.record)
        table = parse_table(data)
        print(f"record {a.record}: {len(data)} bytes, {table.count} rows, mode {table.mode}, "
              f"schema {table.schema}")
        for row in _parse_rows(a.rows, table.count):
            ints = " ".join(f"0x{v:x}" for v in table.ints(row))
            texts = " | ".join(to_display(t) for t in table.texts(row))
            if texts or a.all:
                print(f"{row:5d} [{ints}] {texts}")
    return 0


def cmd_roundtrip(a: argparse.Namespace) -> int:
    with LinkData.open_dir(a.romfs) as ld:
        res = roundtrip_records(ld)
        print(f"roundtrip: {res['table_ok']}/{res['checked']} records serialize(parse(rec)) == rec; "
              f"{res['container_ok']}/{res['checked']} containers re-pack identically")
        print("  per slot: " + ", ".join(f"{k}={v}" for k, v in sorted(res["by_slot"].items(),
                                                                       key=lambda kv: str(kv[0]))))
        for rec, why in res["failures"][:20]:
            print(f"  FAIL {rec}: {why}")
        ok = not res["failures"] and res["container_ok"] == res["checked"]
        if a.mod:
            with LinkData.open_dir(a.mod) as mod:
                m = compare_mod(ld, mod)
            print(f"mod {a.mod}: {m['changed']} changed records ({m['localized']} localized, "
                  f"slots {dict(m['slots'])}); append-mode rebuild exact for {m['exact']}, "
                  f"{m['stale']} carry stale strings, {m['reordered']} appended out of row order; "
                  f"{len(m['invalid'])} invalid")
            for rec, why in m["invalid"][:20]:
                print(f"  INVALID {rec}: {why}")
            ok = ok and not m["invalid"]
    return 0 if ok else 1


def cmd_po_import(a: argparse.Namespace) -> int:
    with open(a.json, "r", encoding="utf-8") as fh:
        wanted: dict[str, str] = json.load(fh)
    by_file: dict[str, dict[str, str]] = {}
    for ctxt, text in wanted.items():
        rec, _row, _col = parse_ctxt(ctxt)
        by_file.setdefault(os.path.join(a.po_dir, po_filename(rec)), {})[ctxt] = text
    missing = []
    for path, items in sorted(by_file.items()):
        po = read_po(path)
        index = {e.msgctxt: e for e in po.entries if not e.obsolete}
        for ctxt, text in items.items():
            e = index.get(ctxt)
            if e is None:
                missing.append(ctxt)
                continue
            from_display(text)
            e.msgstr = text
            if a.clear_fuzzy and "fuzzy" in e.flags:
                e.flags.remove("fuzzy")
        write_po(path, po)
    if missing:
        print(f"error: msgctxt not found: {missing}")
        return 1
    print(f"po-import: set {len(wanted)} entries in {len(by_file)} files")
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ldtool", description="Pokopia LINKDATA text toolkit")
    p.add_argument("--version", action="version", version=f"ldtool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("extract", help="write .po files from the Spanish-slot records")
    e.add_argument("--romfs", default=DEFAULT_ROMFS)
    e.add_argument("--out", default=DEFAULT_PO_DIR, help="output .po directory")
    e.add_argument("--record", action="append", help="only this record (repeatable)")
    e.add_argument("--no-species", dest="include_species", action="store_false")
    e.add_argument("--force", action="store_true", help="overwrite instead of merging translations")
    e.set_defaults(func=cmd_extract)

    b = sub.add_parser("build", help="apply .po files and write LINKDATA_PATCH.IDX/.BIN")
    _add_build_options(b)
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="check a built IDX/BIN pair against the .po files")
    _add_build_options(v)
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("dump", help="print the rows of one record")
    d.add_argument("record", type=int)
    d.add_argument("--rows", help="row range, e.g. 70-80")
    d.add_argument("--all", action="store_true", help="also print empty rows")
    d.add_argument("--romfs", default=DEFAULT_ROMFS)
    d.add_argument("--idx", help="read from this IDX instead of the romfs")
    d.add_argument("--bin", help="BIN for --idx")
    d.set_defaults(func=cmd_dump)

    r = sub.add_parser("roundtrip", help="self-check parser/serializer on all localized records")
    r.add_argument("--romfs", default=DEFAULT_ROMFS)
    r.add_argument("--mod", help="romfs of a mod (e.g. PT-BR) to compare against")
    r.set_defaults(func=cmd_roundtrip)

    i = sub.add_parser("po-import", help="set msgstr values from a JSON {msgctxt: msgstr} file")
    i.add_argument("json")
    i.add_argument("--po-dir", default=DEFAULT_PO_DIR)
    i.add_argument("--clear-fuzzy", action="store_true")
    i.set_defaults(func=cmd_po_import)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
