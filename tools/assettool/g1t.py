from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

G1T_MAGIC = b"GT1G"
EXT_HEADER_SIZE = 12

FORMATS = {
    0x60: ("BC1", 1, 8, "RGBA"),
    0x61: ("BC2", 2, 16, "RGBA"),
    0x62: ("BC3", 3, 16, "RGBA"),
    0x63: ("BC4", 4, 8, "L"),
    0x64: ("BC5", 5, 16, "RGB"),
    0x66: ("BC7", 7, 16, "RGBA"),
}
SWIZZLE = "switch2-block-linear"


class G1tError(Exception):
    pass


@dataclass(frozen=True)
class G1tTexture:
    index: int
    format: int
    width: int
    height: int
    mips: int
    flags: bytes
    ext_header: bytes
    data_offset: int
    data_end: int

    @property
    def format_name(self) -> str:
        return FORMATS[self.format][0] if self.format in FORMATS else "0x%02x" % self.format


@dataclass(frozen=True)
class G1t:
    version: str
    platform: int
    textures: tuple[G1tTexture, ...]


def parse_g1t(data: bytes) -> G1t:
    if data[:4] != G1T_MAGIC:
        raise G1tError("not a GT1G texture")
    version = data[4:8].decode("ascii", "replace")
    _size, table, count, platform, _x = struct.unpack_from("<IIIII", data, 8)
    if table + 4 * count > len(data):
        raise G1tError("texture table out of range")
    starts = [table + struct.unpack_from("<I", data, table + 4 * i)[0] for i in range(count)]
    texs: list[G1tTexture] = []
    for i, start in enumerate(starts):
        if start + 8 > len(data):
            raise G1tError("texture %d header out of range" % i)
        b = data[start:start + 8]
        flags = bytes(b[4:8])
        pos = start + 8
        ext = b""
        if flags[3] >> 4:
            esize = struct.unpack_from("<I", data, pos)[0]
            if esize != EXT_HEADER_SIZE:
                raise G1tError("texture %d: unsupported extended header size %d" % (i, esize))
            ext = data[pos:pos + esize]
            pos += esize
        end = starts[i + 1] if i + 1 < count else len(data)
        texs.append(G1tTexture(i, b[1], 1 << (b[2] & 15), 1 << (b[2] >> 4), b[0] >> 4,
                               flags, ext, pos, end))
    return G1t(version, platform, tuple(texs))


def gob_block_height(height_blocks: int) -> int:
    bh = 1
    while bh < 16 and bh * 8 < height_blocks:
        bh *= 2
    return bh


def block_linear_offsets(width: int, height: int, bpe: int) -> np.ndarray:
    bh = gob_block_height(height)
    gobs_x = (width * bpe + 63) // 64
    y = np.arange(height, dtype=np.int64)[:, None]
    x = (np.arange(width, dtype=np.int64) * bpe)[None, :]
    inner = ((x & 15) | ((y & 1) << 4) | (((y >> 1) & 1) << 5) | (((x >> 4) & 1) << 6)
             | (((y >> 2) & 1) << 7) | (((x >> 5) & 1) << 8))
    gob = (y // (8 * bh)) * gobs_x * bh + (x // 64) * bh + (y % (8 * bh)) // 8
    return gob * 512 + inner


def swizzled_size(width: int, height: int, bpe: int) -> int:
    bh = gob_block_height(height)
    gobs_x = (width * bpe + 63) // 64
    rows = -(-height // (8 * bh))
    return gobs_x * rows * bh * 512


def deswizzle(data: bytes, width: int, height: int, bpe: int) -> bytes:
    need = swizzled_size(width, height, bpe)
    if len(data) < need:
        raise G1tError("swizzled data is %d bytes, need %d" % (len(data), need))
    src = np.frombuffer(data, dtype=np.uint8, count=need)
    offs = block_linear_offsets(width, height, bpe)
    return src[offs[..., None] + np.arange(bpe)].tobytes()


def swizzle(linear: bytes, width: int, height: int, bpe: int) -> bytes:
    src = np.frombuffer(linear, dtype=np.uint8)
    if len(src) != width * height * bpe:
        raise G1tError("linear data is %d bytes, need %d" % (len(src), width * height * bpe))
    out = np.zeros(swizzled_size(width, height, bpe), dtype=np.uint8)
    offs = block_linear_offsets(width, height, bpe)
    out[(offs[..., None] + np.arange(bpe)).ravel()] = src
    return out.tobytes()


def level_layout(tex: G1tTexture) -> list[tuple[int, int, int, int]]:
    if tex.format not in FORMATS:
        raise G1tError("unsupported G1T format 0x%02x" % tex.format)
    bpe = FORMATS[tex.format][2]
    out, pos = [], 0
    for k in range(tex.mips):
        w, h = max(1, tex.width >> k), max(1, tex.height >> k)
        size = swizzled_size((w + 3) // 4, (h + 3) // 4, bpe)
        out.append((w, h, pos, size))
        pos += size
    if tex.data_offset + pos > tex.data_end:
        raise G1tError("texture %d: %d mip bytes do not fit in %d" % (
            tex.index, pos, tex.data_end - tex.data_offset))
    return out


def mip0_size(tex: G1tTexture) -> int:
    _n, _d, bpe, _m = FORMATS[tex.format]
    return swizzled_size((tex.width + 3) // 4, (tex.height + 3) // 4, bpe)


def decode_texture(data: bytes, tex: G1tTexture) -> Image.Image:
    if tex.format not in FORMATS:
        raise G1tError("unsupported G1T format 0x%02x" % tex.format)
    _name, n, bpe, mode = FORMATS[tex.format]
    wb, hb = (tex.width + 3) // 4, (tex.height + 3) // 4
    raw = data[tex.data_offset:tex.data_end]
    lin = deswizzle(raw, wb, hb, bpe)
    img = Image.frombytes(mode, (wb * 4, hb * 4), lin, "bcn", n)
    if img.size != (tex.width, tex.height):
        img = img.crop((0, 0, tex.width, tex.height))
    return img.convert("RGBA")


@dataclass(frozen=True)
class EncodeResult:
    data: bytes
    blocks_encoded: int
    blocks_kept: int
    roundtrip: dict


def encode_texture(data: bytes, tex: G1tTexture, image: np.ndarray,
                   force: np.ndarray | None = None) -> EncodeResult:
    from . import bcn

    if tex.format not in bcn.ENCODERS:
        raise G1tError("no encoder for format %s" % tex.format_name)
    image = np.ascontiguousarray(image, dtype=np.uint8)
    if image.shape != (tex.height, tex.width, 4):
        raise G1tError("image is %dx%d, texture %d is %dx%d" % (
            image.shape[1], image.shape[0], tex.index, tex.width, tex.height))
    bpe = FORMATS[tex.format][2]
    levels = level_layout(tex)
    region = bytearray(data[tex.data_offset:tex.data_end])
    w0, h0, off0, size0 = levels[0]
    wb, hb = (w0 + 3) // 4, (h0 + 3) // 4
    original = np.array(decode_texture(data, tex))
    new_blocks = bcn.image_blocks(image)
    changed = (new_blocks != bcn.image_blocks(original)).any(axis=(1, 2))
    if force is not None:
        changed |= np.asarray(force, bool).reshape(-1)
    lin = np.frombuffer(deswizzle(bytes(region[off0:off0 + size0]), wb, hb, bpe),
                        np.uint8).reshape(hb * wb, bpe).copy()
    sel = np.nonzero(changed)[0]
    if len(sel):
        lin[sel] = bcn.encode_blocks(tex.format, new_blocks[sel])
    region[off0:off0 + size0] = swizzle(lin.tobytes(), wb, hb, bpe)
    for w, h, off, size in levels[1:]:
        mip = np.array(Image.fromarray(image, "RGBA").resize((w, h), Image.BOX))
        enc = bcn.encode_blocks(tex.format, bcn.image_blocks(mip))
        region[off:off + size] = swizzle(enc.tobytes(), (w + 3) // 4, (h + 3) // 4, bpe)
    out = data[:tex.data_offset] + bytes(region) + data[tex.data_end:]
    if len(sel):
        decoded = bcn.image_blocks(np.array(decode_texture(out, tex)))
        check = bcn.compare(new_blocks[sel], decoded[sel])
    else:
        check = bcn.compare(new_blocks[:0], new_blocks[:0])
    return EncodeResult(bytes(region), int(len(sel)), int(wb * hb - len(sel)), check)
