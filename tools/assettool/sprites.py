from __future__ import annotations

import struct
from dataclasses import dataclass

SPRITE_SIZE = 104


class SpriteError(Exception):
    pass


@dataclass(frozen=True)
class Sprite:
    name: str
    texture_index: int
    uv: tuple[float, float, float, float]

    def rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        u0, v0, u1, v1 = self.uv
        return (round(u0 * width), round(v0 * height), round(u1 * width), round(v1 * height))


def sprite_record(name: str, texture_index: int, rect: tuple[int, int, int, int],
                  width: int, height: int) -> bytes:
    raw = name.encode("ascii")
    if not 0 < len(raw) < 64:
        raise SpriteError("bad sprite name %r" % name)
    x0, y0, x1, y1 = rect
    u0, v0, u1, v1 = x0 / width, y0 / height, x1 / width, y1 / height
    return raw.ljust(64, b"\0") + struct.pack("<I8fI", texture_index, u0, v0, u1, v0, u0, v1, u1, v1, 1)


def append_sprite(data: bytes, record: bytes) -> bytes:
    count = struct.unpack_from("<I", data, 0)[0]
    if len(data) != 4 + count * SPRITE_SIZE or len(record) != SPRITE_SIZE:
        raise SpriteError("unexpected sprite set size")
    name = record[:64].split(b"\0", 1)[0]
    for i in range(count):
        if data[4 + i * SPRITE_SIZE:4 + i * SPRITE_SIZE + 64].split(b"\0", 1)[0] == name:
            raise SpriteError("sprite %s already exists" % name.decode("ascii", "replace"))
    return struct.pack("<I", count + 1) + data[4:] + record


def parse_sprite_set(data: bytes) -> list[Sprite]:
    if len(data) < 4:
        raise SpriteError("truncated sprite set")
    count = struct.unpack_from("<I", data, 0)[0]
    if 4 + count * SPRITE_SIZE > len(data):
        raise SpriteError("sprite set with %d sprites does not fit in %d bytes" % (count, len(data)))
    out: list[Sprite] = []
    for i in range(count):
        p = 4 + i * SPRITE_SIZE
        name = data[p:p + 64].split(b"\0", 1)[0].decode("ascii", "replace")
        tex = struct.unpack_from("<I", data, p + 64)[0]
        c = struct.unpack_from("<8f", data, p + 68)
        out.append(Sprite(name, tex, (c[0], c[1], c[6], c[7])))
    return out
