from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

RDB_MAGIC = b"_DRK0000"
ENTRY_MAGIC = b"IDRK"
ENTRY_HEADER = struct.Struct("<4s4sQQQIIII")
FLAG_EXTERNAL = 0x00020000
FLAG_CHUNKED_ZLIB = 0x00400000
LOCATION_FDATA = b"\x01\x04"
ROOT_MOUNT = 0xFF


class RdbError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    fdata_index: int
    offset: int
    stored_size: int


@dataclass(frozen=True)
class RdbEntry:
    index: int
    ktid: int
    type_ktid: int
    flags: int
    size: int
    location: Location | None


@dataclass(frozen=True)
class FdataRef:
    index: int
    mount: int
    ktid: int

    @property
    def relpath(self) -> str:
        name = "0x%08x.fdata" % self.ktid
        if self.mount == ROOT_MOUNT:
            return name
        return "MountPoint_%d/%s" % (self.mount, name)


def _entry_header(buf: bytes, off: int) -> tuple[int, int, int, int, int, int, int]:
    if len(buf) < off + ENTRY_HEADER.size:
        raise RdbError("truncated IDRK header at 0x%x" % off)
    magic, _ver, esz, psz, dsz, pcount, ktid, tktid, flags = ENTRY_HEADER.unpack_from(buf, off)
    if magic != ENTRY_MAGIC:
        raise RdbError("no IDRK entry at 0x%x" % off)
    if esz < ENTRY_HEADER.size + psz or off + esz > len(buf):
        raise RdbError("bad IDRK sizes at 0x%x" % off)
    return esz, psz, dsz, pcount, ktid, tktid, flags


def parse_location(blob: bytes) -> Location | None:
    if len(blob) >= 13 and blob[:2] == LOCATION_FDATA:
        off, size, fidx = struct.unpack_from("<IIH", blob, 2)
        return Location(fidx, off, size)
    return None


def parse_rdb(data: bytes) -> list[RdbEntry]:
    if data[:8] != RDB_MAGIC:
        raise RdbError("not a _DRK0000 database")
    hsize, _unk, count = struct.unpack_from("<III", data, 8)
    entries: list[RdbEntry] = []
    off = hsize
    while off < len(data) and len(entries) < count:
        esz, psz, dsz, _pc, ktid, tktid, flags = _entry_header(data, off)
        blob = data[off + esz - psz:off + esz]
        entries.append(RdbEntry(len(entries), ktid, tktid, flags, dsz, parse_location(blob)))
        off += (esz + 3) & ~3
    if len(entries) != count:
        raise RdbError("expected %d entries, found %d" % (count, len(entries)))
    return entries


def parse_rdx(data: bytes) -> dict[int, FdataRef]:
    out: dict[int, FdataRef] = {}
    for pos in range(0, len(data) - 7, 8):
        idx, mount, _ff, ktid = struct.unpack_from("<HBBI", data, pos)
        out[idx] = FdataRef(idx, mount, ktid)
    return out


def decompress_chunked(payload: bytes, size: int) -> bytes:
    out = bytearray()
    pos = 0
    while pos + 2 <= len(payload):
        zsize = struct.unpack_from("<H", payload, pos)[0]
        if zsize == 0:
            break
        start = pos + 10
        if start + zsize > len(payload):
            raise RdbError("truncated zlib chunk")
        out += zlib.decompress(payload[start:start + zsize])
        pos = start + zsize
    if len(out) != size:
        raise RdbError("decompressed %d bytes, expected %d" % (len(out), size))
    return bytes(out)


def decode_stored_entry(stored: bytes, expect_ktid: int | None = None) -> bytes:
    esz, psz, dsz, _pc, ktid, _t, flags = _entry_header(stored, 0)
    if expect_ktid is not None and ktid != expect_ktid:
        raise RdbError("fdata entry KTID 0x%08x, expected 0x%08x" % (ktid, expect_ktid))
    payload = stored[esz - psz:esz]
    if flags & FLAG_CHUNKED_ZLIB:
        return decompress_chunked(payload, dsz)
    if len(payload) != dsz:
        raise RdbError("raw payload is %d bytes, expected %d" % (len(payload), dsz))
    return payload


def rdb_entry_offsets(data: bytes) -> list[int]:
    if data[:8] != RDB_MAGIC:
        raise RdbError("not a _DRK0000 database")
    hsize, _unk, count = struct.unpack_from("<III", data, 8)
    out, off = [], hsize
    while off < len(data) and len(out) < count:
        esz = _entry_header(data, off)[0]
        out.append(off)
        off += (esz + 3) & ~3
    if len(out) != count:
        raise RdbError("expected %d entries, found %d" % (count, len(out)))
    return out


def raw_fdata_entry(original: bytes, payload: bytes) -> bytes:
    esz, psz, _dsz, pcount, ktid, tktid, flags = _entry_header(original, 0)
    params = bytearray(original[ENTRY_HEADER.size:esz - psz])
    if len(params) >= 4:
        struct.pack_into("<I", params, 0, zlib.crc32(payload))
    size = ENTRY_HEADER.size + len(params) + len(payload)
    head = ENTRY_HEADER.pack(ENTRY_MAGIC, b"0000", size, len(payload), len(payload), pcount, ktid,
                             tktid, flags & ~FLAG_CHUNKED_ZLIB)
    return head + bytes(params) + payload


def stored_crc_ok(stored: bytes) -> bool:
    esz, psz = _entry_header(stored, 0)[:2]
    params = stored[ENTRY_HEADER.size:esz - psz]
    return len(params) >= 4 and struct.unpack_from("<I", params)[0] == zlib.crc32(stored[esz - psz:esz])


def patch_rdb_entry(rdb: bytearray, offset: int, fdata_index: int, fdata_offset: int,
                    stored_size: int, data_size: int) -> None:
    esz, psz, _dsz, _pc, _k, _t, flags = _entry_header(rdb, offset)
    if psz < 13 or rdb[offset + esz - psz:offset + esz - psz + 2] != LOCATION_FDATA:
        raise RdbError("root.rdb entry at 0x%x has no fdata location" % offset)
    struct.pack_into("<Q", rdb, offset + 0x18, data_size)
    struct.pack_into("<I", rdb, offset + 0x2C, flags & ~FLAG_CHUNKED_ZLIB)
    struct.pack_into("<IIH", rdb, offset + esz - psz + 2, fdata_offset, stored_size, fdata_index)


class ResourceDb:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        with open(os.path.join(base_dir, "root.rdb"), "rb") as f:
            self.entries = parse_rdb(f.read())
        with open(os.path.join(base_dir, "root.rdx"), "rb") as f:
            self.fdata = parse_rdx(f.read())
        self.by_ktid = {e.ktid: e for e in self.entries}
        self._files: dict[str, object] = {}

    def close(self) -> None:
        for f in self._files.values():
            f.close()
        self._files.clear()

    def __enter__(self) -> "ResourceDb":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fdata_ref(self, entry: RdbEntry) -> FdataRef | None:
        if entry.location is None:
            return None
        return self.fdata.get(entry.location.fdata_index)

    def fdata_path(self, entry: RdbEntry) -> str | None:
        ref = self.fdata_ref(entry)
        return None if ref is None else os.path.join(self.base_dir, *ref.relpath.split("/"))

    def has_data(self, entry: RdbEntry) -> bool:
        path = self.fdata_path(entry)
        return path is not None and os.path.isfile(path)

    def read_stored(self, entry: RdbEntry) -> bytes:
        path = self.fdata_path(entry)
        if path is None or not os.path.isfile(path):
            raise RdbError("no data file for KTID 0x%08x" % entry.ktid)
        f = self._files.get(path)
        if f is None:
            f = self._files[path] = open(path, "rb")
        loc = entry.location
        assert loc is not None
        f.seek(loc.offset)
        return f.read(loc.stored_size)

    def read(self, entry: RdbEntry) -> bytes:
        data = decode_stored_entry(self.read_stored(entry), entry.ktid)
        if len(data) != entry.size:
            raise RdbError("KTID 0x%08x: %d bytes, root.rdb says %d" % (entry.ktid, len(data), entry.size))
        return data

    def read_ktid(self, ktid: int) -> bytes:
        return self.read(self.by_ktid[ktid])

    def fdata_index_of(self, ktid: int, mount: int = ROOT_MOUNT) -> int:
        for ref in self.fdata.values():
            if ref.ktid == ktid and ref.mount == mount:
                return ref.index
        raise RdbError("root.rdx has no fdata 0x%08x" % ktid)
