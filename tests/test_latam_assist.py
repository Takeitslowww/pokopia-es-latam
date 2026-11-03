import os
import shutil
import sys
import tempfile
import unittest

import _support

sys.path.insert(0, os.path.join(_support.MOD_ROOT, "scripts"))
import latam_assist as la
import latam_text as lt
from ldtool.po import PoEntry, PoFile, format_po, read_po, write_po

PO_DIR = os.path.join(_support.MOD_ROOT, "text", "es-419")

DIALOGUE = ("<oneline><$comeff(1,9)><$motion(1,806)><$voicel(1,9)>¡Siempre lo paso chachi piruli "
            "cuando estoy contigo,<br><$cdef(27)><pname;0></color>!<clear><br><$emotion(1,38)>"
            "<$voicel(1,7)>¿Qué nos reservará el futuro? ¡Estoy ya impaciente por<br>descubrirlo!")
SAVE_NOTE = ("Este juego guarda tu progreso automáticamente.<br>Cuando eso ocurra, verás el icono "
             "<$icon(2645)> en pantalla.<br><br>No cierres el juego ni apagues la consola mientras "
             "se guardan<br>los datos.")
GRAMMAR = ("<oneline><$comeff(1,14)><$motion(1,666)><$voicel(1,6)>Recórcholis, tu cara no me suena..."
           "<clear><br><$emotion(1,29)><$voicel(1,7)>Hmm... ¿No serás amiguit<gen;-2(o,a)> de "
           "<$cdef(27)><nowner;0><br></color>por un casual<slide><br>de los casuales?<clear><br>"
           "<$voicel(1,6)>Pues mira tú qué cosa, ya tenemos algo en común tú y yo.<br>"
           "<$emotion(1,38)><$voicel(1,10)>¡Encantad<gen;-3(o,a)> de conocerte, mocit<gen;-2(o,a)>!")
SLIDES = ("<oneline><$emotion(1,8)><$voicel(1,9)>Cómo me alegro de que<br><dart;0()><$cdef(27)>"
          "<fqty(1)><usingp;0></color><slide><br>ya est<qty;0(é,én)> acabad<genqty;0(o,a,os,as)>...")
PENSAIS = ("<$comeff(1,10)><$motion(1,807)><$voicel(1,10)>A ver, peña. ¿Qué pensáis <gen;-2(del,de la)> "
           "nuev<gen;-2(o,a)>? Lleva unas pintas<br>un poco raras...")
SAMPLES = [DIALOGUE, SAVE_NOTE, GRAMMAR, SLIDES, PENSAIS, "Una mata de patata reseca.",
           "<$cdef(27)>Centro Pokémon</color>", "Pues... <wait(1.0)> <$cdef(27)><$stflevel;0(1)></color>."]


def model_text(prompt: str) -> str:
    return prompt.split("\n\n\n", 1)[1]


class ProtectTests(unittest.TestCase):
    def test_identity_roundtrip(self):
        for fmt in lt.FORMATS:
            for newline in (False, True):
                for grammar in ("expand", "placeholder"):
                    for s in SAMPLES:
                        p = lt.protect(s, fmt, newline, grammar)
                        self.assertEqual(lt.restore(p, p.text).text, s, (fmt, newline, grammar, s))

    def test_model_never_sees_tags(self):
        for s in SAMPLES:
            p = lt.protect(s)
            self.assertNotRegex(p.text, r"<[^<>]*>", s)

    def test_edge_runs_and_groups(self):
        p = lt.protect(DIALOGUE)
        self.assertEqual(p.lead, "<oneline><$comeff(1,9)><$motion(1,806)><$voicel(1,9)>")
        self.assertEqual(p.trail, "")
        runs = [s.run for s in p.slots.values()]
        self.assertIn("<$cdef(27)><pname;0></color>", runs)
        self.assertIn("<clear><br><$emotion(1,38)><$voicel(1,7)>", runs)
        self.assertEqual(p.text, "¡Siempre lo paso chachi piruli cuando estoy contigo, {1}! {2} "
                                 "¿Qué nos reservará el futuro? ¡Estoy ya impaciente por descubrirlo!")

    def test_grammar_words_expanded(self):
        p = lt.protect(GRAMMAR)
        self.assertEqual([w for w, _ in p.words], ["amiguito", "Encantado", "mocito"])
        self.assertIn("¡Encantado de conocerte, mocito!", p.text)
        p = lt.protect(PENSAIS)
        self.assertIn("¿Qué pensáis del nuevo?", p.text)

    def test_ambiguous_grammar_word_becomes_placeholder(self):
        s = "El amigo de tu amig<gen;0(o,a)>."
        p = lt.protect(s)
        self.assertEqual(p.words, [])
        self.assertIn("amig<gen;0(o,a)>", [x.run for x in p.slots.values()])
        self.assertEqual(lt.restore(p, p.text).text, s)

    def test_translatable(self):
        self.assertFalse(lt.is_translatable("タマゴ"))
        self.assertFalse(lt.is_translatable("<$icon(1)><br>123"))
        self.assertTrue(lt.is_translatable("¡Hola!"))


class RestoreTests(unittest.TestCase):
    def test_changed_text_keeps_tags(self):
        p = lt.protect(DIALOGUE)
        out = ("¡Siempre me divierto muchísimo cuando estoy contigo, {1}! {2} ¿Qué nos reservará "
               "el futuro? ¡Estoy ya impaciente por descubrirlo!")
        r = lt.restore(p, out)
        self.assertEqual(r.errors, [])
        self.assertEqual(lt.tag_problems(DIALOGUE, r.text), [])
        self.assertTrue(r.text.startswith(p.lead + "¡Siempre me divierto"))
        self.assertIn("<clear><br><$emotion(1,38)><$voicel(1,7)>¿Qué", r.text)
        self.assertTrue(r.text.endswith("¡Estoy ya impaciente por<br>descubrirlo!"))

    def test_dropped_repeated_unknown_placeholders(self):
        p = lt.protect(DIALOGUE)
        no_name = p.text.replace("{1}", "")
        self.assertIn("dropped", " ".join(lt.restore(p, no_name).errors))
        self.assertIn("repeated", " ".join(lt.restore(p, p.text + " {1}").errors))
        self.assertIn("emptied", " ".join(lt.restore(p, "{2} hola {1}").errors))
        self.assertIn("emptied", " ".join(lt.restore(p, "Hola, {1}!").errors))
        g = lt.protect(GRAMMAR)
        hard = [g.fmt.render(n) for n in g.hard_order]
        swapped = g.text.replace(hard[0], "@").replace(hard[1], hard[0]).replace("@", hard[1])
        self.assertIn("out of order", " ".join(lt.restore(g, swapped).errors))

    def test_grammar_word_changed(self):
        p = lt.protect(GRAMMAR)
        r = lt.restore(p, p.text.replace("amiguito", "amigo"))
        self.assertEqual(r.errors, [])
        self.assertIn("¿No serás amig<gen;-2(o,a)> de", r.text)
        self.assertTrue(any("amig<gen;-2(o,a)>" in n for n in r.notes))
        bad = p.text.replace("amiguito", "colega")
        self.assertIn("amiguito", " ".join(lt.restore(p, bad).errors))
        self.assertEqual(lt.reattach_grammar("tronc<gen;-2(o,a)>", "amigo"), "amig<gen;-2(o,a)>")
        self.assertEqual(lt.reattach_grammar("señor<gen;0(,a)>", "don"), "don<gen;0(,a)>")
        self.assertIsNone(lt.reattach_grammar("<gen;-2(del,de la)>", "al"))
        good = p.text.replace("Pues mira tú qué cosa", "Pues mira qué cosa")
        r = lt.restore(p, good)
        self.assertEqual(r.errors, [])
        self.assertIn("¡Encantad<gen;-3(o,a)> de conocerte, mocit<gen;-2(o,a)>!", r.text)

    def test_hard_break_padding_removed(self):
        p = lt.protect(GRAMMAR)
        r = lt.restore(p, p.text.replace("Recórcholis", "Caramba"))
        self.assertIn("amiguit<gen;-2(o,a)> de <$cdef(27)><nowner;0><br></color>por un casual", r.text)
        self.assertEqual(lt.tag_problems(GRAMMAR, r.text), [])

    def test_slide_breaks_are_rewrapped(self):
        p = lt.protect(SLIDES)
        self.assertFalse(any("<slide>" in s.run for s in p.slots.values()))
        r = lt.restore(p, p.text.replace("Cómo me alegro", "Qué alegría"))
        self.assertEqual(lt.tag_problems(SLIDES, r.text), [])
        first, rest = r.text.split("<br>", 1)
        self.assertNotIn("<slide>", first)
        self.assertIn("<slide><br>", rest)
        odd = "Uno<slide><br>dos<br>tres y más texto aquí."
        self.assertTrue(any("<slide>" in s.run for s in lt.protect(odd).slots.values()))

    def test_alternatives_and_runaway_output(self):
        p = lt.protect("¿Qué te apetece hacer?")
        alternatives = "¿Qué te gustaría hacer?\n¿Qué te apetece hacer?\n¿Qué quieres hacer?"
        r = lt.restore(p, alternatives)
        self.assertEqual(r.text, "¿Qué te gustaría hacer?")
        self.assertTrue(any("alternatives" in n for n in r.notes))
        self.assertIn("longer", " ".join(lt.restore(p, "¿Qué quieres hacer? " * 6).errors))

    def test_invented_placeholder_removed(self):
        p = lt.protect("Vale, no pasa nada.")
        r = lt.restore(p, "De acuerdo, no hay problema. {1}.")
        self.assertEqual(r.text, "De acuerdo, no hay problema.")
        self.assertTrue(r.notes)

    def test_dropped_control_tags_and_breaks_reinserted(self):
        s = ("<$voicel(1,8)>¿Es la foto que te pedí? Qué chachi.<$emotion(1,8)><$voicel(1,8)> "
             "Muchas gracias.<clear><br><$voicel(1,2)>Hasta luego.")
        p = lt.protect(s)
        self.assertEqual(p.text, "¿Es la foto que te pedí? Qué chachi.{1} Muchas gracias. {2} Hasta luego.")
        r = lt.restore(p, "¿Es la foto que te pedí? Qué linda. Muchas gracias. Hasta luego.")
        self.assertEqual(r.errors, [])
        self.assertEqual(r.text, "<$voicel(1,8)>¿Es la foto que te pedí? Qué linda.<$emotion(1,8)>"
                                 "<$voicel(1,8)> Muchas gracias.<clear><br><$voicel(1,2)>Hasta luego.")
        self.assertEqual(len([n for n in r.notes if "re-inserted" in n]), 2)
        p = lt.protect("Hola, <$cdef(27)><pname;0></color>. ¿Qué tal?")
        self.assertIn("dropped", " ".join(lt.restore(p, "Hola. ¿Qué tal?").errors))

    def test_spacing_around_voice_tags(self):
        s = ("<$cdef(27)><$emotion(1,60)></color><$voicel(1,9)>¿Es la foto que te pedí? Qué chachi, "
             "me encanta.<br><$emotion(1,8)><$voicel(1,8)>Muchas gracias.")
        p = lt.protect(s)
        r = lt.restore(p, "{1} ¿Es la foto que te pedí? ¡Qué linda!, me encanta. {2} Muchas gracias.")
        self.assertEqual(r.errors, [])
        self.assertTrue(r.text.startswith("<$cdef(27)><$emotion(1,60)></color><$voicel(1,9)>¿Es"))
        self.assertIn("<$emotion(1,8)><$voicel(1,8)>Muchas", r.text)
        self.assertNotRegex(r.text, r"\. <\$emotion\(1,8\)><\$voicel\(1,8\)> ")

    def test_page_break_punctuation(self):
        s = ("<$voicel(1,5)>Venga, machaca la <$cdef(27)><$ifull(2340)></color> y tráemela.<clear><br>"
             "<$voicel(1,8)>Dile a algún Pokémon que te ayude.")
        p = lt.protect(s)
        r = lt.restore(p, "¡Vamos, rompe la {1} y tráemela! Si es necesario, pídele ayuda a un Pokémon.")
        self.assertEqual(r.errors, [])
        self.assertIn("tráemela!<clear><br><$voicel(1,8)>Si es", r.text)
        r = lt.restore(p, "¡Vamos, rompe la {1} y tráemela! ¿ {2} Le pides ayuda a un Pokémon?")
        self.assertIn("tráemela!<clear><br><$voicel(1,8)>¿Le pides", r.text)

    def test_grammar_notes_show_all_forms(self):
        self.assertEqual(lt.grammar_forms("gust<gen;-3(o,a)>"), "gusto/gusta")
        p = lt.protect("¡Qué hay! ¡Encantad<gen;-3(o,a)>!")
        r = lt.restore(p, "¡Hola! ¡Mucho gusto!")
        self.assertTrue(any("gusto/gusta" in n for n in r.notes), r.notes)

    def test_leading_name_stays_visible(self):
        s = "<$voicel(1,5)><$cdef(27)><pname;0></color> es tan guay..."
        p = lt.protect(s)
        self.assertEqual(p.lead, "<$voicel(1,5)>")
        self.assertEqual(p.text, "{1} es tan guay...")

    def test_repeated_word_warning(self):
        s = "¡Siempre estoy dispuest<gen;-3(o,a)> a limpiar!"
        bad = "¡Siempre estoy dispuest<gen;-3(o,a)> dispuesta a limpiar!"
        self.assertTrue(lt.repeat_warnings(s, bad))
        self.assertEqual(lt.repeat_warnings(s, s), [])

    def test_wrapping_quotes_removed(self):
        p = lt.protect("Una mata de papa reseca.")
        self.assertEqual(lt.restore(p, '"Una mata de papa seca."').text, "Una mata de papa seca.")

    def test_newline_mode_keeps_model_breaks(self):
        p = lt.protect(SAVE_NOTE, newline_mode=True)
        out = p.text.replace("No cierres", "No cierres nunca")
        r = lt.restore(p, out)
        self.assertEqual(r.errors, [])
        self.assertIn("nunca el juego ni apagues la consola mientras se guardan<br>los datos.", r.text)


class WrapTests(unittest.TestCase):
    def test_wrap_words(self):
        self.assertEqual(lt.wrap_words("aaa bbb ccc ddd", 7), ["aaa bbb", "ccc ddd"])
        self.assertEqual(lt.wrap_words("aaa<$voicel(1,2)> bbb", 7), ["aaa<$voicel(1,2)> bbb"])
        self.assertEqual(lt.wrap_words("<pname;0> bbb", 10), ["<pname;0>", "bbb"])

    def test_changed_lines_rewrapped_to_page_width(self):
        p = lt.protect(SAVE_NOTE)
        out = p.text.replace("Este juego guarda tu progreso automáticamente.",
                             "Este juego guarda tu progreso de forma automática cada cierto tiempo.")
        r = lt.restore(p, out)
        width = lt.page_widths(SAVE_NOTE)[0][0]
        for line in r.text.split("<br>"):
            self.assertLessEqual(lt.visible_width(line), width)
        self.assertIn("<br><br>No cierres", r.text)
        self.assertTrue(r.text.endswith("se guardan<br>los datos."))

    def test_single_line_never_wrapped(self):
        s = "Consulta tu Hábitatdex para coger ideas."
        p = lt.protect(s)
        r = lt.restore(p, "Consulta tu Hábitatdex para tomar ideas y muchas cosas más interesantes.")
        self.assertNotIn("<br>", r.text)
        self.assertTrue(any("grew" in w for w in lt.layout_warnings(s, r.text)))


class ValidateTests(unittest.TestCase):
    def test_identical_is_valid(self):
        for s in SAMPLES:
            self.assertEqual(lt.tag_problems(s, s), [], s)

    def test_problems(self):
        d = DIALOGUE
        cases = {
            "missing tags": d.replace("<pname;0>", ""),
            "extra tags": d.replace("¡Siempre", "<pname;0>¡Siempre"),
            "leading tags": d.replace("<oneline>", "", 1),
            "<clear> count": d.replace("<clear>", ""),
            "raw line break": d.replace("<br>descubrirlo", "\ndescubrirlo"),
            "unbalanced": d.replace("¡Siempre", "<¡Siempre"),
            "leftover placeholders": d.replace("¡Siempre", "{3} ¡Siempre"),
        }
        for what, text in cases.items():
            self.assertTrue(any(what in p for p in lt.tag_problems(d, text)), what)
        s = "Ve al <$cdef(27)>Centro</color> y <$cdef(27)>Tienda</color>."
        swapped = "Ve al </color>Centro<$cdef(27)> y <$cdef(27)>Tienda</color>."
        self.assertTrue(any("out of order" in p for p in lt.tag_problems(s, swapped)))
        trail = "Hola, <$cdef(27)>Centro</color>"
        moved = "Hola, <$cdef(27)>Centro</color><$voicel(1,2)>"
        self.assertTrue(any("trailing" in p for p in lt.tag_problems(trail, moved)))

    def test_layout_tags_may_move(self):
        moved = SAVE_NOTE.replace("se guardan<br>los datos.", "se<br>guardan los datos.")
        self.assertEqual(lt.tag_problems(SAVE_NOTE, moved), [])

    def test_register_warnings(self):
        self.assertIn("formal 'usted' introduced", lt.register_warnings("¿Tú vienes?", "¿Usted viene?"))
        self.assertIn("voseo introduced", lt.register_warnings("¿Tú tienes?", "¿Vos tenés?"))


class SpainismTests(unittest.TestCase):
    POSITIVE = [
        "¡Eh, vosotros!", "Coged vuestras cosas.", "¿Os gusta?", "¿Qué pensáis?", "¿Lo decís en serio?",
        "¡Acercaos todos!", "Vale, ya voy.", "Hazlo, ¿vale?", "¡Qué guay!", "Me mola.",
        "Es chachi piruli.", "Ey, tío, ven.", "Gracias, tío.", "Esto flipa.", "mi ordenador",
        "el móvil", "un zumo", "¡Venga, vamos!", "voy a coger una fresa", "cójanlo",
        "¿Te apetece?", "Me enfado.", "¡Qué chulo!", "tronc<gen;-2(o,a)>?",
    ]
    NEGATIVE = [
        "Mi casa es acogedora.", "Vale la pena.", "Eso no vale.", "¿Cuánto vale?", "Mi tío vive aquí.",
        "el país", "¡Achís!", "El camino conduce al mar.", "Hola, ¿cómo estás?", "Es un caos.",
        "Recoge las bayas.", "Escoge una opción.", "Los dos sabemos.", "Usted y yo.",
    ]

    def test_positive(self):
        for s in self.POSITIVE:
            self.assertTrue(lt.spainisms(s), s)

    def test_negative(self):
        for s in self.NEGATIVE:
            self.assertEqual(lt.spainisms(s), [], s)

    def test_spain_only_uses_same_regex(self):
        self.assertTrue(lt.is_spain_only(PENSAIS))
        self.assertFalse(lt.is_spain_only(SAVE_NOTE))


@unittest.skipUnless(os.path.isdir(PO_DIR), "text/es-419 not found")
class CorpusTests(unittest.TestCase):
    def test_every_msgid_roundtrips_and_validates(self):
        bad = []
        for name in sorted(n for n in os.listdir(PO_DIR) if n.endswith(".po")):
            for e in read_po(os.path.join(PO_DIR, name)).entries:
                if e.obsolete or not lt.is_translatable(e.msgid):
                    continue
                p = lt.protect(e.msgid)
                if lt.restore(p, p.text).text != e.msgid or lt.tag_problems(e.msgid, e.msgid):
                    bad.append(e.msgctxt)
        self.assertEqual(bad, [])


class FakeClient:
    model = "fake"

    def __init__(self, replacements):
        self.replacements = replacements
        self.calls = 0

    def chat(self, prompt, *, temperature, num_predict, seed=None):
        self.calls += 1
        text = model_text(prompt)
        for a, b in self.replacements:
            text = text.replace(a, b)
        return text, {"done_reason": "stop", "total_duration": 1}


def scripted_io(keys, edits=(), confirms=()):
    keys, edits, confirms = list(keys), list(edits), list(confirms)
    return la.ReviewIO(read_key=lambda: keys.pop(0), edit=lambda text: edits.pop(0),
                       confirm=lambda q: confirms.pop(0), wait=lambda msg, fn: fn())


class QuietConsole:
    def print(self, *a, **k):
        pass

    def rule(self, *a, **k):
        pass


PO_ENTRIES = [
    ("9:1", "¡Eh, vosotros! ¡Coged vuestras cosas, que nos vamos!"),
    ("9:2", "Una mata de patata reseca."),
    ("9:3", "Hola, <$cdef(27)><pname;0></color>. ¿Os gusta?"),
    ("9:4", "Sin cambios aquí."),
    ("9:5", "Vale, ¿qué hacemos?"),
]


class ReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.po_dir = os.path.join(self.tmp, "po")
        os.makedirs(self.po_dir)
        entries = [PoEntry(msgctxt=c, msgid=m, extracted_comments=["en: x"]) for c, m in PO_ENTRIES]
        write_po(os.path.join(self.po_dir, "table_9.po"),
                 PoFile(entries, [("Language", "es_419")], ["test"]))
        self.client = FakeClient([("vosotros", "ustedes"), ("Coged vuestras", "Recojan sus"),
                                  ("patata", "papa"), ("Os gusta", "Les gusta"),
                                  ("Vale,", "Bueno,")])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def ctx(self):
        rd = os.path.join(self.tmp, "review")
        return la.Context(self.po_dir, la.JsonlStore(os.path.join(rd, "suggestions.jsonl")),
                          la.JsonlStore(os.path.join(rd, "decisions.jsonl")), self.client,
                          la.GenConfig())

    def msgstrs(self):
        return {e.msgctxt: e.msgstr for e in read_po(os.path.join(self.po_dir, "table_9.po")).entries}

    def review(self, io, **kw):
        ctx = self.ctx()
        items = la.load_items(self.po_dir, la.Filters())
        return la.Reviewer(ctx, items, io, QuietConsole(), workers=1, **kw).run(), ctx

    def test_accept_keep_edit_quit_and_resume(self):
        bad_edit = "Hola, <pname;0>. ¿Les gusta?"
        good_edit = "Hola, <$cdef(27)><pname;0></color>. ¿Les gusta a ustedes?"
        io = scripted_io("akeeq", edits=[bad_edit, good_edit], confirms=[False])
        counts, ctx = self.review(io)
        self.assertEqual((counts["accept"], counts["edit"], counts["keep"], counts["same"]),
                         (1, 1, 1, 1))
        po = self.msgstrs()
        self.assertEqual(po["9:1"], "¡Eh, ustedes! ¡Recojan sus cosas, que nos vamos!")
        self.assertEqual(po["9:2"], "")
        self.assertEqual(po["9:3"], good_edit)
        self.assertEqual(po["9:4"], "")
        self.assertEqual(po["9:5"], "")
        calls = self.client.calls
        counts, _ = self.review(scripted_io("a"))
        self.assertEqual(counts["accept"], 1)
        self.assertEqual(self.client.calls, calls)
        self.assertEqual(self.msgstrs()["9:5"], "Bueno, ¿qué hacemos?")

    def test_invalid_edit_needs_explicit_override(self):
        forced = "Hola, <pname;0>. ¿Les gusta?"
        io = scripted_io("kkea", edits=[forced], confirms=[True])
        counts, ctx = self.review(io)
        self.assertEqual(counts["forced"], 1)
        self.assertEqual(self.msgstrs()["9:3"], forced)

    def test_accept_without_usable_proposal_decides_nothing(self):
        ctx = self.ctx()
        ctx.client = FakeClient([])
        ctx.client.chat = lambda prompt, **kw: ("{1} {2} {3}", {"done_reason": "stop"})
        items = la.load_items(self.po_dir, la.Filters(ctxts=["9:3"]))
        counts = la.Reviewer(ctx, items, scripted_io("ak"), QuietConsole(), workers=1).run()
        self.assertEqual((counts["accept"], counts["keep"]), (0, 1))
        self.assertEqual(ctx.decisions.get(items[0].key)["action"], "keep")

    def test_reconcile_reapplies_decisions(self):
        self.review(scripted_io("a" "q"))
        path = os.path.join(self.po_dir, "table_9.po")
        po = read_po(path)
        po.entries[0].msgstr = ""
        write_po(path, po)
        applied, problems = la.reconcile(self.ctx())
        self.assertEqual((applied, problems), (1, []))
        self.assertTrue(self.msgstrs()["9:1"])

    def test_stale_msgid_refused(self):
        item = la.load_items(self.po_dir, la.Filters(ctxts=["9:2"]))[0]
        path = os.path.join(self.po_dir, "table_9.po")
        po = read_po(path)
        po.entries[1].msgid = "Otra cosa."
        write_po(path, po)
        with self.assertRaises(la.StaleEntry):
            la.write_msgstr(self.po_dir, item, "Una mata de papa reseca.")

    def test_filters(self):
        f = la.Filters(spain_only=True)
        self.assertEqual([i.ctxt for i in la.load_items(self.po_dir, f)], ["9:1", "9:2", "9:3", "9:5"])
        self.assertEqual(len(la.load_items(self.po_dir, la.Filters(limit=2))), 2)
        self.assertEqual(la.load_items(self.po_dir, la.Filters(records=[8])), [])


class StoreTests(unittest.TestCase):
    def test_last_line_wins_and_truncated_line_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            s = la.JsonlStore(path)
            s.put({"key": "a", "v": 1})
            s.put({"key": "a", "v": 2})
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"key": "b", "v"')
            s2 = la.JsonlStore(path)
            self.assertEqual(s2.get("a")["v"], 2)
            self.assertIsNone(s2.get("b"))

    def test_po_writer_is_byte_stable(self):
        name = "table_2004.po"
        src = os.path.join(PO_DIR, name)
        if not os.path.exists(src):
            self.skipTest("text/es-419 not found")
        with open(src, "r", encoding="utf-8", newline="") as fh:
            original = fh.read()
        self.assertEqual(format_po(read_po(src)), original)

    def test_cli_defaults_do_not_leak_between_commands(self):
        p = la.make_parser()
        self.assertIsNone(p.parse_args(["suggest"]).workers)
        self.assertIsNone(p.parse_args(["review"]).workers)
        self.assertEqual(p.parse_args(["suggest", "--workers", "3"]).workers, 3)
        self.assertEqual(la.SUGGEST_WORKERS, 4)

    def test_prompt_mentions_placeholders_only_when_present(self):
        self.assertNotIn("placeholder", la.build_prompt("Hola, ¿vale?", "guided"))
        self.assertIn("placeholder such as {1}", la.build_prompt("Hola, {1}.", "guided"))
        self.assertNotIn("Use neutral", la.build_prompt("Hola, {1}.", "official"))

    def test_num_predict_bounded(self):
        self.assertLess(la.num_predict_for("x" * 300), 300)
        self.assertGreaterEqual(la.num_predict_for("hola"), 48)


if __name__ == "__main__":
    unittest.main()
