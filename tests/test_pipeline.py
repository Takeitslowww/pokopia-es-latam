import os
import shutil
import tempfile
import unittest

import json

import _support
from ldtool.container import LATAM_MARKER, LinkData
from ldtool.gamedata import SPANISH_SLOT, SPARE_FIRST_INDEX, table_records
from ldtool.pipeline import BuildConfig, PipelineError, build, extract, verify
from ldtool.po import read_po, write_po
from ldtool.table import parse_table

ALWAYS_ON = {
    "about": "test overrides",
    "rows": {
        "2324:80": {"text": "Are you sure you want to play in Spanish (Latin America)?",
                    "ints": [1]},
        "2328:80": {"text": "¿Quieres jugar en español de Latinoamérica?", "ints": [1]},
    },
}

TRANSLATIONS = {
    "table_4668.po": {"4668:2": "Español (Latinoamérica)"},
    "table_2328.po": {"2328:79": "¿Quieres jugar en español de Latinoamérica?",
                      "2328:210": "¿Quieres presionar un botón?<U+E01B>"},
    "table_4650.po": {"4650:1168:0": "celular", "4650:1168:2": "celulares"},
    "table_1474.po": {"1474:4:5": "Venusaur LatAm"},
}


def set_msgstr(po_dir, filename, values):
    path = os.path.join(po_dir, filename)
    po = read_po(path)
    found = 0
    for e in po.entries:
        if e.msgctxt in values:
            e.msgstr = values[e.msgctxt]
            found += 1
    assert found == len(values), (filename, values)
    write_po(path, po)


def write_always_on(directory, doc, name="test.json"):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)


@_support.needs_romfs
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ldtool-test-")
        cls.po_dir = os.path.join(cls.tmp, "po")
        extract(_support.ROMFS, cls.po_dir, log=_support.quiet)
        for name, values in TRANSLATIONS.items():
            set_msgstr(cls.po_dir, name, values)
        cls.always_on = os.path.join(cls.tmp, "always-on")
        write_always_on(cls.always_on, ALWAYS_ON)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def config(self, out, **kw):
        kw.setdefault("always_on_dir", self.always_on)
        return BuildConfig(romfs=_support.ROMFS, po_dir=self.po_dir,
                           out_dir=os.path.join(self.tmp, out), **kw)

    def test_extract_shape(self):
        files = sorted(os.listdir(self.po_dir))
        self.assertEqual(len(files), 369)
        po = read_po(os.path.join(self.po_dir, "table_4668.po"))
        self.assertEqual(po.header_value("Language"), "es_419")
        e = next(e for e in po.entries if e.msgctxt == "4668:2")
        self.assertEqual(e.msgid, "Español (España)")
        self.assertEqual(e.extracted_comments, ["en: Español (España)"])
        self.assertNotIn("4668:0", [x.msgctxt for x in po.entries])
        species = read_po(os.path.join(self.po_dir, "table_1474.po"))
        self.assertTrue(all(x.msgctxt.endswith(":5") for x in species.entries))
        en = next(x for x in species.entries if x.msgctxt == "1474:4:5")
        self.assertEqual(en.extracted_comments, ["en: Venusaur"])

    def test_patch_layout(self):
        cfg = self.config("out-patch")
        self.assertEqual(os.path.basename(cfg.idx_path), "LINKDATA_PATCH.IDX")
        self.assertEqual(os.path.basename(cfg.bin_path), "LINKDATA_PATCH.BIN")
        summary = build(cfg, log=_support.quiet)
        self.assertTrue(verify(cfg, log=_support.quiet))
        spare = SPARE_FIRST_INDEX
        self.assertEqual([(r["source"], r["index"], r["kind"]) for r in summary["records"]], [
            (1474, 1474, "latam"), (2324, 2324, "always-on"), (2328, 2328, "always-on"),
            (2328, spare, "latam"), (4650, 4650, "latam"), (4668, 4668, "latam")])
        with LinkData.open_dir(_support.ROMFS) as src, \
                LinkData(cfg.idx_path, cfg.bin_path) as out:
            self.assertEqual(len(out), 12298)
            used = {1474, 2324, 2328, spare, 4650, 4668}
            for i in range(len(out)):
                if i not in used:
                    self.assertTrue(out.entry(i).is_empty, i)
            for i in used:
                e = out.entry(i)
                self.assertFalse(e.compressed, i)
                self.assertEqual(e.real_size, e.stored_size, i)
            for i in (1474, 4650, 4668):
                self.assertEqual((out.entry(i).marker, out.entry(i).target), (LATAM_MARKER, 0))
            self.assertEqual(parse_table(out.read(4668)).text(2), "Español (Latinoamérica)")
            self.assertEqual(parse_table(out.read(1474)).texts(4)[4:6],
                             ["Bisaflor", "Venusaur LatAm"])
            for i in (2324, 2328):
                self.assertEqual((out.entry(i).marker, out.entry(i).target), (0, 0))
            en = parse_table(out.read(2324))
            self.assertEqual(en.text(80), ALWAYS_ON["rows"]["2324:80"]["text"])
            self.assertEqual(en.ints(80), [1])
            self.assertEqual(en.text(79), parse_table(src.read(2324)).text(79))
            es = parse_table(out.read(2328))
            self.assertEqual(es.text(79), "¿Quieres jugar en español de España?")
            self.assertEqual(es.text(80), "¿Quieres jugar en español de Latinoamérica?")
            self.assertEqual((out.entry(spare).marker, out.entry(spare).target),
                             (LATAM_MARKER, 2328))
            latam = parse_table(out.read(spare))
            self.assertEqual(latam.text(79), "¿Quieres jugar en español de Latinoamérica?")
            self.assertEqual(latam.text(210), "¿Quieres presionar un botón?")
            self.assertEqual((latam.text(80), latam.ints(80)), (es.text(80), [1]))

    def test_patch_layout_without_always_on(self):
        cfg = self.config("out-patch-plain", always_on_dir=None)
        summary = build(cfg, log=_support.quiet)
        self.assertTrue(verify(cfg, log=_support.quiet))
        self.assertEqual([(r["source"], r["index"]) for r in summary["records"]],
                         [(1474, 1474), (2328, 2328), (4650, 4650), (4668, 4668)])
        with LinkData(cfg.idx_path, cfg.bin_path) as out:
            self.assertEqual(out.entry(2328).marker, LATAM_MARKER)
        with self.assertRaises(PipelineError):
            build(self.config("out-patch-bad", map_spec="sequential:8953"), log=_support.quiet)

    def test_always_on_errors(self):
        bad_rows = [
            {"100:1": {"text": "x"}},
            {"2324:9999": {"text": "x"}},
            {"2324:80": {"ints": [1, 2]}},
            {"2324:80": {"colour": "x"}},
            {"2324:80:3": {"text": "x"}},
        ]
        for n, rows in enumerate(bad_rows):
            directory = os.path.join(self.tmp, f"always-on-bad{n}")
            write_always_on(directory, {"rows": rows})
            with self.assertRaises(PipelineError, msg=rows):
                build(self.config(f"out-always-on-bad{n}", always_on_dir=directory),
                      log=_support.quiet)

    def test_verify_detects_tampered_always_on(self):
        cfg = self.config("out-patch-tamper")
        build(cfg, log=_support.quiet)
        other = os.path.join(self.tmp, "always-on-tampered")
        doc = json.loads(json.dumps(ALWAYS_ON))
        doc["rows"]["2324:80"]["text"] = "Something else"
        write_always_on(other, doc)
        self.assertFalse(verify(self.config("out-patch-tamper", always_on_dir=other),
                                log=_support.quiet))

    def test_archive_layout(self):
        cfg = self.config("out-default", layout="archive")
        summary = build(cfg, log=_support.quiet)
        self.assertTrue(verify(cfg, log=_support.quiet))
        self.assertEqual(len(summary["records"]), 369)
        with LinkData.open_dir(_support.ROMFS) as src, \
                LinkData(cfg.idx_path, cfg.bin_path) as out:
            self.assertEqual(len(out), 12298)
            spanish = set(table_records(SPANISH_SLOT)) | {1474}
            for i in range(len(out)):
                if i not in spanish:
                    self.assertTrue(out.entry(i).is_empty, i)
            changed = {4668, 2328, 4650, 1474}
            for rec in spanish - changed:
                self.assertEqual(out.stored(rec), src.stored(rec), rec)
            self.assertEqual(parse_table(out.read(4668)).text(2), "Español (Latinoamérica)")
            t2328 = parse_table(out.read(2328))
            self.assertEqual(t2328.text(79), "¿Quieres jugar en español de Latinoamérica?")
            self.assertEqual(t2328.text(210), "¿Quieres presionar un botón?")
            items = parse_table(out.read(4650))
            self.assertEqual(items.texts(1168), ["celular", "", "celulares", "móvil", "", "móviles"])
            self.assertEqual(items.ints(1168), parse_table(src.read(4650)).ints(1168))
            species = parse_table(out.read(1474))
            self.assertEqual(species.texts(4)[4:6], ["Bisaflor", "Venusaur LatAm"])

    def test_spare_range_changed_only(self):
        cfg = self.config("out-spare", layout="archive", map_spec="sequential:8953",
                          idx_entries="compact", records="changed")
        summary = build(cfg, log=_support.quiet)
        self.assertTrue(verify(cfg, log=_support.quiet))
        self.assertEqual([r["source"] for r in summary["records"]], [1474, 2328, 4650, 4668])
        self.assertEqual([r["index"] for r in summary["records"]], [8953, 8954, 8955, 8956])
        with LinkData(cfg.idx_path, cfg.bin_path) as out:
            self.assertEqual(len(out), 8957)
            self.assertEqual(parse_table(out.read(8956)).text(2), "Español (Latinoamérica)")

    def test_append_serializer(self):
        cfg = self.config("out-append", layout="archive", serializer="append",
                          records="changed")
        build(cfg, log=_support.quiet)
        self.assertTrue(verify(cfg, log=_support.quiet))
        with LinkData.open_dir(_support.ROMFS) as src, \
                LinkData(cfg.idx_path, cfg.bin_path) as out:
            original, patched = src.read(4668), out.read(4668)
            self.assertEqual(patched[len(original):], "Español (Latinoamérica)".encode() + b"\0")

    def test_verify_detects_mismatch(self):
        cfg = self.config("out-tamper")
        build(cfg, log=_support.quiet)
        other = os.path.join(self.tmp, "po-tampered")
        shutil.copytree(self.po_dir, other)
        set_msgstr(other, "table_4668.po", {"4668:2": "Español (otro)"})
        cfg2 = BuildConfig(romfs=_support.ROMFS, po_dir=other, out_dir=cfg.out_dir,
                           always_on_dir=self.always_on)
        self.assertFalse(verify(cfg2, log=_support.quiet))

    def test_rejects_bad_targets(self):
        bad = os.path.join(self.tmp, "po-bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "x.po"), "w", encoding="utf-8") as fh:
            fh.write('msgctxt "1910:3"\nmsgid "a"\nmsgstr "b"\n')
        with self.assertRaises(PipelineError):
            build(BuildConfig(romfs=_support.ROMFS, po_dir=bad,
                              out_dir=os.path.join(self.tmp, "out-bad")), log=_support.quiet)
        with open(os.path.join(bad, "x.po"), "w", encoding="utf-8") as fh:
            fh.write('msgctxt "4668:99"\nmsgid "a"\nmsgstr "b"\n')
        with self.assertRaises(PipelineError):
            build(BuildConfig(romfs=_support.ROMFS, po_dir=bad,
                              out_dir=os.path.join(self.tmp, "out-bad")), log=_support.quiet)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out-bad")))

    def test_reextract_keeps_translations(self):
        po_dir = os.path.join(self.tmp, "po-merge")
        shutil.copytree(self.po_dir, po_dir)
        path = os.path.join(po_dir, "table_2328.po")
        po = read_po(path)
        for e in po.entries:
            if e.msgctxt == "2328:210":
                e.msgid = "texto viejo"
        write_po(path, po)
        extract(_support.ROMFS, po_dir, records=[2328], log=_support.quiet)
        merged = {e.msgctxt: e for e in read_po(path).entries}
        self.assertEqual(merged["2328:79"].msgstr, "¿Quieres jugar en español de Latinoamérica?")
        self.assertFalse(merged["2328:79"].fuzzy)
        self.assertTrue(merged["2328:210"].fuzzy)
        self.assertEqual(merged["2328:210"].previous, ['msgid "texto viejo"'])
        self.assertEqual(merged["2328:210"].msgid, "¿Quieres pulsar un botón?")


if __name__ == "__main__":
    unittest.main()
