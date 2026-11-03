from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD_ROOT, "tools"))
sys.path.insert(0, os.path.join(MOD_ROOT, "module"))

import build as module_build
import patches
from assettool import build as asset_build
from assettool import extract as asset_extract
from assettool import gamedata as asset_gd
from assettool.catalog import CatalogError
from assettool.g1t import G1tError
from assettool.rdb import RdbError
from ldtool.pipeline import BuildConfig, PipelineError, build as ld_build, verify

TITLE_ID = "0400A3A01EE4C000"
MOD_NAME = "ES-LATAM"
DEFAULT_MOD_DIR = os.path.join(os.environ.get("APPDATA", ""), "eden", "load", TITLE_ID, MOD_NAME)
BOOT_EXEFS = os.path.join(MOD_ROOT, "boot", "exefs")
ASSETS_DIR = os.path.join(MOD_ROOT, "assets")
PACKAGE_ZIP = os.path.join(MOD_ROOT, "dist", "pokopia-es-latam.zip")
LEEME = os.path.join(MOD_ROOT, "docs", "LEEME.txt")
ASSET_ERRORS = (asset_build.BuildError, RdbError, G1tError, CatalogError, OSError, ValueError)
OBSOLETE = [os.path.join("romfs", "LI_C0_R_LATAM.IDX"), os.path.join("romfs", "LD_C0_R_LATAM.BIN")]


def emulator_running() -> bool:
    if os.name != "nt":
        return False
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return any(exe in out for exe in ("eden-cli.exe", "eden.exe", "eglantine.exe"))


def ensure_references(romfs: str, assets_dir: str) -> bool:
    missing = asset_extract.missing_references(assets_dir)
    if not missing:
        return True
    print(f"  {len(missing)} reference images missing; extracting them from {romfs}")
    warnings: list[str] = []
    counts = asset_extract.extract(romfs, assets_dir, working_copies=False,
                                   log=lambda m: warnings.append(m) if m.startswith("warning") else None)
    for w in warnings:
        print(f"  {w}")
    if counts["reference_mismatch"]:
        print("error: the romfs images differ from assets/manifest.json (another game version?)",
              file=sys.stderr)
        return False
    return True


def build_parts(romfs: str | None, romfs_out: str, debug: bool, script: int) -> dict | None:
    game_romfs = romfs or asset_gd.DEFAULT_ROMFS
    print("== assets (assettool)")
    try:
        if not ensure_references(game_romfs, ASSETS_DIR):
            return None
        assets = asset_build.build(game_romfs, ASSETS_DIR, romfs_out)
    except ASSET_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    cfg = BuildConfig(out_dir=romfs_out, extra_records=assets["linkdata_records"])
    if romfs:
        cfg.romfs = romfs
    print("== text (ldtool)")
    try:
        ld_build(cfg)
        if not verify(cfg):
            print("error: ldtool verify failed", file=sys.stderr)
            return None
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    print("== module")
    try:
        res = module_build.build(debug, script, module_build.DEFAULT_MAIN)
    except module_build.BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    return {"assets": assets, "text": cfg, "module": res}


def install(mod_dir: str, parts: dict) -> None:
    print("== install")
    res, cfg = parts["module"], parts["text"]
    exefs = os.path.join(mod_dir, "exefs")
    os.makedirs(exefs, exist_ok=True)
    ips_dst = os.path.join(exefs, f"{patches.BUILD_ID}.ips")
    shutil.copyfile(res["ips"], ips_dst)
    sub_dst = os.path.join(BOOT_EXEFS, "subsdk9")
    shutil.copyfile(res["subsdk9"], sub_dst)
    for rel in OBSOLETE:
        stale = os.path.join(mod_dir, rel)
        if os.path.exists(stale):
            os.remove(stale)
            print(f"  removed obsolete {stale}")
    print(f"  {ips_dst}")
    print(f"  {sub_dst} ({res['variant']})")
    print(f"  {cfg.idx_path}")
    print(f"  {cfg.bin_path}")
    for f in parts["assets"]["files"]:
        print(f"  {f['path']}")
    print(f"done. Launch the game from {os.path.join(BOOT_EXEFS, 'main')}")


def staging_romfs(staging: str) -> str:
    return os.path.join(staging, TITLE_ID, MOD_NAME, "romfs")


def write_package(staging: str, module: dict, zip_path: str) -> list[tuple[str, int]]:
    exefs = os.path.join(staging, TITLE_ID, MOD_NAME, "exefs")
    os.makedirs(exefs, exist_ok=True)
    shutil.copyfile(module["ips"], os.path.join(exefs, f"{patches.BUILD_ID}.ips"))
    os.makedirs(os.path.join(staging, "exefs"), exist_ok=True)
    shutil.copyfile(module["subsdk9"], os.path.join(staging, "exefs", "subsdk9"))
    shutil.copyfile(LEEME, os.path.join(staging, "LEEME.txt"))
    files = []
    for root, dirs, names in os.walk(staging):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(root, name)
            files.append((os.path.relpath(path, staging).replace(os.sep, "/"), path))
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    tmp = zip_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, path in files:
            zf.write(path, arc)
    os.replace(tmp, zip_path)
    return [(arc, os.path.getsize(path)) for arc, path in files]


def package(romfs: str | None, zip_path: str) -> int:
    with tempfile.TemporaryDirectory(prefix="pokopia-es-latam-") as staging:
        parts = build_parts(romfs, staging_romfs(staging), False, 0)
        if parts is None:
            return 1
        print("== package")
        entries = write_package(staging, parts["module"], zip_path)
    for arc, size in entries:
        print(f"  {size:>10}  {arc}")
    print(f"done: {zip_path} ({os.path.getsize(zip_path)} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build and install the whole mod.")
    ap.add_argument("--debug", action="store_true", help="debug module (Eden log + scripted input)")
    ap.add_argument("--debug-script", type=int, default=0,
                    help="debug input script (see module/build.py)")
    ap.add_argument("--mod-dir", default=DEFAULT_MOD_DIR, help="Eden mod folder (ES-LATAM)")
    ap.add_argument("--romfs", help="game romfs for ldtool and assettool (default: theirs)")
    ap.add_argument("--no-install", action="store_true",
                    help="build into module/build and a temporary text folder only")
    ap.add_argument("--package", action="store_true",
                    help="release build into dist/pokopia-es-latam.zip; installs nothing")
    a = ap.parse_args(argv)
    if a.package and a.debug:
        ap.error("--package always builds the release module; drop --debug")

    if a.package:
        return package(a.romfs, PACKAGE_ZIP)

    if not a.no_install and emulator_running():
        print("error: the emulator is running; close it first", file=sys.stderr)
        return 1

    romfs_out = os.path.join(a.mod_dir, "romfs") if not a.no_install else \
        os.path.join(MOD_ROOT, "module", "build", "romfs")
    parts = build_parts(a.romfs, romfs_out, a.debug, a.debug_script)
    if parts is None:
        return 1
    if a.no_install:
        print("done (not installed)")
        return 0
    install(a.mod_dir, parts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
