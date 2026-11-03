from __future__ import annotations

import struct
from dataclasses import dataclass

DOK_MAGIC = b"_DOK0000"
OBJ_MAGIC = b"IDOK"


class ObjDbError(Exception):
    pass


@dataclass(frozen=True)
class KidsObject:
    ktid: int
    type_ktid: int
    props: tuple[tuple[int, int, int], ...]
    values: tuple[int, ...]
    raw: bytes


@dataclass(frozen=True)
class ObjDb:
    ktid: int
    objects: tuple[KidsObject, ...]


@dataclass(frozen=True)
class TextureBinding:
    object_ktid: int
    texture: int
    sprite_set: int
    alias: int


def parse_objdb(data: bytes) -> ObjDb:
    if data[:8] != DOK_MAGIC:
        raise ObjDbError("not a _DOK0000 object database")
    hsize, _unk, _count, ktid = struct.unpack_from("<IIII", data, 8)
    objs: list[KidsObject] = []
    off = hsize
    while off + 24 <= len(data) and data[off:off + 4] == OBJ_MAGIC:
        size, oktid, tktid, pcount = struct.unpack_from("<IIII", data, off + 8)
        if size < 24 or off + size > len(data):
            raise ObjDbError("bad IDOK size at 0x%x" % off)
        pos = off + 24
        props: list[tuple[int, int, int]] = []
        if 24 + 12 * pcount <= size:
            for _ in range(pcount):
                props.append(struct.unpack_from("<III", data, pos))
                pos += 12
        raw = data[pos:off + size]
        words = struct.unpack("<%dI" % (len(raw) // 4), raw[:len(raw) // 4 * 4])
        objs.append(KidsObject(oktid, tktid, tuple(props), tuple(words), raw))
        off += size
    return ObjDb(ktid, tuple(objs))


def texture_bindings(db: ObjDb, binding_type: int) -> list[TextureBinding]:
    out: list[TextureBinding] = []
    for o in db.objects:
        if o.type_ktid != binding_type or len(o.values) < 3:
            continue
        if any(p[0] != 5 or p[1] != 1 for p in o.props):
            continue
        out.append(TextureBinding(o.ktid, o.values[0], o.values[1], o.values[2]))
    return out
