import os
import random
import struct
import tempfile
import unittest
import zlib

import _support
from ldtool.container import (CHUNK_ALIGN, LATAM_MARKER, ContainerError, IdxEntry, LinkData,
                              StoredBlob, pack_container, parse_idx, serialize_idx,
                              unpack_container, write_linkdata)


def _random_text(n: int, seed: int) -> bytes:
    rng = random.Random(seed)
    words = [b"Pokemon", b"<br>", b"hola ", b"\xc2\xa1", b"isla ", b"nube ", b"\x00"]
    out = bytearray()
    while len(out) < n:
        out += rng.choice(words)
    return bytes(out[:n])


class ContainerTests(unittest.TestCase):
    def test_roundtrip_sizes(self):
        for n in (0, 1, 17, 4096, 0x10000 - 1, 0x10000, 0x10001, 3 * 0x10000 + 5):
            data = _random_text(n, n)
            blob = pack_container(data)
            self.assertEqual(len(blob) % CHUNK_ALIGN, 0)
            self.assertEqual(unpack_container(blob, n), data, n)

    def test_header_and_alignment(self):
        data = _random_text(0x10000 + 300, 1)
        blob = pack_container(data)
        bs, count, total = struct.unpack_from("<III", blob)
        self.assertEqual((bs, count, total), (0x10000, 2, len(data)))
        first = struct.unpack_from("<I", blob, 12)[0]
        self.assertEqual(struct.unpack_from("<I", blob, 128)[0] + 4, first)
        self.assertEqual(blob[132:134], b"\x78\xda")

    def test_incompressible_chunk_stored_raw(self):
        data = random.Random(5).randbytes(300)
        blob = pack_container(data)
        length = struct.unpack_from("<I", blob, 12)[0]
        self.assertEqual(length, len(data))
        self.assertEqual(blob[128:128 + len(data)], data)
        self.assertEqual(unpack_container(blob, len(data)), data)

    def test_tiny_chunk_raw(self):
        data = b"\x00" * 4 + b"abcdefghijklmn"
        blob = pack_container(data)
        self.assertEqual(struct.unpack_from("<I", blob, 12)[0], 18)
        self.assertEqual(unpack_container(blob, 18), data)

    def test_errors(self):
        blob = pack_container(b"x" * 1000)
        with self.assertRaises(ContainerError):
            unpack_container(blob, 999)
        with self.assertRaises(ContainerError):
            unpack_container(blob[:10])
        bad = bytearray(blob)
        struct.pack_into("<I", bad, 12, 7)
        with self.assertRaises((ContainerError, zlib.error)):
            unpack_container(bytes(bad), 1000)

    def test_idx_roundtrip(self):
        entries = [IdxEntry(0, 10, 128, 1), IdxEntry(256, 0, 0, 0), IdxEntry(256, 9, 9, 0)]
        self.assertEqual(parse_idx(serialize_idx(entries)), entries)
        with self.assertRaises(ContainerError):
            parse_idx(b"\x00" * 33)


class WriterTests(unittest.TestCase):
    def test_write_and_read(self):
        records = {1: b"hello" * 100, 4: _random_text(70000, 4), 5: b""}
        with tempfile.TemporaryDirectory() as tmp:
            idx, bin_ = os.path.join(tmp, "t.idx"), os.path.join(tmp, "t.bin")
            entries = write_linkdata(idx, bin_, 8, records,
                                     stored={6: StoredBlob(b"RAW!" * 3, 12, 0)})
            with LinkData(idx, bin_) as ld:
                self.assertEqual(len(ld), 8)
                for i, data in records.items():
                    self.assertEqual(ld.read(i), data)
                self.assertEqual(ld.read(6), b"RAW!" * 3)
                for i in (0, 2, 3, 7):
                    self.assertTrue(ld.entry(i).is_empty)
                    self.assertEqual(ld.read(i), b"")
            self.assertEqual(entries[5].stored_size, 128)
            for e in entries:
                self.assertEqual(e.offset % 256, 0)
            self.assertEqual(entries[0].offset, 0)
            self.assertEqual(entries[2].offset, entries[3].offset)
            self.assertEqual(entries[7].offset, os.path.getsize(bin_))
            self.assertEqual(os.path.getsize(bin_) % 256, 0)

    def test_marked_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, bin_ = os.path.join(tmp, "t.idx"), os.path.join(tmp, "t.bin")
            write_linkdata(idx, bin_, 4, {}, stored={
                1: StoredBlob(b"latam", 5 | LATAM_MARKER << 32, 0),
                2: StoredBlob(b"moved", 5 | LATAM_MARKER << 32, 3 << 32),
                3: StoredBlob(b"plain", 5, 0)})
            with LinkData(idx, bin_) as ld:
                e1, e2, e3 = ld.entry(1), ld.entry(2), ld.entry(3)
                self.assertEqual((e1.marker, e1.real_size, e1.target), (LATAM_MARKER, 5, 0))
                self.assertEqual((e2.marker, e2.target, e2.compressed), (LATAM_MARKER, 3, False))
                self.assertEqual((e3.marker, e3.target), (0, 0))
                self.assertEqual([ld.read(i) for i in (1, 2, 3)], [b"latam", b"moved", b"plain"])
        self.assertEqual(LATAM_MARKER.to_bytes(4, "big"), b"LATM")

    def test_index_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx, bin_ = os.path.join(tmp, "t.idx"), os.path.join(tmp, "t.bin")
            with self.assertRaises(ValueError):
                write_linkdata(idx, bin_, 3, {3: b"x"})
            self.assertFalse(os.path.exists(idx) or os.path.exists(bin_))


if __name__ == "__main__":
    unittest.main()
