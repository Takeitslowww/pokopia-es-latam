from __future__ import annotations

import os
import struct
import zlib

M32 = 1 << 32

TYPE_TEXTURE = 0xAFBEC60C
TYPE_SPRITE_SET = 0xF20DE437
TYPE_OBJDB = 0x20A6A0BB
OBJ_BINDING = 0x79991E79
BINDING_PROPS = ((5, 1, 0xC8BB66DA), (5, 1, 0x2276FC56), (5, 1, 0x2856A1B3))
FDATA_KTID = 0x00011C54

OFFSETS = {"ja": 0x190ABFB6, "en": 0, "fr": 0xC742C669, "it": 0xEF3728C8, "de": 0x455E358A,
           "es": 0x07AA0DFA, "zh-Hant": 0x619F3AB3, "ko": 0x45651C90, "zh-Hans": 0x05DDA5B0}
LANGS = tuple(OFFSETS)

RED, GREEN, BLUE, WHITE, BLACK = 0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000
CLEAR = -1
RGB = {RED: (255, 0, 0), GREEN: (0, 255, 0), BLUE: (0, 0, 255), WHITE: (255, 255, 255),
       BLACK: (0, 0, 0)}


def ref_offset(x: int, y: int, width: int, height: int, bpe: int) -> int:
    bh = 1
    while bh < 16 and bh * 8 < height:
        bh *= 2
    xb = x * bpe
    gobs_x = (width * bpe + 63) // 64
    gob = (y // (8 * bh)) * gobs_x * bh + (xb // 64) * bh + (y % (8 * bh)) // 8
    y0, y1, y2 = y & 1, (y >> 1) & 1, (y >> 2) & 1
    x4, x5 = (xb >> 4) & 1, (xb >> 5) & 1
    inner = (xb & 15) | (y0 << 4) | (y1 << 5) | (x4 << 6) | (y2 << 7) | (x5 << 8)
    return gob * 512 + inner


def ref_swizzle(linear: bytes, width: int, height: int, bpe: int) -> bytes:
    bh = 1
    while bh < 16 and bh * 8 < height:
        bh *= 2
    size = ((width * bpe + 63) // 64) * (-(-height // (8 * bh))) * bh * 512
    out = bytearray(size)
    for y in range(height):
        for x in range(width):
            src = (y * width + x) * bpe
            dst = ref_offset(x, y, width, height, bpe)
            out[dst:dst + bpe] = linear[src:src + bpe]
    return bytes(out)


def bc1_block(color: int) -> bytes:
    if color == CLEAR:
        return struct.pack("<HHI", 0, 0, 0xFFFFFFFF)
    return struct.pack("<HHI", color, color, 0)


def bc1_texture(colors: list[list[int]]) -> tuple[int, int, bytes]:
    hb, wb = len(colors), len(colors[0])
    linear = b"".join(bc1_block(c) for row in colors for c in row)
    return wb * 4, hb * 4, ref_swizzle(linear, wb, hb, 8)


def g1t(textures: list[tuple[int, int, bytes]], fmt: int = 0x60, ext: bool = True) -> bytes:
    count = len(textures)
    table = 0x1C
    blobs = []
    for w, h, data in textures:
        lw, lh = w.bit_length() - 1, h.bit_length() - 1
        flags = bytes([0x11, 0x10, 0x21, 0x10]) if ext else bytes([0x00, 0x10, 0x21, 0x00])
        head = bytes([0x10, fmt, (lh << 4) | lw, 0]) + flags
        if ext:
            head += struct.pack("<III", 12, 0, 0x01000000)
        blobs.append(head + data)
    offsets, pos = [], 4 * count
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
    body = struct.pack("<%dI" % count, *offsets) + b"".join(blobs)
    size = table + len(body)
    return b"GT1G0600" + struct.pack("<IIIII", size, table, count, 0x18, 0) + body


def sprite_set(sprites: list[tuple[str, int, tuple[float, float, float, float]]]) -> bytes:
    out = struct.pack("<I", len(sprites))
    for name, tex, (u0, v0, u1, v1) in sprites:
        out += name.encode().ljust(64, b"\0") + struct.pack("<I8fI", tex, u0, v0, u1, v0, u0, v1, u1, v1, 1)
    return out


def objdb(ktid: int, objects: list[tuple[int, int, tuple, list[int]]]) -> bytes:
    body = b""
    for oktid, otype, props, values in objects:
        p = b"".join(struct.pack("<III", *x) for x in props)
        v = struct.pack("<%dI" % len(values), *values)
        body += b"IDOK0000" + struct.pack("<IIII", 24 + len(p) + len(v), oktid, otype, len(props)) + p + v
    return b"_DOK0000" + struct.pack("<IIIII", 0x1C, 0x18, len(objects), ktid, 0x1C + len(body)) + body


def binding(oktid: int, texture: int, sset: int, alias: int = 0):
    return (oktid, OBJ_BINDING, BINDING_PROPS, [texture, sset, alias])


def chunked_zlib(data: bytes, chunk: int = 0x40) -> bytes:
    out = b""
    for i in range(0, len(data), chunk):
        z = zlib.compress(data[i:i + chunk])
        out += struct.pack("<H", len(z)) + b"\0" * 8 + z
    return out + b"\0\0"


def idrk(ktid: int, tktid: int, flags: int, size: int, params: bytes, payload: bytes) -> bytes:
    esz = 0x30 + len(params) + len(payload)
    return struct.pack("<4s4sQQQIIII", b"IDRK", b"0000", esz, len(payload), size, 0, ktid, tktid,
                       flags) + params + payload


def write_romfs(root: str, resources: list[tuple[int, int, bytes]], fdata_mount: int = 0xFF) -> str:
    base = os.path.join(root, "motor_asset_re", "KIDS_PLATFORM_OUNCE")
    os.makedirs(base, exist_ok=True)
    fdata = bytearray(b"PDRK0000" + struct.pack("<II", 0x10, 0))
    rdb_entries = b""
    for ktid, tktid, data in sorted(resources):
        while len(fdata) % 16:
            fdata += b"\0"
        off = len(fdata)
        payload = chunked_zlib(data)
        params = struct.pack("<I", zlib.crc32(payload)) + b"\0" * 4
        entry = idrk(ktid, tktid, 0x00400000, len(data), params, payload)
        fdata += entry
        loc = b"\x01\x04" + struct.pack("<IIH", off, len(entry), 0) + b"\0"
        e = idrk(ktid, tktid, 0x00420000, len(data), b"\0" * 8, loc)
        rdb_entries += e + b"\0" * (-len(e) % 4)
    rdb = b"_DRK0000" + struct.pack("<IIII", 0x20, 0x18, len(resources), 0x12345678) + b"data/\0\0\0"
    with open(os.path.join(base, "root.rdb"), "wb") as f:
        f.write(rdb + rdb_entries)
    with open(os.path.join(base, "root.rdx"), "wb") as f:
        f.write(struct.pack("<HBBI", 0, fdata_mount, 0xFF, FDATA_KTID))
    folder = base if fdata_mount == 0xFF else os.path.join(base, "MountPoint_%d" % fdata_mount)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "0x%08x.fdata" % FDATA_KTID), "wb") as f:
        f.write(fdata)
    return base


LANG_COLORS = {"es": RED, "en": BLUE}
IMG1, IMG2 = 0x31000000, 0x52000000
SSET1, SSET2, SSET_SHARED, SSET_LABEL = 0x0A000001, 0x0A000002, 0x0A000003, 0x0A000004
TEX_SHARED, TEX_ATLAS = 0x0B000001, 0x0B000002


def lang_ktid(base: int, lang: str) -> int:
    return (base + OFFSETS[lang]) % M32


def build_game(root: str) -> str:
    res: list[tuple[int, int, bytes]] = []
    for lang in LANGS:
        c = LANG_COLORS.get(lang, GREEN)
        res.append((lang_ktid(IMG1, lang), TYPE_TEXTURE, g1t([bc1_texture([[c, WHITE], [BLACK, c]])])))
        res.append((lang_ktid(IMG2, lang), TYPE_TEXTURE, g1t([bc1_texture([[c] * 4] * 2)])))
    res.append((TEX_SHARED, TYPE_TEXTURE, g1t([bc1_texture([[WHITE]])])))
    res.append((TEX_ATLAS, TYPE_TEXTURE, g1t([bc1_texture([[GREEN]]),
                                              bc1_texture([[BLUE, RED, WHITE, BLACK]]
                                                          + [[CLEAR] * 4] * 3)])))
    res.append((SSET1, TYPE_SPRITE_SET, sprite_set([("still_tutorial_001", 0, (0.0, 0.0, 1.0, 1.0))])))
    res.append((SSET2, TYPE_SPRITE_SET, sprite_set([("8000_txt_a", 0, (0.0, 0.0, 0.5, 1.0)),
                                                    ("8000_txt_b", 0, (0.5, 0.0, 1.0, 1.0))])))
    res.append((SSET_SHARED, TYPE_SPRITE_SET, sprite_set([("icon_shared", 0, (0.0, 0.0, 1.0, 1.0))])))
    res.append((SSET_LABEL, TYPE_SPRITE_SET, sprite_set([
        ("0000_base_badge_0", 0, (0.0, 0.0, 1.0, 1.0)),
        ("0030_prt_language_1", 1, (0.0, 0.0, 0.25, 0.25)),
        ("0030_prt_language_2", 1, (0.25, 0.0, 0.5, 0.25))])))
    for i, lang in enumerate(LANGS):
        pack = 0x60000000 + i
        res.append((pack, TYPE_OBJDB, objdb(pack, [
            binding(0x70000000 + 16 * i, lang_ktid(IMG1, lang), SSET1),
            binding(0x70000001 + 16 * i, lang_ktid(IMG2, lang), SSET2),
            binding(0x70000002 + 16 * i, TEX_SHARED, SSET_SHARED),
            binding(0x70000003 + 16 * i, 0, 0, 0x70000000 + 16 * i)])))
    res.append((0x6F000000, TYPE_OBJDB, objdb(0x6F000000, [binding(0x7F000000, TEX_ATLAS, SSET_LABEL)])))
    base = write_romfs(root, res)
    movies = os.path.join(root, "movie_asset", "ounce")
    os.makedirs(movies, exist_ok=True)
    with open(os.path.join(movies, "MV01.webm"), "wb") as f:
        f.write(b"\x1a\x45\xdf\xa3fake")
    return base


LABEL_RECORD, LABEL_ROWS = 1552, 0x110


def name_hash(name: str) -> int:
    h = 0
    for c in reversed(name.encode()):
        h = ((h + c) * 31) % M32
    return h


def write_label_table(romfs: str) -> bytes:
    from ldtool.container import write_linkdata

    rec = bytearray(64 + 8 * LABEL_ROWS)
    struct.pack_into("<I", rec, 0, LABEL_ROWS)
    for row, name in ((0x100, "0030_prt_language_1"), (0x101, "0030_prt_language_2")):
        struct.pack_into("<IHH", rec, 64 + 8 * row, name_hash(name), 10, 0)
    write_linkdata(os.path.join(romfs, "LI_C0_R.IDX"), os.path.join(romfs, "LD_C0_R.BIN"),
                   LABEL_RECORD + 1, {LABEL_RECORD: bytes(rec)})
    return bytes(rec)
