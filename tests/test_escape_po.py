import os
import tempfile
import unittest

import _support
from ldtool.escape import from_display, tags, to_display
from ldtool.po import PoEntry, PoFile, PoSyntaxError, format_po, parse_po, read_po, write_po

TRICKY = [
    "",
    "Plain text",
    "¡Hola! ¿Qué tal?<br>Línea 2<clear><br><$cdef(27)>PC</color>",
    "icon\ue01bglyph",
    "tab\tnew\nline\rcr",
    "zero\u200bwidth \u00adsoft \ufeffbom",
    "literal <U+0041> and <U+003C> text",
    "back\\slash \"quotes\"",
    "ruby <日:ひ> and ideographic\u3000space",
    "nbsp\u00a0kept",
]


class EscapeTests(unittest.TestCase):
    def test_roundtrip(self):
        for s in TRICKY:
            d = to_display(s)
            self.assertEqual(from_display(d), s, repr(s))

    def test_visible_form(self):
        self.assertEqual(to_display("a\ue01bb"), "a<U+E01B>b")
        self.assertEqual(to_display("x\ny"), "x<U+000A>y")
        self.assertEqual(to_display("<U+0041>"), "<U+003C>U+0041>")
        self.assertEqual(to_display("¡Hola!<br>"), "¡Hola!<br>")
        self.assertEqual(to_display("a\u00a0b"), "a\u00a0b")
        self.assertEqual(from_display("<U+e01b>"), "\ue01b")
        self.assertEqual(from_display("<u+e01b>"), "<u+e01b>")

    def test_invalid_escape(self):
        with self.assertRaises(ValueError):
            from_display("<U+D800>")
        with self.assertRaises(ValueError):
            from_display("<U+110000>")

    def test_tags(self):
        self.assertEqual(tags("a<br>b<$cdef(27)>c</color>"), ["<br>", "<$cdef(27)>", "</color>"])


class PoTests(unittest.TestCase):
    def test_roundtrip(self):
        entries = []
        for i, s in enumerate(TRICKY):
            e = PoEntry(msgctxt=f"1:{i}", msgid=to_display(s), msgstr=to_display(s[::-1]))
            e.extracted_comments.append("en: " + (to_display(s) or "(empty)"))
            if i % 3 == 0:
                e.flags.append("fuzzy")
                e.previous.append('msgid "old"')
            entries.append(e)
        po = PoFile(entries, [("Language", "es_419"), ("Content-Type", "text/plain; charset=UTF-8")],
                    ["comment"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.po")
            write_po(path, po)
            back = read_po(path)
        self.assertEqual(back.header_value("language"), "es_419")
        self.assertEqual(back.header_comments, ["comment"])
        self.assertEqual(len(back.entries), len(entries))
        for a, b in zip(entries, back.entries):
            self.assertEqual((a.msgctxt, a.msgid, a.msgstr, a.flags, a.previous, a.extracted_comments),
                             (b.msgctxt, b.msgid, b.msgstr, b.flags, b.previous, b.extracted_comments))

    def test_poedit_style_input(self):
        text = (
            '\ufeff# translator note\n'
            'msgid ""\nmsgstr ""\n"Language: es_419\\n"\n"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
            '#. en: Hello\n#: some/ref:1 other:2\n#, fuzzy, c-format\n#| msgid "Old"\n'
            'msgctxt "10:2"\nmsgid ""\n"Hola, este es un texto largo "\n"partido en varias líneas"\n'
            'msgstr ""\n"Hola \\"LatAm\\" "\n"\\101\\x42"\n'
            'msgctxt "10:3"\nmsgid "x"\nmsgstr "y"\n\n'
            '#~ msgctxt "10:9"\n#~ msgid "gone"\n#~ msgstr "ido"\n'
        )
        po = parse_po(text, "t.po")
        self.assertEqual(po.header_value("Plural-Forms"), "nplurals=2; plural=(n != 1);")
        self.assertEqual(po.header_comments, ["translator note"])
        e = po.entries[0]
        self.assertEqual(e.msgctxt, "10:2")
        self.assertEqual(e.msgid, "Hola, este es un texto largo partido en varias líneas")
        self.assertEqual(e.msgstr, 'Hola "LatAm" AB')
        self.assertTrue(e.fuzzy)
        self.assertIn("c-format", e.flags)
        self.assertEqual(e.references, ["some/ref:1", "other:2"])
        self.assertEqual(e.extracted_comments, ["en: Hello"])
        self.assertEqual((po.entries[1].msgctxt, po.entries[1].msgstr), ("10:3", "y"))
        self.assertTrue(po.entries[2].obsolete)
        self.assertEqual(po.entries[2].msgstr, "ido")

    def test_multiline_output(self):
        po = PoFile([PoEntry(msgctxt="1:1", msgid="a\nb", msgstr="")], [], [])
        out = format_po(po)
        self.assertIn('msgid ""\n"a\\n"\n"b"', out)
        self.assertEqual(parse_po(out).entries[0].msgid, "a\nb")

    def test_syntax_errors(self):
        for bad in ('msgid "unterminated\n', 'msgid "x"\nmsgstr "\\q"\n', '"orphan"\n', "garbage\n"):
            with self.assertRaises(PoSyntaxError, msg=bad):
                parse_po(bad)


if __name__ == "__main__":
    unittest.main()
