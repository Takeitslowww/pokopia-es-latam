from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterable, Mapping

ENTRY = struct.Struct("<QQQQ")
ENTRY_SIZE = ENTRY.size
CHUNK_ALIGN = 128
DEFAULT_ENTRY_ALIGN = 256
DEFAULT_BLOCK_SIZE = 0x10000
DEFAULT_ZLIB_LEVEL = 9
LATAM_MARKER = 0x4C41544D

_U32 = struct.Struct("<I")


class ContainerError(ValueError):
    pass


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass(frozen=True)
class IdxEntry:
    offset: int
    size: int
    stored_size: int
    flag: int

    @property
    def is_empty(self) -> bool:
        return self.stored_size == 0

    @property
    def real_size(self) -> int:
        return self.size & 0xFFFFFFFF

    @property
    def marker(self) -> int:
        return self.size >> 32

    @property
    def compressed(self) -> bool:
        return bool(self.flag & 0xFFFFFFFF)

    @property
    def target(self) -> int:
        return self.flag >> 32

    def pack(self) -> bytes:
        return ENTRY.pack(self.offset, self.size, self.stored_size, self.flag)


def parse_idx(data: bytes) -> list[IdxEntry]:
    if len(data) % ENTRY_SIZE:
        raise ContainerError(f"IDX size {len(data)} is not a multiple of {ENTRY_SIZE}")
    return [IdxEntry(*ENTRY.unpack_from(data, pos)) for pos in range(0, len(data), ENTRY_SIZE)]


def read_idx(path: str | os.PathLike) -> list[IdxEntry]:
    with open(path, "rb") as fh:
        return parse_idx(fh.read())


def serialize_idx(entries: Iterable[IdxEntry]) -> bytes:
    return b"".join(e.pack() for e in entries)


def unpack_container(blob: bytes, expected_size: int | None = None) -> bytes:
    if len(blob) < 12:
        raise ContainerError("container shorter than its 12-byte header")
    block_size, count, total = struct.unpack_from("<III", blob, 0)
    if expected_size is not None and total != expected_size:
        raise ContainerError(f"container total {total} != IDX size {expected_size}")
    if block_size == 0:
        if total or count:
            raise ContainerError("container with block_size 0")
        return b""
    if count != (total + block_size - 1) // block_size:
        raise ContainerError(f"chunk count {count} inconsistent with total {total}/{block_size}")
    if 12 + 4 * count > len(blob):
        raise ContainerError("truncated chunk length table")
    lengths = struct.unpack_from(f"<{count}I", blob, 12)
    pos = align_up(12 + 4 * count, CHUNK_ALIGN)
    out = bytearray()
    for length in lengths:
        expect = min(block_size, total - len(out))
        if pos + length > len(blob):
            raise ContainerError("chunk runs past the end of the container")
        if length == expect:
            part = blob[pos:pos + length]
        elif length >= 4 and _U32.unpack_from(blob, pos)[0] + 4 == length:
            part = zlib.decompress(blob[pos + 4:pos + length])
        else:
            raise ContainerError(f"unrecognised chunk (length {length}, expected block {expect})")
        if len(part) != expect:
            raise ContainerError(f"chunk inflated to {len(part)} bytes, expected {expect}")
        out += part
        pos = align_up(pos + length, CHUNK_ALIGN)
    return bytes(out)


def pack_container(data: bytes, block_size: int = DEFAULT_BLOCK_SIZE,
                   level: int = DEFAULT_ZLIB_LEVEL) -> bytes:
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    count = (len(data) + block_size - 1) // block_size
    chunks = []
    for i in range(count):
        raw = data[i * block_size:(i + 1) * block_size]
        packed = zlib.compress(raw, level)
        if len(packed) + 4 < len(raw):
            chunks.append(_U32.pack(len(packed)) + packed)
        else:
            chunks.append(bytes(raw))
    out = bytearray(struct.pack("<III", block_size, count, len(data)))
    out += struct.pack(f"<{count}I", *(len(c) for c in chunks))
    out += bytes(align_up(len(out), CHUNK_ALIGN) - len(out))
    for chunk in chunks:
        out += chunk
        out += bytes(align_up(len(out), CHUNK_ALIGN) - len(out))
    return bytes(out)


class LinkData:
    def __init__(self, idx_path: str | os.PathLike, bin_path: str | os.PathLike):
        self.idx_path = os.fspath(idx_path)
        self.bin_path = os.fspath(bin_path)
        self.entries = read_idx(self.idx_path)
        self._fh: BinaryIO = open(self.bin_path, "rb")
        self.bin_size = os.fstat(self._fh.fileno()).st_size

    @classmethod
    def open_dir(cls, directory: str | os.PathLike, idx_name: str = "LI_C0_R.IDX",
                 bin_name: str = "LD_C0_R.BIN") -> "LinkData":
        return cls(os.path.join(directory, idx_name), os.path.join(directory, bin_name))

    def __len__(self) -> int:
        return len(self.entries)

    def __enter__(self) -> "LinkData":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    def entry(self, index: int) -> IdxEntry:
        return self.entries[index]

    def stored(self, index: int) -> bytes:
        e = self.entries[index]
        if e.is_empty:
            return b""
        if e.offset + e.stored_size > self.bin_size:
            raise ContainerError(f"entry {index} runs past the end of {self.bin_path}")
        self._fh.seek(e.offset)
        return self._fh.read(e.stored_size)

    def read(self, index: int) -> bytes:
        e = self.entries[index]
        if e.is_empty:
            return b""
        blob = self.stored(index)
        if e.compressed:
            return unpack_container(blob, e.real_size)
        if e.real_size != e.stored_size:
            raise ContainerError(f"raw entry {index}: size {e.real_size} != stored {e.stored_size}")
        return blob


@dataclass(frozen=True)
class StoredBlob:
    data: bytes
    size: int
    flag: int


def write_linkdata(idx_path: str | os.PathLike, bin_path: str | os.PathLike, entry_count: int,
                   records: Mapping[int, bytes], *,
                   stored: Mapping[int, StoredBlob] | None = None,
                   stored_source: Callable[[int], StoredBlob | None] | None = None,
                   entry_align: int = DEFAULT_ENTRY_ALIGN,
                   block_size: int = DEFAULT_BLOCK_SIZE,
                   level: int = DEFAULT_ZLIB_LEVEL) -> list[IdxEntry]:
    stored = stored or {}
    bad = [i for i in list(records) + list(stored) if not 0 <= i < entry_count]
    if bad:
        raise ValueError(f"indices outside the IDX (0..{entry_count - 1}): {sorted(bad)[:10]}")
    overlap = set(records) & set(stored)
    if overlap:
        raise ValueError(f"indices given both as record and stored blob: {sorted(overlap)[:10]}")

    idx_path, bin_path = os.fspath(idx_path), os.fspath(bin_path)
    tmp_bin, tmp_idx = bin_path + ".tmp", idx_path + ".tmp"
    entries: list[IdxEntry] = []
    try:
        with open(tmp_bin, "wb") as out:
            pos = 0
            for index in range(entry_count):
                if index in records:
                    data = records[index]
                    blob, size, flag = pack_container(data, block_size, level), len(data), 1
                else:
                    sb = stored.get(index)
                    if sb is None and stored_source is not None:
                        sb = stored_source(index)
                    if sb is None or not sb.data:
                        entries.append(IdxEntry(pos, 0, 0, sb.flag if sb is not None else 0))
                        continue
                    blob, size, flag = sb.data, sb.size, sb.flag
                entries.append(IdxEntry(pos, size, len(blob), flag))
                out.write(blob)
                end = align_up(pos + len(blob), entry_align)
                out.write(bytes(end - pos - len(blob)))
                pos = end
        with open(tmp_idx, "wb") as out:
            out.write(serialize_idx(entries))
        os.replace(tmp_bin, bin_path)
        os.replace(tmp_idx, idx_path)
    finally:
        for tmp in (tmp_bin, tmp_idx):
            if os.path.exists(tmp):
                os.remove(tmp)
    return entries
