from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image

from . import gamedata as gd
from .extract import MANIFEST, write_if_changed
from .g1t import G1tError, decode_texture, encode_texture, parse_g1t
from .rdb import (ROOT_MOUNT, ResourceDb, patch_rdb_entry, raw_fdata_entry, rdb_entry_offsets,
                  stored_crc_ok)
from .sprites import append_sprite, parse_sprite_set, sprite_record

FDATA_ALIGN = 16
ROUNDTRIP_MIN_PSNR = 30.0
FREE_MARGIN = 4


class BuildError(Exception):
    pass


@dataclass
class SubEdit:
    image: np.ndarray
    force: np.ndarray
    sources: list[str] = field(default_factory=list)


@dataclass
class Plan:
    textures: dict[int, dict[int, SubEdit]] = field(default_factory=dict)
    global_replacements: list[str] = field(default_factory=list)
    reencoded: list[str] = field(default_factory=list)
    label: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)


def _load_rgba(path: str) -> np.ndarray:
    with Image.open(path) as im:
        if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
            raise BuildError("%s has no alpha channel; save it as RGBA" % path)
        return np.array(im.convert("RGBA"))


def _hex(v: int) -> str:
    return "0x%08x" % v


class _Game:
    def __init__(self, db: ResourceDb):
        self.db = db
        self.data: dict[int, bytes] = {}
        self.images: dict[tuple[int, int], np.ndarray] = {}

    def texture(self, ktid: int) -> bytes:
        if ktid not in self.data:
            entry = self.db.by_ktid.get(ktid)
            if entry is None or entry.type_ktid != gd.TYPE_TEXTURE:
                raise BuildError("%s is not a texture in root.rdb" % _hex(ktid))
            self.data[ktid] = self.db.read(entry)
        return self.data[ktid]

    def image(self, ktid: int, sub: int) -> np.ndarray:
        if (ktid, sub) not in self.images:
            g = parse_g1t(self.texture(ktid))
            self.images[ktid, sub] = np.array(decode_texture(self.texture(ktid), g.textures[sub]))
        return self.images[ktid, sub]


def _edit(plan: Plan, game: _Game, ktid: int, sub: int) -> SubEdit:
    subs = plan.textures.setdefault(ktid, {})
    if sub not in subs:
        img = game.image(ktid, sub).copy()
        subs[sub] = SubEdit(img, np.zeros(((img.shape[0] + 3) // 4, (img.shape[1] + 3) // 4), bool))
    return subs[sub]


def _paste(edit: SubEdit, image: np.ndarray, x0: int, y0: int, force: bool, source: str) -> None:
    h, w = image.shape[:2]
    if y0 + h > edit.image.shape[0] or x0 + w > edit.image.shape[1]:
        raise BuildError("%s does not fit in its texture at (%d, %d)" % (source, x0, y0))
    edit.image[y0:y0 + h, x0:x0 + w] = image
    if force:
        edit.force[y0 // 4:(y0 + h + 3) // 4, x0 // 4:(x0 + w + 3) // 4] = True
    edit.sources.append(source)


def make_plan(manifest: dict, assets_dir: str, game: _Game, reencode: tuple[str, ...] = ()) -> Plan:
    plan = Plan()
    names = {a["name"] for a in manifest["assets"]}
    unknown = sorted(set(reencode) - names)
    if unknown:
        raise BuildError("--reencode: unknown asset(s): %s" % ", ".join(unknown))
    for a in manifest["assets"]:
        name, es = a["name"], a["es-ES"]
        work = os.path.join(assets_dir, *a["files"]["es-419"].split("/"))
        ref = os.path.join(assets_dir, *a["files"]["es-ES"].split("/"))
        if not os.path.isfile(ref):
            raise BuildError("missing reference image %s (run assettool extract --reference-only)" % ref)
        ref_img = _load_rgba(ref)
        img = ref_img
        if os.path.isfile(work):
            img = _load_rgba(work)
            if img.shape != ref_img.shape:
                raise BuildError("%s is %dx%d, must stay %dx%d" % (
                    work, img.shape[1], img.shape[0], ref_img.shape[1], ref_img.shape[0]))
        changed = not np.array_equal(img, ref_img)
        force = name in reencode
        x0, y0 = (es["crop"][0], es["crop"][1]) if es.get("crop") else (0, 0)
        sub = es["g1t"]["sub_texture"]
        ktid = int(es["ktid"], 16)
        if name == gd.LATAM_LABEL_ASSET:
            if changed:
                plan.label = img
            if force:
                _paste(_edit(plan, game, ktid, sub), ref_img, x0, y0, True, name + " (es-ES)")
                plan.reencoded.append(name)
            continue
        if not (changed or force):
            continue
        _paste(_edit(plan, game, ktid, sub), img, x0, y0, force, name)
        if changed:
            plan.global_replacements.append(name)
        if force:
            plan.reencoded.append(name)
    return plan


def _add_label(plan: Plan, game: _Game, sprite_data: bytes) -> bytes:
    assert plan.label is not None
    g = parse_g1t(game.texture(gd.LABEL_ATLAS))
    tex = g.textures[gd.LABEL_ATLAS_SUB]
    x0, y0, x1, y1 = gd.LATAM_LABEL_RECT
    if plan.label.shape != (y1 - y0, x1 - x0, 4):
        raise BuildError("the label must be %dx%d" % (x1 - x0, y1 - y0))
    m = FREE_MARGIN
    area = game.image(gd.LABEL_ATLAS, gd.LABEL_ATLAS_SUB)[max(0, y0 - m):y1 + m, max(0, x0 - m):x1 + m]
    if area[..., 3].any():
        raise BuildError("the atlas area for the new label is not transparent")
    for s in parse_sprite_set(sprite_data):
        if s.texture_index != gd.LABEL_ATLAS_SUB:
            continue
        r = s.rect(tex.width, tex.height)
        if r[0] < x1 + m and x0 - m < r[2] and r[1] < y1 + m and y0 - m < r[3]:
            raise BuildError("sprite %s overlaps the area for the new label" % s.name)
    edit = _edit(plan, game, gd.LABEL_ATLAS, gd.LABEL_ATLAS_SUB)
    _paste(edit, plan.label, x0, y0, False, gd.LATAM_LABEL_ASSET + " (es-419 label)")
    record = sprite_record(gd.LATAM_LABEL_SPRITE, gd.LABEL_ATLAS_SUB, gd.LATAM_LABEL_RECT,
                           tex.width, tex.height)
    return append_sprite(sprite_data, record)


def label_table_record(romfs: str, with_label: bool) -> bytes:
    from ldtool.container import LinkData

    with LinkData.open_dir(romfs) as ld:
        if len(ld) <= gd.LABEL_TABLE_RECORD:
            raise BuildError("LINKDATA has no record %d" % gd.LABEL_TABLE_RECORD)
        data = bytearray(ld.read(gd.LABEL_TABLE_RECORD))

    def row(i: int) -> int:
        return gd.LABEL_TABLE_HEADER + i * gd.LABEL_TABLE_ROW

    count = struct.unpack_from("<I", data, 0)[0]
    if count <= gd.LABEL_ROW_LATAM or len(data) < row(count):
        raise BuildError("record %d is not the label table" % gd.LABEL_TABLE_RECORD)
    spain = struct.unpack_from("<IHH", data, row(gd.LABEL_ROW_SPAIN))
    if spain != (gd.kt_hash(gd.LABEL_SPRITES["es"]), gd.LABEL_UI_ASSET, 0):
        raise BuildError("record %d row 0x%x is not the Spain label (another game version?)"
                         % (gd.LABEL_TABLE_RECORD, gd.LABEL_ROW_SPAIN))
    if any(data[row(gd.LABEL_ROW_LATAM):row(gd.LABEL_ROW_LATAM + 1)]):
        raise BuildError("record %d row 0x%x is not empty" % (gd.LABEL_TABLE_RECORD, gd.LABEL_ROW_LATAM))
    new = (gd.kt_hash(gd.LATAM_LABEL_SPRITE), gd.LABEL_UI_ASSET, 0) if with_label else spain
    struct.pack_into("<IHH", data, row(gd.LABEL_ROW_LATAM), *new)
    return bytes(data)


def _out_dir(out_romfs: str) -> str:
    return os.path.join(out_romfs, gd.ASSET_DIR)


def _clean(out_dir: str, keep: set[str], log: Callable[[str], None]) -> list[str]:
    removed = []
    if not os.path.isdir(out_dir):
        return removed
    for root, dirs, files in os.walk(out_dir, topdown=False):
        for f in files:
            path = os.path.join(root, f)
            if os.path.normcase(os.path.abspath(path)) not in keep:
                os.remove(path)
                removed.append(path)
                log("  removed %s" % path)
        for d in dirs:
            p = os.path.join(root, d)
            if not os.listdir(p):
                os.rmdir(p)
    p = out_dir
    for _ in gd.ASSET_DIR.split(os.sep):
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
        p = os.path.dirname(p)
    return removed


def build(romfs: str, assets_dir: str, out_romfs: str, reencode: tuple[str, ...] = (),
          log: Callable[[str], None] = print) -> dict:
    with open(os.path.join(assets_dir, MANIFEST), encoding="utf-8") as f:
        manifest = json.load(f)
    base = os.path.join(romfs, gd.ASSET_DIR)
    out_dir = _out_dir(out_romfs)
    if os.path.normcase(os.path.abspath(out_dir)) == os.path.normcase(os.path.abspath(base)):
        raise BuildError("the output folder is the game's own romfs")
    summary: dict = {"textures": [], "files": [], "removed": []}
    with ResourceDb(base) as db:
        game = _Game(db)
        plan = make_plan(manifest, assets_dir, game, tuple(reencode))
        resources: dict[int, bytes] = {}
        if plan.label is not None:
            entry = db.by_ktid[gd.LABEL_SPRITE_SET]
            resources[gd.LABEL_SPRITE_SET] = _add_label(plan, game, db.read(entry))
        for ktid in sorted(plan.textures):
            data = game.texture(ktid)
            g = parse_g1t(data)
            out = bytearray(data)
            for sub in sorted(plan.textures[ktid]):
                edit = plan.textures[ktid][sub]
                tex = g.textures[sub]
                try:
                    res = encode_texture(data, tex, edit.image, edit.force)
                except G1tError as ex:
                    raise BuildError("%s: %s" % (", ".join(edit.sources), ex)) from ex
                out[tex.data_offset:tex.data_end] = res.data
                info = {"ktid": _hex(ktid), "sub_texture": sub, "format": tex.format_name,
                        "size": [tex.width, tex.height], "mips": tex.mips, "sources": edit.sources,
                        "blocks_encoded": res.blocks_encoded, "blocks_kept": res.blocks_kept,
                        "roundtrip": res.roundtrip}
                summary["textures"].append(info)
                log("  %s/%d %s %dx%d: %d blocks encoded, %d kept; round trip %.1f dB, max %d  [%s]"
                    % (_hex(ktid), sub, tex.format_name, tex.width, tex.height, res.blocks_encoded,
                       res.blocks_kept, res.roundtrip["psnr"], res.roundtrip["max_abs"],
                       ", ".join(edit.sources)))
                if res.blocks_encoded and res.roundtrip["psnr"] < ROUNDTRIP_MIN_PSNR:
                    plan.warnings.append("%s: round trip only %.1f dB" % (", ".join(edit.sources),
                                                                         res.roundtrip["psnr"]))
            resources[ktid] = bytes(out)
        summary["linkdata_records"] = {
            gd.LABEL_TABLE_RECORD: label_table_record(romfs, plan.label is not None)}
        keep: set[str] = set()
        if resources:
            keep = set(_write_outputs(db, base, out_dir, resources, summary))
    summary["removed"] = _clean(out_dir, keep, log)
    summary["label"] = plan.label is not None
    summary["global_replacements"] = plan.global_replacements
    summary["reencoded"] = plan.reencoded
    summary["warnings"] = plan.warnings
    for name in plan.global_replacements:
        log("warning: %s replaces its es-ES texture for every language that uses it, "
            "Español (España) included (no Latam-only image swap yet)" % name)
    for w in plan.warnings:
        log("warning: " + w)
    if plan.label is not None:
        log("  10th-button label: %s -> sprite %s (record %d row 0x%x)" % (
            gd.LATAM_LABEL_ASSET, gd.LATAM_LABEL_SPRITE, gd.LABEL_TABLE_RECORD, gd.LABEL_ROW_LATAM))
    else:
        log("  10th-button label: none (es-419 label equals es-ES); the button shows the Spain label")
    if not resources:
        log("  no image changes: nothing installed")
    return summary


def _write_outputs(db: ResourceDb, base: str, out_dir: str, resources: dict[int, bytes],
                   summary: dict) -> list[str]:
    host_index = db.fdata_index_of(gd.HOST_FDATA, ROOT_MOUNT)
    host_name = "0x%08x.fdata" % gd.HOST_FDATA
    with open(os.path.join(base, host_name), "rb") as f:
        fdata = bytearray(f.read())
    with open(os.path.join(base, "root.rdb"), "rb") as f:
        rdb = bytearray(f.read())
    offsets = rdb_entry_offsets(bytes(rdb))
    for ktid in sorted(resources):
        entry = db.by_ktid[ktid]
        stored = db.read_stored(entry)
        if not stored_crc_ok(stored):
            raise BuildError("%s: unexpected parameter block (no CRC-32)" % _hex(ktid))
        fdata += b"\0" * (-len(fdata) % FDATA_ALIGN)
        new = raw_fdata_entry(stored, resources[ktid])
        patch_rdb_entry(rdb, offsets[entry.index], host_index, len(fdata), len(new),
                        len(resources[ktid]))
        fdata += new
    fdata += b"\0" * (-len(fdata) % FDATA_ALIGN)
    paths = []
    for name, data in ((host_name, bytes(fdata)), ("root.rdb", bytes(rdb))):
        path = os.path.join(out_dir, name)
        write_if_changed(path, data)
        paths.append(os.path.normcase(os.path.abspath(path)))
        summary["files"].append({"path": path, "bytes": len(data)})
    summary["resources"] = [_hex(k) for k in sorted(resources)]
    return paths
