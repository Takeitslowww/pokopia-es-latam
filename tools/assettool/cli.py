from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from . import gamedata as gd
from .bcn import BcnError
from .build import BuildError, build
from .catalog import CatalogError, build_catalog
from .extract import extract
from .g1t import G1tError
from .rdb import RdbError, ResourceDb
from .sheet import contact_sheets

DEFAULT_OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets"))


def cmd_extract(a: argparse.Namespace) -> int:
    counts = extract(a.romfs, a.out, force=a.force, ptbr_romfs=a.ptbr_romfs,
                     working_copies=not a.reference_only)
    return 1 if counts["reference_mismatch"] else 0


def cmd_build(a: argparse.Namespace) -> int:
    build(a.romfs, a.assets, a.out, reencode=tuple(a.reencode or ()))
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    with ResourceDb(os.path.join(a.romfs, gd.ASSET_DIR)) as db:
        cat = build_catalog(db, print)
        for s in cat.language_sets:
            print("%s (31^%d): %s" % (s.id, s.exponent, ", ".join(
                "%s=0x%08x" % (l, s.packs[l]) for l in gd.LANGUAGES if l in s.packs)))
        for x in cat.assets:
            en = "0x%08x/%d" % (x.en.ktid, x.en.sub) if x.en else "-"
            print("%-48s %-7s es 0x%08x/%d  en %-14s %s" % (x.name, x.kind, x.es.ktid, x.es.sub, en,
                                                           x.match or ""))
        for n in cat.notes:
            print("note:", n)
    return 0


def cmd_sheet(a: argparse.Namespace) -> int:
    for path in contact_sheets(a.assets, a.out, subdir=a.subdir, thumb=a.thumb, cols=a.cols,
                               per_sheet=a.per_sheet):
        print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="assettool", description="Pokopia image assets: extract and build")
    p.add_argument("--version", action="version", version="assettool " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="export es-ES localized images as PNG + manifest.json")
    e.add_argument("--romfs", default=gd.DEFAULT_ROMFS, help="game romfs (v2.0.1)")
    e.add_argument("--out", default=DEFAULT_OUT, help="assets folder (default: the mod's assets/)")
    mode = e.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="overwrite existing es-419 working files")
    mode.add_argument("--reference-only", action="store_true",
                      help="write only reference/ (never es-419/; an existing manifest.json is kept)")
    e.add_argument("--ptbr-romfs", help="PT-BR mod romfs: mark English images it translated")
    e.set_defaults(func=cmd_extract)

    b = sub.add_parser("build", help="encode changed es-419 images into <out>/motor_asset_re "
                                     "(new fdata + patched root.rdb)")
    b.add_argument("--romfs", default=gd.DEFAULT_ROMFS, help="game romfs (v2.0.1)")
    b.add_argument("--assets", default=DEFAULT_OUT, help="assets folder (default: the mod's assets/)")
    b.add_argument("--out", required=True, help="mod romfs folder, e.g. .../ES-LATAM/romfs")
    b.add_argument("--reencode", action="append", metavar="NAME",
                   help="also re-encode this asset's es-ES image even if unchanged (test); "
                        "repeatable")
    b.set_defaults(func=cmd_build)

    ls = sub.add_parser("list", help="print the language sets and assets, write nothing")
    ls.add_argument("--romfs", default=gd.DEFAULT_ROMFS)
    ls.set_defaults(func=cmd_list)

    sh = sub.add_parser("sheet", help="contact sheet(s) of an assets folder")
    sh.add_argument("--assets", default=DEFAULT_OUT)
    sh.add_argument("--out", required=True, help="output PNG (more sheets get _2, _3, ...)")
    sh.add_argument("--subdir", default="es-419", help="es-419 | reference/es-ES | reference/en")
    sh.add_argument("--thumb", type=int, default=300)
    sh.add_argument("--cols", type=int, default=5)
    sh.add_argument("--per-sheet", type=int, default=30)
    sh.set_defaults(func=cmd_sheet)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except (RdbError, G1tError, CatalogError, BuildError, BcnError, OSError) as ex:
        print("error: %s" % ex, file=sys.stderr)
        return 1
