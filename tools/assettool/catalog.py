from __future__ import annotations

import collections
import os
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import gamedata as gd
from .g1t import G1t, parse_g1t
from .kidsobj import ObjDbError, TextureBinding, parse_objdb, texture_bindings
from .rdb import RdbError, ResourceDb
from .sprites import Sprite, SpriteError, parse_sprite_set

M32 = 1 << 32


class CatalogError(Exception):
    pass


def scale_factor(exponent: int) -> int:
    if exponent >= 0:
        return pow(31, exponent, M32)
    return pow(pow(31, -1, M32), -exponent, M32)


@dataclass(frozen=True)
class Fingerprint:
    lang: str
    exponent: int
    votes: int
    anchor: int


def fingerprint_textures(ktids: list[int], known: set[int],
                         offsets: dict[str, int] | None = None,
                         exponents: tuple[int, ...] = gd.SCALE_EXPONENTS,
                         min_votes: int = gd.MIN_FINGERPRINT_VOTES) -> dict[int, Fingerprint]:
    offsets = offsets or gd.LANG_KTID_OFFSETS
    if not ktids:
        return {}
    arr = np.array(sorted(set(ktids)), dtype=np.int64)
    pool = np.array(sorted(known), dtype=np.int64)
    langs = list(offsets)
    best_votes = np.zeros(len(arr), dtype=np.int64)
    best: list[Fingerprint | None] = [None] * len(arr)
    tied = np.zeros(len(arr), dtype=bool)
    for exp in exponents:
        s = scale_factor(exp)
        scaled = {l: (offsets[l] * s) % M32 for l in langs}
        for lang in langs:
            anchor = (arr - scaled[lang]) % M32
            votes = np.zeros(len(arr), dtype=np.int64)
            for other in langs:
                if other != lang:
                    votes += np.isin((anchor + scaled[other]) % M32, pool)
            better = votes > best_votes
            same = (votes == best_votes) & (votes >= min_votes)
            for i in np.nonzero(same)[0]:
                fp = best[i]
                if fp is not None and fp.lang != lang:
                    tied[i] = True
            for i in np.nonzero(better)[0]:
                best_votes[i] = votes[i]
                best[i] = Fingerprint(lang, exp, int(votes[i]), int(anchor[i]))
                tied[i] = False
    out: dict[int, Fingerprint] = {}
    for i, fp in enumerate(best):
        if fp is not None and fp.votes >= min_votes and not tied[i]:
            out[int(arr[i])] = fp
    return out


@dataclass
class Pack:
    ktid: int
    bindings: list[TextureBinding]
    lang: str | None = None
    exponent: int | None = None


@dataclass
class LanguageSet:
    id: str
    exponent: int
    packs: dict[str, int]


@dataclass
class TextureRef:
    ktid: int
    sub: int
    sprite_set: int | None = None
    sprites: list[tuple[str, tuple[int, int, int, int]]] = field(default_factory=list)
    crop: tuple[int, int, int, int] | None = None


@dataclass
class Asset:
    name: str
    category: str
    description: str
    kind: str
    es: TextureRef
    en: TextureRef | None
    match: str | None
    set_id: str | None = None
    es_pack: int | None = None
    en_pack: int | None = None
    fingerprint: Fingerprint | None = None


@dataclass
class Catalog:
    assets: list[Asset]
    language_sets: list[LanguageSet]
    shared: list[dict]
    notes: list[str]
    stats: dict[str, int]
    g1t: dict[int, G1t]
    data: dict[int, bytes]


def _category(name: str) -> tuple[str, str]:
    for prefix, cat, desc in gd.CATEGORY_RULES:
        if name.startswith(prefix):
            return cat, desc
    return gd.DEFAULT_CATEGORY


def _base_name(names: list[str]) -> str:
    names = sorted(names)
    if len(names) == 1:
        return names[0]
    prefix = os.path.commonprefix(names).rstrip("_")
    return (prefix if len(prefix) >= 6 else names[0]) + "_atlas"


class _Reader:
    def __init__(self, db: ResourceDb):
        self.db = db
        self.data: dict[int, bytes] = {}
        self.g1t: dict[int, G1t] = {}
        self.sprite_sets: dict[int, list[Sprite]] = {}

    def texture(self, ktid: int) -> G1t:
        if ktid not in self.g1t:
            entry = self.db.by_ktid.get(ktid)
            if entry is None or entry.type_ktid != gd.TYPE_TEXTURE:
                raise CatalogError("KTID 0x%08x is not a texture" % ktid)
            self.data[ktid] = self.db.read(entry)
            self.g1t[ktid] = parse_g1t(self.data[ktid])
        return self.g1t[ktid]

    def sprites(self, ktid: int) -> list[Sprite]:
        if ktid not in self.sprite_sets:
            entry = self.db.by_ktid.get(ktid)
            sprites: list[Sprite] = []
            if entry is not None and entry.type_ktid == gd.TYPE_SPRITE_SET and self.db.has_data(entry):
                try:
                    sprites = parse_sprite_set(self.db.read(entry))
                except (SpriteError, RdbError):
                    sprites = []
            self.sprite_sets[ktid] = sprites
        return self.sprite_sets[ktid]

    def texture_ref(self, ktid: int, sub: int, sprite_set: int | None) -> TextureRef:
        tex = self.texture(ktid).textures[sub]
        ref = TextureRef(ktid, sub, sprite_set)
        if sprite_set:
            for s in self.sprites(sprite_set):
                if s.texture_index == sub:
                    ref.sprites.append((s.name, s.rect(tex.width, tex.height)))
            ref.sprites.sort()
        return ref


def load_packs(db: ResourceDb, log: Callable[[str], None]) -> tuple[dict[int, Pack], dict[str, int]]:
    packs: dict[int, Pack] = {}
    stats = collections.Counter()
    for entry in db.entries:
        if entry.type_ktid != gd.TYPE_OBJDB:
            continue
        stats["objdb_entries"] += 1
        if not db.has_data(entry):
            stats["objdb_without_data"] += 1
            continue
        try:
            objdb = parse_objdb(db.read(entry))
        except (ObjDbError, RdbError):
            stats["objdb_unreadable"] += 1
            continue
        binds = texture_bindings(objdb, gd.OBJ_TEXTURE_BINDING)
        if binds:
            packs[entry.ktid] = Pack(entry.ktid, binds)
    stats["texture_packs"] = len(packs)
    log("%d object databases, %d texture packs" % (stats["objdb_entries"], len(packs)))
    return packs, dict(stats)


def assign_languages(packs: dict[int, Pack], fps: dict[int, Fingerprint]) -> None:
    for pack in packs.values():
        texs = {b.texture for b in pack.bindings if b.texture}
        votes = collections.Counter((fps[t].lang, fps[t].exponent) for t in texs if t in fps)
        if not votes:
            continue
        (lang, exp), n = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if 2 * n > sum(votes.values()):
            pack.lang, pack.exponent = lang, exp


def group_language_sets(packs: dict[int, Pack], notes: list[str]) -> list[LanguageSet]:
    langs = sorted((p for p in packs.values() if p.lang), key=lambda p: p.ktid)
    parent = {p.ktid: p.ktid for p in langs}

    def find(k: int) -> int:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    owner: dict[tuple[int, int], int] = {}
    for p in langs:
        for b in p.bindings:
            if not b.sprite_set or not b.texture:
                continue
            key = (p.exponent or 0, b.sprite_set)
            if key in owner:
                a, c = find(owner[key]), find(p.ktid)
                if a != c:
                    parent[max(a, c)] = min(a, c)
            else:
                owner[key] = p.ktid
    groups: dict[int, list[Pack]] = collections.defaultdict(list)
    for p in langs:
        groups[find(p.ktid)].append(p)
    sets: list[LanguageSet] = []
    for members in groups.values():
        by_lang: dict[str, int] = {}
        for p in sorted(members, key=lambda p: p.ktid):
            if p.lang in by_lang:
                notes.append("language set with two %s packs: 0x%08x, 0x%08x (kept the first)"
                             % (p.lang, by_lang[p.lang], p.ktid))
                continue
            by_lang[p.lang] = p.ktid
        if len(by_lang) < 2:
            continue
        sets.append(LanguageSet("", members[0].exponent or 0, by_lang))
    sets.sort(key=lambda s: (s.packs.get(gd.COUNTERPART_LANGUAGE, 0), min(s.packs.values())))
    for i, s in enumerate(sets, 1):
        s.id = "set%d" % i
        missing = [l for l in gd.LANGUAGES if l not in s.packs]
        if missing:
            notes.append("%s has no pack for: %s" % (s.id, ", ".join(missing)))
    return sets


def _set_assets(rd: _Reader, lset: LanguageSet, packs: dict[int, Pack], fps: dict[int, Fingerprint],
                shared: list[dict], notes: list[str]) -> list[Asset]:
    src, ref = gd.SOURCE_LANGUAGE, gd.COUNTERPART_LANGUAGE
    if src not in lset.packs:
        return []
    es_pack = packs[lset.packs[src]]
    en_pack = packs[lset.packs[ref]] if ref in lset.packs else None
    en_by_tex = {b.texture: b for b in en_pack.bindings if b.texture} if en_pack else {}
    en_by_set = {b.sprite_set: b for b in en_pack.bindings if b.texture} if en_pack else {}
    out: list[Asset] = []
    seen: set[int] = set()
    for b in sorted(es_pack.bindings, key=lambda b: b.texture):
        if not b.texture or b.texture in seen:
            continue
        seen.add(b.texture)
        fp = fps.get(b.texture)
        en_b, match = None, None
        if fp is not None and fp.lang == src and fp.anchor in en_by_tex:
            en_b, match = en_by_tex[fp.anchor], "ktid-offset"
        elif b.sprite_set in en_by_set:
            en_b, match = en_by_set[b.sprite_set], "sprite-set"
        if en_b is not None and en_b.texture == b.texture:
            shared.append({"set": lset.id, "texture": "0x%08x" % b.texture,
                           "sprite_set": "0x%08x" % b.sprite_set})
            continue
        es_g1t = rd.texture(b.texture)
        en_g1t = rd.texture(en_b.texture) if en_b else None
        for sub in range(len(es_g1t.textures)):
            es_ref = rd.texture_ref(b.texture, sub, b.sprite_set)
            en_ref = None
            if en_b is not None and en_g1t is not None and sub < len(en_g1t.textures):
                en_ref = rd.texture_ref(en_b.texture, sub, en_b.sprite_set)
            set_names = [s.name for s in rd.sprites(b.sprite_set)] if b.sprite_set else []
            if not set_names and en_ref is not None:
                set_names = [n for n, _ in en_ref.sprites]
            base = _base_name(set_names) if set_names else "texture_%08x" % b.texture
            if not es_ref.sprites and len(es_g1t.textures) > 1:
                notes.append("0x%08x sub-texture %d has no sprites" % (b.texture, sub))
            category, desc = _category(set_names[0] if set_names else "")
            name = "%s__%s" % (category, base)
            if len(es_g1t.textures) > 1:
                name += ".t%d" % sub
            out.append(Asset(name, category, desc, "texture", es_ref, en_ref, match if en_ref else None,
                             lset.id, es_pack.ktid, en_pack.ktid if en_pack else None, fp))
    return out


def _label_asset(rd: _Reader, packs: dict[int, Pack], notes: list[str]) -> Asset | None:
    want_es, want_en = gd.LABEL_SPRITES[gd.SOURCE_LANGUAGE], gd.LABEL_SPRITES[gd.COUNTERPART_LANGUAGE]
    for pack in sorted(packs.values(), key=lambda p: p.ktid):
        if pack.lang:
            continue
        for b in pack.bindings:
            if not b.sprite_set or not b.texture:
                continue
            entry = rd.db.by_ktid.get(b.sprite_set)
            if entry is None or not rd.db.has_data(entry):
                continue
            sprites = {s.name: s for s in rd.sprites(b.sprite_set)}
            if want_es not in sprites:
                continue
            g1t = rd.texture(b.texture)
            es_s = sprites[want_es]
            tex = g1t.textures[es_s.texture_index]
            es_ref = TextureRef(b.texture, es_s.texture_index, b.sprite_set,
                                [(want_es, es_s.rect(tex.width, tex.height))], es_s.rect(tex.width, tex.height))
            en_ref = None
            if want_en in sprites:
                en_s = sprites[want_en]
                t2 = g1t.textures[en_s.texture_index]
                en_ref = TextureRef(b.texture, en_s.texture_index, b.sprite_set,
                                    [(want_en, en_s.rect(t2.width, t2.height))], en_s.rect(t2.width, t2.height))
            category, desc = _category(want_es)
            return Asset("%s__%s" % (category, want_es), category, desc, "sprite", es_ref, en_ref,
                         "label-sprite" if en_ref else None, None, pack.ktid, pack.ktid if en_ref else None)
    notes.append("language label sprite %s not found" % want_es)
    return None


def _disambiguate(db: ResourceDb, assets: list[Asset]) -> None:
    for rule in ("mount", "ktid"):
        seen = collections.Counter(a.name for a in assets)
        for a in assets:
            if seen[a.name] < 2:
                continue
            ref = db.fdata_ref(db.by_ktid[a.es.ktid])
            if rule == "mount" and ref is not None and ref.mount != 0xFF:
                a.name += ".mp%d" % ref.mount
            elif rule == "ktid":
                a.name += ".%08x" % a.es.ktid


def build_catalog(db: ResourceDb, log: Callable[[str], None] = lambda m: None) -> Catalog:
    notes: list[str] = []
    packs, stats = load_packs(db, log)
    known = {e.ktid for e in db.entries if e.type_ktid == gd.TYPE_TEXTURE}
    pack_textures = sorted({b.texture for p in packs.values() for b in p.bindings if b.texture})
    fps = fingerprint_textures(pack_textures, known)
    assign_languages(packs, fps)
    sets = group_language_sets(packs, notes)
    log("%d language sets" % len(sets))
    if not sets:
        raise CatalogError("no language sets found")
    rd = _Reader(db)
    shared: list[dict] = []
    assets: list[Asset] = []
    for lset in sets:
        assets += _set_assets(rd, lset, packs, fps, shared, notes)
    label = _label_asset(rd, packs, notes)
    if label is not None:
        assets.append(label)
    _disambiguate(db, assets)
    assets.sort(key=lambda a: a.name)
    if len({a.name for a in assets}) != len(assets):
        raise CatalogError("asset names are not unique")
    stats.update(language_packs=sum(1 for p in packs.values() if p.lang), language_sets=len(sets),
                 fingerprinted_textures=len(fps), assets=len(assets))
    return Catalog(assets, sets, shared, notes, stats, rd.g1t, rd.data)
