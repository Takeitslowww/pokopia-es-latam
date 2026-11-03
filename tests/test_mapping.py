import json
import os
import tempfile
import unittest

import _support
from ldtool.mapping import MappingError, resolve_entry_count, resolve_mapping


class MappingTests(unittest.TestCase):
    SOURCES = [1914, 1474, 1923]

    def test_identity(self):
        self.assertEqual(resolve_mapping("identity", self.SOURCES),
                         {1474: 1474, 1914: 1914, 1923: 1923})

    def test_sequential_and_offset(self):
        self.assertEqual(resolve_mapping("sequential:8953", self.SOURCES),
                         {1474: 8953, 1914: 8954, 1923: 8955})
        self.assertEqual(resolve_mapping("offset:-1000", self.SOURCES)[1914], 914)
        self.assertEqual(resolve_mapping("sequential:0x10", [5])[5], 16)

    def test_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            with open(path, "w") as fh:
                json.dump({"1474": 9000, "1914": 9001, "1923": 9002, "9999": 1}, fh)
            self.assertEqual(resolve_mapping(f"file:{path}", self.SOURCES)[1923], 9002)
            with open(path, "w") as fh:
                json.dump({"1474": 9000}, fh)
            with self.assertRaises(MappingError):
                resolve_mapping(f"file:{path}", self.SOURCES)

    def test_errors(self):
        with self.assertRaises(MappingError):
            resolve_mapping("offset:-2000", self.SOURCES)
        with self.assertRaises(MappingError):
            resolve_mapping("bogus", self.SOURCES)
        with self.assertRaises(MappingError):
            resolve_mapping("sequential:abc", self.SOURCES)

    def test_entry_count(self):
        m = resolve_mapping("identity", self.SOURCES)
        self.assertEqual(resolve_entry_count("full", m, 12298), 12298)
        self.assertEqual(resolve_entry_count("compact", m, 12298), 1924)
        self.assertEqual(resolve_entry_count("5000", m, 12298), 5000)
        with self.assertRaises(MappingError):
            resolve_entry_count("100", m, 12298)


if __name__ == "__main__":
    unittest.main()
