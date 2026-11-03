import os
import subprocess
import sys
import unittest

import _support
from ldtool.container import LinkData
from ldtool.gamedata import (FIRST_TABLE_RECORD, GROUP_COUNT, SLOT_COUNT, SPECIES_RECORD,
                             localized_records)
from ldtool.pipeline import compare_mod, roundtrip_records
from ldtool.table import parse_table


@_support.needs_romfs
class GameDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ld = LinkData.open_dir(_support.ROMFS)

    @classmethod
    def tearDownClass(cls):
        cls.ld.close()

    def test_archive_shape(self):
        self.assertEqual(len(self.ld), 12298)
        self.assertTrue(all(e.offset % 256 == 0 for e in self.ld.entries))

    def test_all_localized_records_roundtrip(self):
        res = roundtrip_records(self.ld)
        self.assertEqual(res["checked"], 3313)
        self.assertEqual(res["failures"], [])
        self.assertEqual(res["table_ok"], 3313)
        self.assertEqual(res["container_ok"], 3313)
        for slot in range(SLOT_COUNT):
            self.assertEqual(res["by_slot"][slot], GROUP_COUNT)
        self.assertEqual(res["by_slot"]["species"], 1)

    def test_groups_share_schema(self):
        for g in range(GROUP_COUNT):
            base = FIRST_TABLE_RECORD + SLOT_COUNT * g
            shapes = set()
            for slot in range(SLOT_COUNT):
                t = parse_table(self.ld.read(base + slot))
                shapes.add((t.count, t.mode, t.schema))
            self.assertEqual(len(shapes), 1, base)

    def test_known_rows(self):
        self.assertEqual(parse_table(self.ld.read(4668)).text(2), "Español (España)")
        self.assertEqual(parse_table(self.ld.read(2328)).text(79),
                         "¿Quieres jugar en español de España?")
        self.assertEqual(parse_table(self.ld.read(2324)).text(79),
                         "Are you sure you want to play in Spanish?")
        species = parse_table(self.ld.read(SPECIES_RECORD))
        self.assertEqual(species.texts(4)[:6],
                         ["フシギバナ", "Venusaur", "Florizarre", "Venusaur", "Bisaflor", "Venusaur"])

    def test_localized_record_list(self):
        recs = localized_records()
        self.assertEqual(len(recs), 3313)
        self.assertEqual(recs[0], SPECIES_RECORD)


@_support.needs_ptbr
class PtBrGroundTruthTests(unittest.TestCase):
    def test_ptbr_records(self):
        with LinkData.open_dir(_support.ROMFS) as src, LinkData.open_dir(_support.PTBR_ROMFS) as mod:
            res = compare_mod(src, mod)
        self.assertEqual(res["changed"], 269)
        self.assertEqual(res["localized"], 269)
        self.assertEqual(dict(res["slots"]), {1: 269})
        self.assertEqual(res["invalid"], [])
        self.assertEqual((res["exact"], res["stale"], res["reordered"]), (89, 178, 2))


class DefaultRomfsTests(unittest.TestCase):
    def defaults(self, **env: str) -> list[str]:
        clean = {k: v for k, v in os.environ.items() if k not in ("LDTOOL_ROMFS", "ASSETTOOL_ROMFS")}
        code = ("from ldtool import gamedata as l; from assettool import gamedata as a; "
                "print(l.DEFAULT_ROMFS); print(a.DEFAULT_ROMFS)")
        out = subprocess.run([sys.executable, "-c", code], cwd=_support.TOOLS_DIR, env=dict(clean, **env),
                             capture_output=True, text=True, check=True).stdout
        return out.splitlines()

    def test_boot_romfs_in_the_mod_folder(self):
        boot = os.path.join(_support.MOD_ROOT, "boot", "romfs")
        self.assertEqual(self.defaults(), [boot, boot])

    def test_environment_overrides(self):
        self.assertEqual(self.defaults(LDTOOL_ROMFS="x"), ["x", "x"])
        self.assertEqual(self.defaults(LDTOOL_ROMFS="x", ASSETTOOL_ROMFS="y"), ["x", "y"])


if __name__ == "__main__":
    unittest.main()
