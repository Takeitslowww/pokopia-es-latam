import struct
import unittest

import _support
from ldtool.table import (TableError, TableSchema, TextTable, infer_schema, parse_table,
                          serialize_table, serialize_table_append)


def make_table(kinds="si", rows=None, mode=2):
    rows = rows or [["", 0], ["Hola", 1], ["¿Qué tal?<br>Bien", 10], ["", 0]]
    return TextTable(mode, bytes(56), TableSchema(4 * len(kinds), kinds), [list(r) for r in rows])


class TableTests(unittest.TestCase):
    def test_canonical_layout(self):
        data = serialize_table(make_table())
        self.assertEqual(struct.unpack_from("<II", data), (4, 2))
        self.assertEqual(data[8:64], bytes(56))
        self.assertEqual(struct.unpack_from("<I", data, 64)[0], 4 * 8)
        self.assertEqual(len(data) % 8, 0)
        pool = data[64 + 32:]
        self.assertTrue(pool.startswith(b"\x00Hola\x00\xc2\xbfQu\xc3\xa9 tal?<br>Bien\x00\x00"))

    def test_roundtrip_all_schemas(self):
        cases = {
            "s": [["a"], [""], ["ñandú"]],
            "si": [["x", 1], ["", 0]],
            "ssi": [["a", "b", 0x10000], ["", "", 0]],
            "ssssssii": [["1", "", "3", "4", "", "6", 0x800000, 0x18000]],
            "sssssssssi": [[str(i) for i in range(9)] + [1], [""] * 9 + [0]],
        }
        for kinds, rows in cases.items():
            for mode in (0, 2):
                t = make_table(kinds, rows, mode)
                data = serialize_table(t)
                back = parse_table(data)
                self.assertEqual(back.schema, t.schema, kinds)
                self.assertTrue(back.same_content(t), kinds)
                self.assertEqual(serialize_table(back), data)

    def test_text_accessors(self):
        t = make_table("ssi", [["a", "b", 7]])
        self.assertEqual(t.text(0, 1), "b")
        t.set_text(0, 1, "c")
        self.assertEqual(t.texts(0), ["a", "c"])
        self.assertEqual(t.ints(0), [7])
        with self.assertRaises(IndexError):
            t.text(0, 2)
        with self.assertRaises(TableError):
            t.set_text(0, 0, "bad\0")
        t.set_ints(0, [9])
        self.assertEqual(t.ints(0), [9])
        self.assertEqual(parse_table(serialize_table(t)).ints(0), [9])
        for bad in ([], [1, 2], [-1], [1 << 32], [True]):
            with self.assertRaises(TableError):
                t.set_ints(0, bad)

    def test_append_mode(self):
        original = serialize_table(make_table())
        t = parse_table(original)
        t.set_text(1, 0, "Holi")
        t.set_text(3, 0, "Holi")
        t.rows[2][1] = 9
        data = serialize_table_append(original, t)
        self.assertEqual(data[:64 + 8], original[:64 + 8])
        self.assertEqual(len(data), len(original) + len("Holi") + 1)
        back = parse_table(data, t.schema)
        self.assertTrue(back.same_content(t))
        self.assertTrue(parse_table(data).same_content(t))
        self.assertEqual(serialize_table_append(original, parse_table(original)), original)

    def test_inference_when_row0_repointed(self):
        original = serialize_table(make_table("si", [["a", 1], ["b", 1], ["c", 0]]))
        t = parse_table(original)
        t.set_text(0, 0, "changed")
        data = serialize_table_append(original, t)
        self.assertEqual(infer_schema(data), t.schema)

    def test_errors(self):
        data = bytearray(serialize_table(make_table()))
        struct.pack_into("<I", data, 64, 4)
        with self.assertRaises(TableError):
            parse_table(bytes(data), TableSchema(8, "si"))
        with self.assertRaises(TableError):
            parse_table(b"\x01\x00\x00\x00")
        bad_utf8 = bytearray(serialize_table(make_table("s", [["ab"]])))
        bad_utf8[-8] = 0xFF
        with self.assertRaises(TableError):
            parse_table(bytes(bad_utf8), TableSchema(4, "s"))

    def test_empty_table(self):
        t = TextTable(0, bytes(56), TableSchema(4, "s"), [])
        data = serialize_table(t)
        self.assertEqual(len(data), 64)
        self.assertEqual(parse_table(data).count, 0)


if __name__ == "__main__":
    unittest.main()
