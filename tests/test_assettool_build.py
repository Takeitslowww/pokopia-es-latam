import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import _support
import _assetfixture as fx

try:
    import numpy as np
    from PIL import Image
    HAS_IMAGING = True
except ImportError:
    HAS_IMAGING = False

if HAS_IMAGING:
    from assettool import bcn, build as abuild, extract as asset_extract, gamedata as gd
    from assettool.extract import extract
    from assettool.g1t import (decode_texture, deswizzle, encode_texture, level_layout, parse_g1t,
                               swizzle)
    from assettool.rdb import (FLAG_CHUNKED_ZLIB, ResourceDb, decode_stored_entry, raw_fdata_entry,
                               stored_crc_ok)
    from assettool.sprites import parse_sprite_set

    sys.path.insert(0, os.path.join(_support.MOD_ROOT, "scripts"))
    import build_all

LABEL = "language_select__0030_prt_language_2"
TUTORIAL = "tutorial__still_tutorial_001"
ASSET_ROOT = os.path.join(_support.ROMFS, "motor_asset_re", "KIDS_PLATFORM_OUNCE")
HAS_ASSETS = os.path.isfile(os.path.join(ASSET_ROOT, "root.rdb"))
MOD_ASSETS = os.path.join(_support.MOD_ROOT, "assets")


def quiet(_msg):
    pass


def psnr(a, b) -> float:
    d = np.asarray(a, np.float64) - np.asarray(b, np.float64)
    mse = float((d * d).mean())
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def pil_decode(fmt: int, blocks: np.ndarray, w: int, h: int) -> np.ndarray:
    n = {0x60: 1, 0x62: 3, 0x66: 7}[fmt]
    return np.array(Image.frombytes("RGBA", (w, h), blocks.tobytes(), "bcn", n))


def test_image(w: int = 32, h: int = 32) -> np.ndarray:
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    rng = np.random.default_rng(1)
    img = np.stack([x / w * 255, y / h * 255, (x + y) / (w + h) * 255, 255 - x / w * 200], -1)
    img[..., :3] += rng.normal(0, 4, (h, w, 3))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def merged_db(game_base: str, out_romfs: str, dest: str) -> "ResourceDb":
    os.makedirs(dest, exist_ok=True)
    out = os.path.join(out_romfs, "motor_asset_re", "KIDS_PLATFORM_OUNCE")
    for name in os.listdir(out):
        shutil.copyfile(os.path.join(out, name), os.path.join(dest, name))
    shutil.copyfile(os.path.join(game_base, "root.rdx"), os.path.join(dest, "root.rdx"))
    return ResourceDb(dest)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class EncoderTests(unittest.TestCase):
    def test_roundtrip_through_pillow(self):
        img = test_image()
        opaque = img.copy()
        opaque[..., 3] = 255
        blocks = bcn.image_blocks(img)
        for fmt, src, alpha_db in ((0x60, opaque, 99), (0x62, img, 45), (0x66, img, 38)):
            enc = bcn.encode_blocks(fmt, bcn.image_blocks(src))
            self.assertEqual(enc.shape, (len(blocks), bcn.ENCODERS[fmt][1]))
            got = pil_decode(fmt, enc, 32, 32)
            self.assertGreater(psnr(src, got), 30, hex(fmt))
            self.assertGreater(bcn.compare(src, got)["psnr"], 32, hex(fmt))
            self.assertGreaterEqual(psnr(src[..., 3], got[..., 3]), alpha_db, hex(fmt))

    def test_solid_colours(self):
        for colour in ((255, 0, 0, 255), (0, 0, 0, 0), (255, 255, 255, 255), (0, 255, 0, 255)):
            img = np.tile(np.array(colour, np.uint8), (4, 8, 1))
            for fmt in (0x60, 0x62, 0x66):
                got = pil_decode(fmt, bcn.encode_blocks(fmt, bcn.image_blocks(img)), 8, 4)
                if colour[3] == 0:
                    self.assertTrue((got[..., 3] == 0).all(), (hex(fmt), colour))
                    continue
                err = np.abs(got.astype(int) - img).max()
                self.assertLessEqual(err, 1 if fmt == 0x66 else 0, (hex(fmt), colour))

    def test_bc1_punch_through_alpha(self):
        img = np.zeros((4, 4, 4), np.uint8)
        img[:2] = (10, 200, 30, 255)
        got = pil_decode(0x60, bcn.encode_blocks(0x60, bcn.image_blocks(img)), 4, 4)
        self.assertTrue((got[:2, :, 3] == 255).all() and (got[2:, :, 3] == 0).all())

    def test_unknown_format(self):
        with self.assertRaises(bcn.BcnError):
            bcn.encode_blocks(0x63, np.zeros((1, 16, 4), np.uint8))


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class SwizzleTests(unittest.TestCase):
    def test_matches_reference_and_inverts(self):
        rng = np.random.default_rng(2)
        for w, h, bpe in ((1, 1, 8), (5, 3, 16), (16, 20, 8), (33, 70, 16), (130, 9, 16)):
            lin = rng.integers(0, 256, w * h * bpe, dtype=np.uint8).tobytes()
            sw = swizzle(lin, w, h, bpe)
            self.assertEqual(sw, fx.ref_swizzle(lin, w, h, bpe), (w, h, bpe))
            self.assertEqual(deswizzle(sw, w, h, bpe), lin, (w, h, bpe))


def mip_g1t(levels: list[list[list[int]]]) -> bytes:
    data = b"".join(fx.bc1_texture(lv)[2] for lv in levels)
    w, h, _ = fx.bc1_texture(levels[0])
    raw = bytearray(fx.g1t([(w, h, data)]))
    raw[0x1C + 4] = len(levels) << 4
    return bytes(raw)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class EncodeTextureTests(unittest.TestCase):
    def setUp(self):
        colours = [[fx.RED, fx.GREEN, fx.BLUE, fx.WHITE], [fx.BLACK, fx.RED, fx.GREEN, fx.BLUE]]
        self.data = fx.g1t([fx.bc1_texture(colours)])
        self.tex = parse_g1t(self.data).textures[0]
        self.img = np.array(decode_texture(self.data, self.tex))

    def linear(self, region: bytes) -> np.ndarray:
        return np.frombuffer(deswizzle(region, 4, 2, 8), np.uint8).reshape(8, 8)

    def test_unchanged_image_keeps_every_byte(self):
        res = encode_texture(self.data, self.tex, self.img)
        self.assertEqual((res.blocks_encoded, res.blocks_kept), (0, 8))
        self.assertEqual(res.data, self.data[self.tex.data_offset:self.tex.data_end])

    def test_only_changed_blocks_are_encoded(self):
        img = self.img.copy()
        img[4:8, 8:12] = (255, 255, 0, 255)
        res = encode_texture(self.data, self.tex, img)
        self.assertEqual((res.blocks_encoded, res.blocks_kept), (1, 7))
        self.assertEqual(res.roundtrip["max_abs"], 0)
        old = self.linear(self.data[self.tex.data_offset:self.tex.data_end])
        new = self.linear(res.data)
        self.assertEqual([i for i in range(8) if (old[i] != new[i]).any()], [6])
        out = self.data[:self.tex.data_offset] + res.data
        np.testing.assert_array_equal(np.array(decode_texture(out, self.tex)), img)

    def test_force_reencodes_unchanged_blocks(self):
        force = np.zeros((2, 4), bool)
        force[0, :2] = True
        res = encode_texture(self.data, self.tex, self.img, force)
        self.assertEqual(res.blocks_encoded, 2)
        out = self.data[:self.tex.data_offset] + res.data
        np.testing.assert_array_equal(np.array(decode_texture(out, self.tex)), self.img)

    def test_mips_are_rebuilt_from_mip0(self):
        g = [[fx.GREEN] * 4] * 4
        data = mip_g1t([g, [[fx.GREEN] * 2] * 2, [[fx.GREEN]]])
        tex = parse_g1t(data).textures[0]
        layout = level_layout(tex)
        self.assertEqual([(w, h, size) for w, h, _o, size in layout],
                         [(16, 16, 512), (8, 8, 512), (4, 4, 512)])
        img = np.array(decode_texture(data, tex))
        img[:, :8] = (255, 0, 0, 255)
        res = encode_texture(data, tex, img)
        for w, h, off, size in layout[1:]:
            lin = np.frombuffer(deswizzle(res.data[off:off + size], (w + 3) // 4, (h + 3) // 4, 8),
                                np.uint8)
            got = pil_decode(0x60, lin, w, h)
            want = np.array(Image.fromarray(img).resize((w, h), Image.BOX))
            np.testing.assert_array_equal(got, want, err_msg="mip %dx%d" % (w, h))

    def test_wrong_size(self):
        from assettool.g1t import G1tError
        with self.assertRaises(G1tError):
            encode_texture(self.data, self.tex, np.zeros((4, 4, 4), np.uint8))


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class RdbWriteTests(unittest.TestCase):
    def test_raw_fdata_entry(self):
        payload = fx.chunked_zlib(b"hello world" * 20)
        import zlib
        params = struct.pack("<I", zlib.crc32(payload)) + b"\x07" * 4
        stored = fx.idrk(0x1234, 0x5678, 0x00400000, 220, params, payload)
        self.assertTrue(stored_crc_ok(stored))
        new = raw_fdata_entry(stored, b"new data")
        self.assertTrue(stored_crc_ok(new))
        self.assertEqual(decode_stored_entry(new, 0x1234), b"new data")
        self.assertEqual(new[0x34:0x38], b"\x07" * 4)
        flags = struct.unpack_from("<I", new, 0x2C)[0]
        self.assertEqual(flags & FLAG_CHUNKED_ZLIB, 0)
        self.assertFalse(stored_crc_ok(stored[:0x30] + b"\0" * 8 + payload))

    def test_kt_hash(self):
        self.assertEqual(gd.kt_hash("0030_prt_language_2"), 0x14B92EA6)
        self.assertEqual(gd.kt_hash("0030_prt_language_3"), 0xFC5B0505)
        self.assertEqual(gd.kt_hash("still_tutorial_001"), fx.name_hash("still_tutorial_001"))


FIXTURE_CONSTANTS = dict(LABEL_ATLAS=fx.TEX_ATLAS, LABEL_ATLAS_SUB=1, LABEL_SPRITE_SET=fx.SSET_LABEL,
                         LATAM_LABEL_RECT=(0, 8, 4, 12), HOST_FDATA=fx.FDATA_KTID)


@unittest.skipUnless(HAS_IMAGING, "numpy/Pillow not installed")
class SyntheticBuildTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.multiple(gd, **FIXTURE_CONSTANTS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name
        self.romfs = os.path.join(self.tmp, "romfs")
        self.base = fx.build_game(self.romfs)
        self.label_record = fx.write_label_table(self.romfs)
        self.assets = os.path.join(self.tmp, "assets")
        extract(self.romfs, self.assets, log=quiet)
        self.logs: list[str] = []

    def work(self, name: str) -> str:
        return os.path.join(self.assets, "es-419", name + ".png")

    def edit(self, name: str, colour) -> np.ndarray:
        with Image.open(self.work(name)) as im:
            img = np.array(im.convert("RGBA"))
        img[...] = colour
        Image.fromarray(img).save(self.work(name))
        return img

    def build(self, out: str = "out", **kw) -> dict:
        return abuild.build(self.romfs, self.assets, os.path.join(self.tmp, out),
                            log=self.logs.append, **kw)

    def out_dir(self, out: str = "out") -> str:
        return os.path.join(self.tmp, out, "motor_asset_re", "KIDS_PLATFORM_OUNCE")

    def row(self, record: bytes, i: int):
        return struct.unpack_from("<IHH", record, 64 + 8 * i)

    def test_manifest_marks_the_label(self):
        with open(os.path.join(self.assets, "manifest.json"), encoding="utf-8") as f:
            man = {a["name"]: a for a in json.load(f)["assets"]}
        self.assertEqual(man[LABEL]["latam_label"]["sprite"], "0030_prt_language_3")
        self.assertNotIn("latam_label", man[TUTORIAL])

    def test_noop_installs_nothing_and_removes_stale_outputs(self):
        os.makedirs(self.out_dir())
        with open(os.path.join(self.out_dir(), "root.rdb"), "wb") as f:
            f.write(b"stale")
        s = self.build()
        self.assertEqual((s["files"], s["label"], s["global_replacements"]), ([], False, []))
        self.assertEqual(len(s["removed"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out", "motor_asset_re")))
        rec = s["linkdata_records"][gd.LABEL_TABLE_RECORD]
        self.assertEqual(self.row(rec, 0x102), self.row(rec, 0x101))
        self.assertEqual(rec[:64 + 8 * 0x102], self.label_record[:64 + 8 * 0x102])
        self.assertTrue(any("nothing installed" in m for m in self.logs))

    def test_label_becomes_a_new_sprite(self):
        label = self.edit(LABEL, (0, 0, 255, 255))
        s = self.build()
        self.assertTrue(s["label"])
        self.assertEqual(s["global_replacements"], [])
        self.assertFalse(any("replaces its es-ES texture" in m for m in self.logs))
        rec = s["linkdata_records"][gd.LABEL_TABLE_RECORD]
        self.assertEqual(self.row(rec, 0x102), (fx.name_hash("0030_prt_language_3"), 10, 0))
        self.assertEqual(self.row(rec, 0x101), (fx.name_hash("0030_prt_language_2"), 10, 0))
        self.assertEqual(sorted(os.listdir(self.out_dir())), ["0x00011c54.fdata", "root.rdb"])
        with ResourceDb(self.base) as old, merged_db(self.base, os.path.join(self.tmp, "out"),
                                                     os.path.join(self.tmp, "check")) as new:
            sprites = parse_sprite_set(new.read_ktid(fx.SSET_LABEL))
            self.assertEqual([x.name for x in sprites][-1], "0030_prt_language_3")
            self.assertEqual(len(sprites), len(parse_sprite_set(old.read_ktid(fx.SSET_LABEL))) + 1)
            self.assertEqual(sprites[-1].rect(16, 16), (0, 8, 4, 12))
            a_old, a_new = old.read_ktid(fx.TEX_ATLAS), new.read_ktid(fx.TEX_ATLAS)
            t = parse_g1t(a_new).textures[1]
            before = np.array(decode_texture(a_old, parse_g1t(a_old).textures[1]))
            after = np.array(decode_texture(a_new, t))
            np.testing.assert_array_equal(after[8:12, 0:4], label)
            after[8:12, 0:4] = before[8:12, 0:4]
            np.testing.assert_array_equal(after, before)
            for e in new.entries:
                if e.ktid not in (fx.SSET_LABEL, fx.TEX_ATLAS):
                    self.assertEqual(new.read(e), old.read(old.by_ktid[e.ktid]))

    def test_other_edits_replace_globally_with_a_warning(self):
        img = self.edit(TUTORIAL, (0, 0, 255, 255))
        s = self.build()
        self.assertEqual((s["global_replacements"], s["label"]), ([TUTORIAL], False))
        self.assertTrue(any(TUTORIAL in m and "Español (España) included" in m for m in self.logs))
        ktid = fx.lang_ktid(fx.IMG1, "es")
        with merged_db(self.base, os.path.join(self.tmp, "out"), os.path.join(self.tmp, "check")) as db:
            data = db.read_ktid(ktid)
            np.testing.assert_array_equal(np.array(decode_texture(data, parse_g1t(data).textures[0])), img)

    def test_deterministic(self):
        self.edit(LABEL, (0, 0, 255, 255))
        self.edit(TUTORIAL, (255, 255, 255, 255))
        self.build("a")
        self.build("b")
        self.build("a")
        files = sorted(os.listdir(self.out_dir("a")))
        self.assertEqual(files, sorted(os.listdir(self.out_dir("b"))))
        for name in files:
            with open(os.path.join(self.out_dir("a"), name), "rb") as f1, \
                    open(os.path.join(self.out_dir("b"), name), "rb") as f2:
                self.assertEqual(f1.read(), f2.read(), name)

    def test_reencode_unchanged_label(self):
        s = self.build(reencode=(LABEL,))
        self.assertEqual((s["reencoded"], s["label"], s["global_replacements"]), ([LABEL], False, []))
        self.assertEqual(s["textures"][0]["blocks_encoded"], 1)
        with ResourceDb(self.base) as old, merged_db(self.base, os.path.join(self.tmp, "out"),
                                                     os.path.join(self.tmp, "check")) as new:
            a_old, a_new = old.read_ktid(fx.TEX_ATLAS), new.read_ktid(fx.TEX_ATLAS)
            self.assertEqual(np.array(decode_texture(a_new, parse_g1t(a_new).textures[1])).tolist(),
                             np.array(decode_texture(a_old, parse_g1t(a_old).textures[1])).tolist())

    def test_errors(self):
        Image.new("RGBA", (5, 4)).save(self.work(LABEL))
        with self.assertRaises(abuild.BuildError):
            self.build()
        extract(self.romfs, self.assets, force=True, log=quiet)
        with self.assertRaises(abuild.BuildError):
            self.build(reencode=("no_such_asset",))
        with self.assertRaises(abuild.BuildError):
            abuild.build(self.romfs, self.assets, self.romfs, log=quiet)

    def test_label_area_must_be_free(self):
        self.edit(LABEL, (0, 0, 255, 255))
        with mock.patch.object(gd, "LATAM_LABEL_RECT", (0, 4, 4, 8)):
            with self.assertRaises(abuild.BuildError):
                self.build()

    def test_missing_working_copies_are_unchanged_and_silent(self):
        shutil.rmtree(os.path.join(self.assets, "es-419"))
        s = self.build()
        self.assertEqual((s["files"], s["label"], s["global_replacements"], s["warnings"]),
                         ([], False, [], []))
        self.assertFalse(any("warning" in m or "missing" in m for m in self.logs))

    def test_only_edited_copies_are_needed(self):
        img = self.edit(TUTORIAL, (0, 0, 255, 255))
        for name in os.listdir(os.path.join(self.assets, "es-419")):
            if name != TUTORIAL + ".png":
                os.remove(os.path.join(self.assets, "es-419", name))
        s = self.build()
        self.assertEqual((s["global_replacements"], s["label"], s["warnings"]), ([TUTORIAL], False, []))
        ktid = fx.lang_ktid(fx.IMG1, "es")
        with merged_db(self.base, os.path.join(self.tmp, "out"), os.path.join(self.tmp, "check")) as db:
            data = db.read_ktid(ktid)
            np.testing.assert_array_equal(np.array(decode_texture(data, parse_g1t(data).textures[0])), img)

    def test_build_all_extracts_missing_references(self):
        self.edit(LABEL, (0, 0, 255, 255))
        with open(self.work(LABEL), "rb") as f:
            label = f.read()
        os.remove(self.work(TUTORIAL))
        shutil.rmtree(os.path.join(self.assets, "reference"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(build_all.ensure_references(self.romfs, self.assets))
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertEqual(asset_extract.missing_references(self.assets), [])
        self.assertEqual(sorted(os.listdir(os.path.join(self.assets, "es-419"))),
                         sorted([LABEL + ".png", "banner__8000_txt_atlas.png"]))
        with open(self.work(LABEL), "rb") as f:
            self.assertEqual(f.read(), label)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(build_all.ensure_references(self.romfs, self.assets))
        self.assertEqual(out.getvalue(), "")

    def fake_module(self) -> dict:
        paths = {"ips": os.path.join(self.tmp, "main.ips"), "subsdk9": os.path.join(self.tmp, "subsdk9")}
        for key, path in paths.items():
            with open(path, "wb") as f:
                f.write(key.encode() * 3)
        return dict(paths, variant="release")

    def stage(self, staging: str) -> None:
        romfs_out = build_all.staging_romfs(staging)
        abuild.build(self.romfs, self.assets, romfs_out, log=quiet)
        os.makedirs(romfs_out, exist_ok=True)
        for name in ("LINKDATA_PATCH.IDX", "LINKDATA_PATCH.BIN"):
            with open(os.path.join(romfs_out, name), "wb") as f:
                f.write(name.encode())

    def test_package_layout(self):
        self.edit(LABEL, (0, 0, 255, 255))
        staging = os.path.join(self.tmp, "staging")
        self.stage(staging)
        module = self.fake_module()
        zip_path = os.path.join(self.tmp, "dist", "pokopia-es-latam.zip")
        entries = build_all.write_package(staging, module, zip_path)
        mod = "0400A3A01EE4C000/ES-LATAM/"
        want = [mod + "exefs/%s.ips" % build_all.patches.BUILD_ID,
                mod + "romfs/LINKDATA_PATCH.BIN", mod + "romfs/LINKDATA_PATCH.IDX",
                mod + "romfs/motor_asset_re/KIDS_PLATFORM_OUNCE/0x00011c54.fdata",
                mod + "romfs/motor_asset_re/KIDS_PLATFORM_OUNCE/root.rdb",
                "LEEME.txt", "exefs/subsdk9"]
        self.assertEqual(sorted(a for a, _ in entries), sorted(want))
        with zipfile.ZipFile(zip_path) as zf:
            self.assertEqual(sorted(zf.namelist()), sorted(want))
            self.assertEqual({i.filename: i.file_size for i in zf.infolist()}, dict(entries))
            self.assertEqual(zf.read("exefs/subsdk9"), b"subsdk9" * 3)
            self.assertEqual(zf.read(want[0]), b"ips" * 3)
            with open(build_all.LEEME, "rb") as f:
                self.assertEqual(zf.read("LEEME.txt"), f.read())

    def test_package_without_image_changes_has_no_asset_files(self):
        staging = os.path.join(self.tmp, "staging")
        self.stage(staging)
        entries = build_all.write_package(staging, self.fake_module(),
                                          os.path.join(self.tmp, "p.zip"))
        self.assertFalse(any("motor_asset_re" in a for a, _ in entries))
        self.assertEqual(len(entries), 5)

    def test_package_option(self):
        zip_path = os.path.join(self.tmp, "dist", "pokopia-es-latam.zip")
        os.makedirs(os.path.dirname(zip_path))
        with open(zip_path, "wb") as f:
            f.write(b"old")
        module = self.fake_module()
        seen = []

        def fake_parts(romfs, romfs_out, debug, script):
            staging = os.path.dirname(os.path.dirname(os.path.dirname(romfs_out)))
            self.assertEqual(build_all.staging_romfs(staging), romfs_out)
            seen.append((romfs, debug, script, staging))
            self.stage(staging)
            return {"assets": {}, "text": None, "module": module}

        with mock.patch.object(build_all, "build_parts", fake_parts), \
                mock.patch.object(build_all, "PACKAGE_ZIP", zip_path), \
                mock.patch.object(build_all, "emulator_running", return_value=True) as running, \
                mock.patch.object(build_all, "install") as install, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(build_all.main(["--package", "--romfs", self.romfs]), 0)
        self.assertEqual([x[:3] for x in seen], [(self.romfs, False, 0)])
        self.assertFalse(os.path.exists(seen[0][3]))
        running.assert_not_called()
        install.assert_not_called()
        with zipfile.ZipFile(zip_path) as zf:
            self.assertIn("exefs/subsdk9", zf.namelist())
        self.assertIn(zip_path, out.getvalue())

    def test_package_rejects_debug(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            build_all.main(["--package", "--debug"])
        self.assertEqual(cm.exception.code, 2)


@unittest.skipUnless(HAS_IMAGING and HAS_ASSETS and os.path.isfile(os.path.join(MOD_ASSETS, "manifest.json")),
                     "game asset folder or mod assets not found")
class DumpBuildTests(unittest.TestCase):
    def test_label_table_record(self):
        from ldtool.container import LinkData
        with LinkData.open_dir(_support.ROMFS) as ld:
            original = ld.read(gd.LABEL_TABLE_RECORD)
        for with_label in (True, False):
            rec = abuild.label_table_record(_support.ROMFS, with_label)
            row = 64 + 8 * 0x102
            self.assertEqual(rec[:row] + rec[row + 8:], original[:row] + original[row + 8:])
            want = (0xFC5B0505, 10, 0) if with_label else (0x14B92EA6, 10, 0)
            self.assertEqual(struct.unpack_from("<IHH", rec, row), want)

    def test_build_with_the_mod_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = abuild.build(_support.ROMFS, MOD_ASSETS, tmp, log=quiet)
            if not s.get("resources"):
                self.assertEqual(s["files"], [])
                return
            self.assertEqual([os.path.basename(f["path"]) for f in s["files"]],
                             ["0x4b13d561.fdata", "root.rdb"])
            with ResourceDb(ASSET_ROOT) as game:
                with open(os.path.join(ASSET_ROOT, "0x4b13d561.fdata"), "rb") as f:
                    host = f.read()
                with open(s["files"][0]["path"], "rb") as f:
                    self.assertEqual(f.read(len(host)), host)
                if s["label"]:
                    self.assertIn("0x%08x" % gd.LABEL_SPRITE_SET, s["resources"])
                    self.assertIn("0x%08x" % gd.LABEL_ATLAS, s["resources"])
                    self.assertTrue(all(t["roundtrip"]["psnr"] >= abuild.ROUNDTRIP_MIN_PSNR
                                        for t in s["textures"]))
                self.assertEqual(len(parse_sprite_set(game.read_ktid(gd.LABEL_SPRITE_SET))), 470)


@_support.needs_romfs
class ExtraRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ldtool-extra-")
        cls.po_dir = os.path.join(cls.tmp, "po")
        os.makedirs(cls.po_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def config(self, **kw):
        from ldtool.pipeline import BuildConfig
        return BuildConfig(romfs=_support.ROMFS, po_dir=self.po_dir, always_on_dir=None,
                           out_dir=os.path.join(self.tmp, "out"), **kw)

    def test_raw_record_is_written_and_verified(self):
        from ldtool.container import LinkData
        from ldtool.pipeline import build, verify
        data = bytes(range(256)) * 3
        cfg = self.config(extra_records={1552: data})
        summary = build(cfg, log=_support.quiet)
        self.assertEqual([(r["source"], r["kind"]) for r in summary["records"]], [(1552, "raw")])
        self.assertTrue(verify(cfg, log=_support.quiet))
        with LinkData(cfg.idx_path, cfg.bin_path) as ld:
            self.assertEqual(ld.read(1552), data)
            e = ld.entry(1552)
            self.assertEqual((e.marker, e.target, e.compressed), (0, 0, False))
        with open(cfg.bin_path, "r+b") as f:
            f.seek(e.offset)
            f.write(b"\xff")
        self.assertFalse(verify(cfg, log=_support.quiet))

    def test_bad_extra_records(self):
        from ldtool.gamedata import SPARE_FIRST_INDEX
        from ldtool.pipeline import BuildConfig, PipelineError, build
        with self.assertRaises(PipelineError):
            build(self.config(extra_records={SPARE_FIRST_INDEX: b"x"}), log=_support.quiet)
        with self.assertRaises(PipelineError):
            BuildConfig(layout="archive", extra_records={1552: b"x"})


if __name__ == "__main__":
    unittest.main()
