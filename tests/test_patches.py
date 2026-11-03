import glob
import os
import struct
import sys
import unittest

import _support

sys.path.insert(0, os.path.join(_support.MOD_ROOT, "module"))
import patches

CACHE = os.path.join(_support.MOD_ROOT, "module", "build", "cache")


class PatchTableTests(unittest.TestCase):
    def test_branch_encoding(self):
        self.assertEqual(patches.branch("bl", 0x1000, 0x1008), struct.pack("<I", 0x94000002))
        self.assertEqual(patches.branch("b", 0x1008, 0x1000), struct.pack("<I", 0x17FFFFFE))
        with self.assertRaises(ValueError):
            patches.branch("b", 0, 1 << 27)

    def test_table_shape(self):
        names = [p.name for p in patches.PATCHES]
        self.assertEqual(len(names), len(set(names)))
        for p in patches.PATCHES:
            self.assertIn(p.kind, ("bytes", "b", "bl"), p.name)
            if p.kind == "bytes":
                self.assertEqual(len(p.new), len(p.original), p.name)
            else:
                self.assertEqual(len(p.original), 4, p.name)
                self.assertTrue(p.target.startswith("hook_"), p.name)
        release = patches.selected(debug=False)
        self.assertFalse(any(p.debug_only for p in release))
        self.assertGreater(len(patches.selected(debug=True)), len(release))

    def test_resolve_and_ips32(self):
        syms = {p.target: 0x100 + 4 * i for i, p in enumerate(patches.PATCHES) if p.target}
        resolved = patches.resolve(patches.selected(debug=False), syms)
        ips = patches.ips32([(p.offset, data) for p, data in resolved])
        self.assertTrue(ips.startswith(b"IPS32") and ips.endswith(b"EEOF"))
        pos, records = 5, []
        while ips[pos:pos + 4] != b"EEOF":
            offset, size = struct.unpack_from(">IH", ips, pos)
            records.append((offset, ips[pos + 6:pos + 6 + size]))
            pos += 6 + size
        self.assertEqual(pos + 4, len(ips))
        self.assertEqual(sorted(records),
                         sorted((patches.NSO_HEADER_SIZE + p.offset, d) for p, d in resolved))
        h1 = next(d for p, d in resolved if p.name == "H1_GRID_ENTRIES")
        word = struct.unpack("<I", h1)[0]
        delta = (word & 0x3FFFFFF) << 2
        self.assertEqual(0xFFA520 + delta, patches.MODULE_OFFSET + syms["hook_grid_entries"])

    @unittest.skipUnless(glob.glob(os.path.join(CACHE, "main-*.img")),
                         "no decompressed main cached (run module/build.py once)")
    def test_original_bytes_match_main(self):
        image = open(sorted(glob.glob(os.path.join(CACHE, "main-*.img")))[0], "rb").read()
        self.assertEqual(patches.check_original(patches.PATCHES, image), [])


if __name__ == "__main__":
    unittest.main()
