import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _support
import _assetfixture as fx

try:
    import numpy
    from PIL import Image
    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False

if HAS_IMAGING:
    from assettool import gamedata as gd
    from assettool.catalog import Fingerprint, build_catalog, fingerprint_textures, scale_factor
    from assettool.extract import extract
    from assettool.rdb import ResourceDb
    from assettool.sheet import contact_sheets

ASSET_ROOT = os.path.join(_support.ROMFS, "motor_asset_re", "KIDS_PLATFORM_OUNCE")
HAS_ASSETS = os.path.isfile(os.path.join(ASSET_ROOT, "root.rdb"))
PTBR_RDB = os.path.join(_support.PTBR_ROMFS, "motor_asset_re", "KIDS_PLATFORM_OUNCE", "root.rdb")


def quiet(_msg):
    pass


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class FingerprintTests(unittest.TestCase):
    def test_languages_and_scaled_offsets(self):
        for exp in (0, 8):
            s = scale_factor(exp)
            base = 0x12345678
            ktids = {l: (base + fx.OFFSETS[l] * s) % fx.M32 for l in fx.LANGS}
            known = set(ktids.values()) | {0xDEADBEEF}
            fps = fingerprint_textures(list(known), known)
            for lang, k in ktids.items():
                self.assertEqual(fps[k], Fingerprint(lang, exp, 8, base), (exp, lang))
            self.assertNotIn(0xDEADBEEF, fps)

    def test_too_few_votes(self):
        base = 0x0F000000
        known = {(base + fx.OFFSETS[l]) % fx.M32 for l in ("en", "es", "fr")}
        self.assertEqual(fingerprint_textures(list(known), known), {})


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class SyntheticExtractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.romfs = os.path.join(self.tmp, "romfs")
        self.base = fx.build_game(self.romfs)
        self.out = os.path.join(self.tmp, "assets")

    def tearDown(self):
        self._tmp.cleanup()

    def test_catalog(self):
        with ResourceDb(self.base) as db:
            cat = build_catalog(db, quiet)
        self.assertEqual(len(cat.language_sets), 1)
        self.assertEqual(sorted(cat.language_sets[0].packs), sorted(fx.LANGS))
        names = [a.name for a in cat.assets]
        self.assertEqual(names, ["banner__8000_txt_atlas", "language_select__0030_prt_language_2",
                                 "tutorial__still_tutorial_001"])
        by = {a.name: a for a in cat.assets}
        img1 = by["tutorial__still_tutorial_001"]
        self.assertEqual(img1.es.ktid, fx.lang_ktid(fx.IMG1, "es"))
        self.assertEqual(img1.en.ktid, fx.IMG1)
        self.assertEqual(img1.match, "ktid-offset")
        label = by["language_select__0030_prt_language_2"]
        self.assertEqual((label.kind, label.es.sub, label.es.crop), ("sprite", 1, (4, 0, 8, 4)))
        self.assertEqual(label.en.crop, (0, 0, 4, 4))
        self.assertEqual([s["texture"] for s in cat.shared], ["0x%08x" % fx.TEX_SHARED])

    def test_extract_files_and_manifest(self):
        counts = extract(self.romfs, self.out, log=quiet)
        self.assertEqual((counts["assets"], counts["es419_written"]), (3, 3))
        with open(os.path.join(self.out, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        self.assertEqual(man["source_language"], "es")
        self.assertEqual([m["file"] for m in man["movies_checked"]], ["movie_asset/ounce/MV01.webm"])
        a = {x["name"]: x for x in man["assets"]}["tutorial__still_tutorial_001"]
        self.assertEqual(a["es-ES"]["g1t"]["format_name"], "BC1")
        self.assertEqual(a["es-ES"]["ktid"], "0x%08x" % fx.lang_ktid(fx.IMG1, "es"))
        self.assertEqual(a["en"]["ktid"], "0x%08x" % fx.IMG1)
        self.assertEqual(a["es-ES"]["sprites"], [{"name": "still_tutorial_001", "rect": [0, 0, 8, 8]}])
        work = os.path.join(self.out, "es-419", "tutorial__still_tutorial_001.png")
        ref = os.path.join(self.out, "reference", "es-ES", "tutorial__still_tutorial_001.png")
        with open(work, "rb") as f1, open(ref, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())
        with Image.open(work) as im:
            self.assertEqual((im.size, im.getpixel((1, 1))), ((8, 8), (255, 0, 0, 255)))
        with Image.open(os.path.join(self.out, "reference", "en", "tutorial__still_tutorial_001.png")) as im:
            self.assertEqual(im.getpixel((1, 1)), (0, 0, 255, 255))
        with Image.open(os.path.join(self.out, "es-419", "language_select__0030_prt_language_2.png")) as im:
            self.assertEqual((im.size, im.getpixel((2, 2))), ((4, 4), (255, 0, 0, 255)))

    def test_rerun_keeps_edits_and_is_deterministic(self):
        extract(self.romfs, self.out, log=quiet)
        man_path = os.path.join(self.out, "manifest.json")
        with open(man_path, "rb") as f:
            first = f.read()
        work = os.path.join(self.out, "es-419", "tutorial__still_tutorial_001.png")
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(work)
        with open(work, "rb") as f:
            edited = f.read()
        counts = extract(self.romfs, self.out, log=quiet)
        self.assertEqual((counts["es419_kept"], counts["es419_edited"], counts["reference_written"]),
                         (3, 1, 0))
        with open(work, "rb") as f:
            self.assertEqual(f.read(), edited)
        with open(man_path, "rb") as f:
            self.assertEqual(f.read(), first)
        counts = extract(self.romfs, self.out, force=True, log=quiet)
        self.assertEqual(counts["es419_written"], 1)
        with open(work, "rb") as f1, open(work.replace("es-419", os.path.join("reference", "es-ES")), "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def snapshot(self, sub: str) -> dict:
        root = os.path.join(self.out, sub)
        out = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                with open(os.path.join(dirpath, name), "rb") as f:
                    out[os.path.relpath(os.path.join(dirpath, name), root)] = f.read()
        return out

    def test_reference_only_leaves_es419_and_manifest(self):
        extract(self.romfs, self.out, log=quiet)
        refs = self.snapshot("reference")
        work = os.path.join(self.out, "es-419")
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(os.path.join(work, "tutorial__still_tutorial_001.png"))
        os.remove(os.path.join(work, "banner__8000_txt_atlas.png"))
        man_path = os.path.join(self.out, "manifest.json")
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)
        by_name = {a["name"]: a for a in man["assets"]}
        by_name["tutorial__still_tutorial_001"]["en"]["ptbr_translated"] = True
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(man, f)
        with open(man_path, "rb") as f:
            committed = f.read()
        es419 = self.snapshot("es-419")
        os.remove(os.path.join(self.out, "reference", "en", "tutorial__still_tutorial_001.png"))
        os.remove(os.path.join(self.out, "reference", "es-ES", "banner__8000_txt_atlas.png"))
        counts = extract(self.romfs, self.out, log=quiet, working_copies=False)
        self.assertEqual((counts["reference_written"], counts["reference_mismatch"],
                          counts["manifest_written"], counts["es419_written"]), (2, 0, 0, 0))
        self.assertEqual(self.snapshot("reference"), refs)
        self.assertEqual(self.snapshot("es-419"), es419)
        with open(man_path, "rb") as f:
            self.assertEqual(f.read(), committed)

    def test_reference_only_without_manifest(self):
        logs = []
        counts = extract(self.romfs, self.out, log=logs.append, working_copies=False)
        self.assertEqual(counts["reference_written"], len(self.snapshot("reference")))
        self.assertEqual(counts["manifest_written"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, "es-419")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "manifest.json")))
        self.assertIn("es-419 untouched", logs[-1])

    def test_reference_only_reports_a_different_game(self):
        extract(self.romfs, self.out, log=quiet)
        man_path = os.path.join(self.out, "manifest.json")
        with open(man_path, encoding="utf-8") as f:
            man = json.load(f)
        man["assets"][1]["image"]["es_ES_sha256"] = "0" * 64
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(man, f)
        logs = []
        counts = extract(self.romfs, self.out, log=logs.append, working_copies=False)
        self.assertEqual(counts["reference_mismatch"], 1)
        self.assertTrue(any(m.startswith("warning: " + man["assets"][1]["name"]) for m in logs))
        from assettool.cli import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["extract", "--romfs", self.romfs, "--out", self.out,
                                   "--reference-only"]), 1)

    def test_missing_references(self):
        from assettool.extract import missing_references
        extract(self.romfs, self.out, log=quiet)
        self.assertEqual(missing_references(self.out), [])
        os.remove(os.path.join(self.out, "reference", "en", "tutorial__still_tutorial_001.png"))
        shutil.rmtree(os.path.join(self.out, "es-419"))
        self.assertEqual(missing_references(self.out), ["reference/en/tutorial__still_tutorial_001.png"])

    def test_cli_reference_only(self):
        from assettool.cli import main
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["extract", "--romfs", self.romfs, "--out", self.out,
                                   "--reference-only"]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "es-419")))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["extract", "--romfs", self.romfs, "--out", self.out, "--reference-only", "--force"])

    def test_contact_sheet(self):
        extract(self.romfs, self.out, log=quiet)
        paths = contact_sheets(self.out, os.path.join(self.tmp, "sheet.png"), per_sheet=2)
        self.assertEqual([os.path.basename(p) for p in paths], ["sheet.png", "sheet_2.png"])


@unittest.skipUnless(HAS_IMAGING and HAS_ASSETS, "game asset folder not found (%s)" % ASSET_ROOT)
class DumpCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = ResourceDb(ASSET_ROOT)
        cls.cat = build_catalog(cls.db, quiet)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_language_sets(self):
        self.assertEqual(len(self.cat.language_sets), 3)
        for s in self.cat.language_sets:
            self.assertEqual(sorted(s.packs), sorted(gd.LANGUAGES))
        packs = {s.packs["es"]: s.packs["en"] for s in self.cat.language_sets}
        self.assertEqual(packs.get(0xA11C0799), 0xDCAF5F9F)
        self.assertEqual(self.cat.notes, [])

    def test_assets(self):
        by = {a.name: a for a in self.cat.assets}
        self.assertEqual(len(by), 49)
        self.assertEqual(sum(1 for a in self.cat.assets if a.category == "tutorial"), 39)
        label = by["language_select__0030_prt_language_2"]
        self.assertEqual((label.es.ktid, label.es.sub, label.es.crop),
                         (0xBC2C37F5, 32, (1536, 1648, 1896, 1712)))
        still = by["tutorial__still_tutorial_236"]
        self.assertEqual((still.es.ktid, still.en.ktid), (0x0245E2B2, 0xFA9BD4B8))
        t = self.cat.g1t[still.es.ktid].textures[0]
        self.assertEqual((t.format_name, t.width, t.height), ("BC3", 1024, 512))

    @unittest.skipUnless(os.path.isfile(PTBR_RDB), "PT-BR mod not found")
    def test_ptbr_images_are_english_counterparts(self):
        from assettool.extract import ptbr_repointed
        ptbr = ptbr_repointed(_support.PTBR_ROMFS, self.db)
        english = {a.en.ktid for a in self.cat.assets if a.en is not None}
        self.assertEqual(len(ptbr), 40)
        self.assertEqual(ptbr - english, set())


if __name__ == "__main__":
    unittest.main()
