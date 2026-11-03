from __future__ import annotations

import hashlib
import io
import json
import os
from typing import Callable

from PIL import Image

from . import __version__
from . import gamedata as gd
from .catalog import Asset, Catalog, TextureRef, build_catalog
from .g1t import decode_texture, gob_block_height
from .rdb import ResourceDb, parse_rdb

WORK_DIR = "es-419"
REF_ES_DIR = os.path.join("reference", "es-ES")
REF_EN_DIR = os.path.join("reference", "en")
MANIFEST = "manifest.json"
MANIFEST_SCHEMA = 1


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def write_if_changed(path: str, data: bytes) -> bool:
    if os.path.isfile(path):
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def ptbr_repointed(ptbr_romfs: str, db: ResourceDb) -> set[int]:
    with open(os.path.join(ptbr_romfs, gd.ASSET_DIR, "root.rdb"), "rb") as f:
        mod = parse_rdb(f.read())
    return {b.ktid for a, b in zip(db.entries, mod) if a.ktid == b.ktid and a.location != b.location}


def _image(cat: Catalog, ref: TextureRef) -> Image.Image:
    g = cat.g1t[ref.ktid]
    img = decode_texture(cat.data[ref.ktid], g.textures[ref.sub])
    return img.crop(ref.crop) if ref.crop else img


def _hex(v: int | None) -> str | None:
    return None if v is None else "0x%08x" % v


def _texture_info(db: ResourceDb, cat: Catalog, ref: TextureRef) -> dict:
    entry = db.by_ktid[ref.ktid]
    loc = entry.location
    fref = db.fdata_ref(entry)
    g = cat.g1t[ref.ktid]
    t = g.textures[ref.sub]
    info = {
        "ktid": _hex(ref.ktid),
        "rdb_index": entry.index,
        "rdb_type": _hex(entry.type_ktid),
        "fdata": fref.relpath if fref else None,
        "fdata_offset": loc.offset if loc else None,
        "stored_size": loc.stored_size if loc else None,
        "size": entry.size,
        "g1t": {
            "version": g.version,
            "platform": g.platform,
            "sub_textures": len(g.textures),
            "sub_texture": ref.sub,
            "format": "0x%02x" % t.format,
            "format_name": t.format_name,
            "width": t.width,
            "height": t.height,
            "mips": t.mips,
            "flags": t.flags.hex(),
            "ext_header": t.ext_header.hex() or None,
            "swizzle": "switch2-block-linear",
            "gob_block_height": gob_block_height((t.height + 3) // 4),
        },
        "sprite_set": _hex(ref.sprite_set),
        "sprites": [{"name": n, "rect": list(r)} for n, r in ref.sprites],
    }
    if ref.crop:
        info["crop"] = list(ref.crop)
    return info


def latam_label_info() -> dict:
    return {
        "role": "The es-419 file is the label of the 10th (es-419) language button. The build "
                "adds it as a new sprite; the Spain button keeps the es-ES original.",
        "sprite": gd.LATAM_LABEL_SPRITE,
        "sprite_hash": _hex(gd.kt_hash(gd.LATAM_LABEL_SPRITE)),
        "atlas": _hex(gd.LABEL_ATLAS),
        "sub_texture": gd.LABEL_ATLAS_SUB,
        "rect": list(gd.LATAM_LABEL_RECT),
        "sprite_set": _hex(gd.LABEL_SPRITE_SET),
        "label_table": {"linkdata_record": gd.LABEL_TABLE_RECORD, "row": "0x%x" % gd.LABEL_ROW_LATAM,
                        "ui_asset": gd.LABEL_UI_ASSET},
    }


def _asset_entry(db: ResourceDb, cat: Catalog, a: Asset, files: dict, img: Image.Image,
                 es_png: bytes, ptbr: set[int] | None) -> dict:
    es_info = _texture_info(db, cat, a.es)
    es_info["pack"] = _hex(a.es_pack)
    entry = {
        "name": a.name,
        "kind": a.kind,
        "category": a.category,
        "description": gd.describe(a.name, a.description),
        "files": files,
        "image": {"width": img.width, "height": img.height, "mode": img.mode,
                  "es_ES_sha256": hashlib.sha256(es_png).hexdigest()},
        "language_set": a.set_id,
        "es-ES": es_info,
        "ktid_fingerprint": None if a.fingerprint is None else {
            "lang": a.fingerprint.lang, "scale_exponent": a.fingerprint.exponent,
            "votes": a.fingerprint.votes},
        "en": None,
    }
    if a.en is not None:
        en_info = _texture_info(db, cat, a.en)
        en_info["pack"] = _hex(a.en_pack)
        en_info["match"] = a.match
        if ptbr is not None:
            en_info["ptbr_translated"] = a.en.ktid in ptbr
        entry["en"] = en_info
    if a.name == gd.LATAM_LABEL_ASSET:
        entry["latam_label"] = latam_label_info()
    return entry


def _movies(romfs: str) -> list[dict]:
    root = os.path.join(romfs, gd.MOVIE_DIR)
    out = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                out.append({"file": "/".join([*gd.MOVIE_DIR.split(os.sep), name]), "kind": "movie",
                            "bytes": os.path.getsize(path), "localized": False,
                            "note": gd.MOVIE_NOTE})
    return out


def missing_references(assets_dir: str) -> list[str]:
    with open(os.path.join(assets_dir, MANIFEST), encoding="utf-8") as f:
        manifest = json.load(f)
    missing = []
    for a in manifest["assets"]:
        for key in ("es-ES", "en"):
            rel = a["files"].get(key)
            if rel and not os.path.isfile(os.path.join(assets_dir, *rel.split("/"))):
                missing.append(rel)
    return missing


def _committed_hashes(path: str) -> dict[str, str] | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return {a["name"]: a["image"]["es_ES_sha256"] for a in json.load(f)["assets"]}


def _update_working_copy(work: str, es_png: bytes, force: bool, counts: dict) -> None:
    if force or not os.path.isfile(work):
        counts["es419_written"] += write_if_changed(work, es_png)
        return
    counts["es419_kept"] += 1
    with open(work, "rb") as f:
        if f.read() != es_png:
            counts["es419_edited"] += 1


def extract(romfs: str, out_dir: str, force: bool = False, ptbr_romfs: str | None = None,
            log: Callable[[str], None] = print, working_copies: bool = True) -> dict:
    base = os.path.join(romfs, gd.ASSET_DIR)
    counts = {"assets": 0, "es419_written": 0, "es419_kept": 0, "es419_edited": 0,
              "reference_written": 0, "reference_mismatch": 0, "manifest_written": 0}
    man_path = os.path.join(out_dir, MANIFEST)
    committed = None if working_copies else _committed_hashes(man_path)
    with ResourceDb(base) as db:
        cat = build_catalog(db, log)
        ptbr = ptbr_repointed(ptbr_romfs, db) if ptbr_romfs else None
        entries = []
        for a in cat.assets:
            img = _image(cat, a.es)
            es_png = png_bytes(img)
            rel = a.name + ".png"
            files = {"es-419": "es-419/" + rel, "es-ES": "reference/es-ES/" + rel, "en": None}
            counts["reference_written"] += write_if_changed(os.path.join(out_dir, REF_ES_DIR, rel), es_png)
            if a.en is not None:
                en_img = _image(cat, a.en)
                counts["reference_written"] += write_if_changed(
                    os.path.join(out_dir, REF_EN_DIR, rel), png_bytes(en_img))
                files["en"] = "reference/en/" + rel
            if working_copies:
                _update_working_copy(os.path.join(out_dir, WORK_DIR, rel), es_png, force, counts)
            elif committed is not None and committed.get(a.name) != hashlib.sha256(es_png).hexdigest():
                counts["reference_mismatch"] += 1
                log("warning: %s differs from the image in %s" % (rel, MANIFEST))
            entries.append(_asset_entry(db, cat, a, files, img, es_png, ptbr))
            counts["assets"] += 1
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generator": "assettool %s" % __version__,
            "game": gd.GAME_INFO,
            "source": {"asset_dir": gd.ASSET_DIR.replace(os.sep, "/"), "rdb_entries": len(db.entries)},
            "source_language": gd.SOURCE_LANGUAGE,
            "target_language": "es-419",
            "languages": list(gd.LANGUAGES),
            "language_sets": [{"id": s.id, "scale_exponent": s.exponent,
                               "packs": {l: _hex(s.packs[l]) for l in gd.LANGUAGES if l in s.packs}}
                              for s in cat.language_sets],
            "stats": dict(sorted(cat.stats.items())),
            "assets": entries,
            "movies_checked": _movies(romfs),
            "shared_with_english": cat.shared,
            "notes": cat.notes,
        }
        text = json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=False) + "\n"
        if committed is None:
            counts["manifest_written"] = int(write_if_changed(man_path, text.encode("utf-8")))
    if working_copies:
        log("%(assets)d assets; es-419: %(es419_written)d written, %(es419_kept)d kept "
            "(%(es419_edited)d edited); %(reference_written)d reference files updated" % counts)
    else:
        log("%(assets)d assets; %(reference_written)d reference files updated; es-419 untouched"
            % counts)
    return counts
