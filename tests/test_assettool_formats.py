import os
import struct
import tempfile
import unittest

import _support
import _assetfixture as fx

try:
    import numpy
    import PIL
    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False

if HAS_IMAGING:
    from assettool import g1t as G
    from assettool.kidsobj import parse_objdb, texture_bindings
    from assettool.rdb import (FdataRef, RdbError, ResourceDb, decompress_chunked, parse_location,
                               parse_rdx)
    from assettool.sprites import parse_sprite_set


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class RdbTests(unittest.TestCase):
    def test_read_through_rdb_and_fdata(self):
        data_a = bytes(range(256)) * 3
        data_b = b"hello" * 100
        with tempfile.TemporaryDirectory() as tmp:
            base = fx.write_romfs(tmp, [(0x10, fx.TYPE_TEXTURE, data_a), (0x20, fx.TYPE_OBJDB, data_b)])
            with ResourceDb(base) as db:
                self.assertEqual([e.ktid for e in db.entries], [0x10, 0x20])
                self.assertEqual(db.entries[1].index, 1)
                self.assertEqual(db.read_ktid(0x10), data_a)
                self.assertEqual(db.read_ktid(0x20), data_b)
                self.assertEqual(db.fdata_ref(db.entries[0]).relpath, "0x%08x.fdata" % fx.FDATA_KTID)

    def test_mount_point_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = fx.write_romfs(tmp, [(0x10, fx.TYPE_TEXTURE, b"x" * 40)], fdata_mount=2)
            with ResourceDb(base) as db:
                ref = db.fdata_ref(db.entries[0])
                self.assertEqual(ref.relpath, "MountPoint_2/0x%08x.fdata" % fx.FDATA_KTID)
                self.assertTrue(db.has_data(db.entries[0]))
                self.assertEqual(db.read(db.entries[0]), b"x" * 40)

    def test_missing_fdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = fx.write_romfs(tmp, [(0x10, fx.TYPE_TEXTURE, b"abc" * 30)])
            os.remove(os.path.join(base, "0x%08x.fdata" % fx.FDATA_KTID))
            with ResourceDb(base) as db:
                self.assertFalse(db.has_data(db.entries[0]))
                with self.assertRaises(RdbError):
                    db.read(db.entries[0])

    def test_rdx_and_location(self):
        rdx = struct.pack("<HBBI", 0, 0xFF, 0xFF, 0x11C54) + struct.pack("<HBBI", 1, 3, 0xFF, 0x184B41)
        self.assertEqual(parse_rdx(rdx), {0: FdataRef(0, 0xFF, 0x11C54), 1: FdataRef(1, 3, 0x184B41)})
        loc = parse_location(b"\x01\x04" + struct.pack("<IIH", 0xA8880, 0xD7, 11) + b"\0")
        self.assertEqual((loc.fdata_index, loc.offset, loc.stored_size), (11, 0xA8880, 0xD7))
        self.assertIsNone(parse_location(b"\x01\x0c" + b"\0" * 11))

    def test_chunked_zlib_size_check(self):
        blob = fx.chunked_zlib(b"abcdef" * 50, chunk=64)
        self.assertEqual(decompress_chunked(blob, 300), b"abcdef" * 50)
        with self.assertRaises(RdbError):
            decompress_chunked(blob, 301)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class SwizzleTests(unittest.TestCase):
    def test_gob_block_height(self):
        for h, bh in ((1, 1), (8, 1), (9, 2), (16, 2), (17, 4), (64, 8), (128, 16), (1000, 16)):
            self.assertEqual(G.gob_block_height(h), bh, h)

    def test_known_offsets_16_byte_blocks(self):
        offs = G.block_linear_offsets(8, 16, 16)
        self.assertEqual(int(offs[0, 1]), 64)
        self.assertEqual(int(offs[1, 0]), 16)
        self.assertEqual(int(offs[2, 0]), 32)
        self.assertEqual(int(offs[4, 0]), 128)
        self.assertEqual(int(offs[0, 2]), 256)
        self.assertEqual(int(offs[8, 0]), 512)
        self.assertEqual(int(offs[0, 4]), 1024)

    def test_matches_reference_and_roundtrips(self):
        for w, h, bpe in ((4, 8, 16), (16, 8, 16), (8, 16, 8), (3, 5, 16), (32, 40, 4)):
            linear = bytes((i * 7 + 3) & 0xFF for i in range(w * h * bpe))
            swz = fx.ref_swizzle(linear, w, h, bpe)
            self.assertEqual(len(swz), G.swizzled_size(w, h, bpe))
            self.assertEqual(G.deswizzle(swz, w, h, bpe), linear, (w, h, bpe))

    def test_atlas_size(self):
        self.assertEqual(G.swizzled_size(256, 128, 16), 0x80000)

    def test_short_data(self):
        with self.assertRaises(G.G1tError):
            G.deswizzle(b"\0" * 100, 4, 4, 16)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class G1tTests(unittest.TestCase):
    def test_parse_and_decode_bc1(self):
        tex = fx.bc1_texture([[fx.RED, fx.GREEN], [fx.BLUE, fx.WHITE]])
        data = fx.g1t([tex])
        g = G.parse_g1t(data)
        self.assertEqual((g.version, g.platform, len(g.textures)), ("0600", 0x18, 1))
        t = g.textures[0]
        self.assertEqual((t.format_name, t.width, t.height, t.mips), ("BC1", 8, 8, 1))
        self.assertEqual(len(t.ext_header), 12)
        img = G.decode_texture(data, t)
        self.assertEqual(img.size, (8, 8))
        self.assertEqual(img.getpixel((1, 1)), (255, 0, 0, 255))
        self.assertEqual(img.getpixel((5, 1)), (0, 255, 0, 255))
        self.assertEqual(img.getpixel((1, 5)), (0, 0, 255, 255))
        self.assertEqual(img.getpixel((6, 6)), (255, 255, 255, 255))

    def test_no_extended_header_and_multi_texture(self):
        a, b = fx.bc1_texture([[fx.RED]]), fx.bc1_texture([[fx.BLUE, fx.GREEN]])
        data = fx.g1t([a, b], ext=False)
        g = G.parse_g1t(data)
        self.assertEqual([(t.width, t.height) for t in g.textures], [(4, 4), (8, 4)])
        self.assertEqual(g.textures[0].ext_header, b"")
        self.assertEqual(g.textures[0].data_end, g.textures[1].data_offset - 8)
        self.assertEqual(G.decode_texture(data, g.textures[1]).getpixel((6, 2)), (0, 255, 0, 255))

    def test_bad_extended_header(self):
        data = bytearray(fx.g1t([fx.bc1_texture([[fx.RED]])]))
        struct.pack_into("<I", data, 0x20 + 8, 0x14)
        with self.assertRaises(G.G1tError):
            G.parse_g1t(bytes(data))

    def test_unsupported_format(self):
        data = fx.g1t([fx.bc1_texture([[fx.RED]])], fmt=0x0A)
        t = G.parse_g1t(data).textures[0]
        with self.assertRaises(G.G1tError):
            G.decode_texture(data, t)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class RecordTests(unittest.TestCase):
    def test_objdb_bindings(self):
        data = fx.objdb(0x99, [fx.binding(1, 0xAAAA, 0xBBBB),
                               (2, 0x12345678, ((5, 2, 0x1),), [7, 8]),
                               fx.binding(3, 0, 0, 1)])
        db = parse_objdb(data)
        self.assertEqual((db.ktid, len(db.objects)), (0x99, 3))
        binds = texture_bindings(db, fx.OBJ_BINDING)
        self.assertEqual([(b.object_ktid, b.texture, b.sprite_set, b.alias) for b in binds],
                         [(1, 0xAAAA, 0xBBBB, 0), (3, 0, 0, 1)])

    def test_sprite_set(self):
        sprites = parse_sprite_set(fx.sprite_set([("a", 0, (0.0, 0.0, 1.0, 0.875)),
                                                  ("0030_prt_language_2", 32, (0.375, 0.8046875,
                                                                               0.462890625, 0.8359375))]))
        self.assertEqual([s.name for s in sprites], ["a", "0030_prt_language_2"])
        self.assertEqual(sprites[0].rect(1024, 512), (0, 0, 1024, 448))
        self.assertEqual(sprites[1].texture_index, 32)
        self.assertEqual(sprites[1].rect(4096, 2048), (1536, 1648, 1896, 1712))


if __name__ == "__main__":
    unittest.main()
