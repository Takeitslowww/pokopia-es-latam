from __future__ import annotations

import collections
import difflib
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(os.path.dirname(_HERE), "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from ldtool.escape import TAG_RE
from ldtool.pipeline import LAYOUT_TAGS, PoText, lint_translation


LETTER = r"[^\W\d_]"
GRAMMAR_TAG = r"<(?:gen|qty|genqty);-?\d+\([^()<>]*\)>"
GRAMMAR_TAG_RE = re.compile(GRAMMAR_TAG)
_GRAMMAR_ARGS_RE = re.compile(r"<(?:gen|qty|genqty);-?\d+\(([^()<>]*)\)>")
_NON_GRAMMAR_TAG = r"<(?!(?:gen|qty|genqty);)[^<>]*>"
_GRAMMAR_WORD = rf"{LETTER}*{GRAMMAR_TAG}(?:{LETTER}|{GRAMMAR_TAG})*"
_TAG_RUN = rf"{_NON_GRAMMAR_TAG}(?:\s*{_NON_GRAMMAR_TAG})*"
_BODY_TOKEN_RE = re.compile(rf"(?P<word>{_GRAMMAR_WORD})|(?P<run>{_TAG_RUN})")
_EDGE_TOKEN_RE = re.compile(r"(<[^<>]*>)")
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯ｦ-ﾟ]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")

BR = "<br>"
SLIDE_BR = "<slide><br>"
PAIR_TAG_RE = re.compile(r"<\$cdef\([^<>]*\)>|</color>|<\$textsize\([^<>]*\)>|</textsize>")


@dataclass(frozen=True)
class PlaceholderFormat:
    name: str
    template: str
    pattern: re.Pattern

    def render(self, n: int) -> str:
        return self.template.format(n=n)


FORMATS = {
    "brace": PlaceholderFormat("brace", "{{{n}}}", re.compile(r"\{\s*(\d+)\s*\}")),
    "bracket": PlaceholderFormat("bracket", "[{n}]", re.compile(r"\[\s*(\d+)\s*\]")),
    "xml": PlaceholderFormat("xml", "<x{n}/>", re.compile(r"<\s*x\s*(\d+)\s*/?\s*>")),
}
DEFAULT_FORMAT = "brace"
_ANY_PLACEHOLDER_RE = re.compile(r"\{\s*\d+\s*\}|\[\s*\d+\s*\]|<\s*/?\s*x\s*\d+\s*/?\s*>")


def msgid_hash(msgid: str) -> str:
    return hashlib.sha1(msgid.encode("utf-8")).hexdigest()[:12]


def expand_grammar(word: str) -> str:
    return _GRAMMAR_ARGS_RE.sub(lambda m: m.group(1).split(",")[0], word)


def plain_text(text: str) -> str:
    text = re.sub(_GRAMMAR_WORD, lambda m: expand_grammar(m.group(0)), text)
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", text)).strip()


def is_translatable(msgid: str) -> bool:
    plain = plain_text(msgid)
    return bool(_LATIN_LETTER_RE.search(plain)) and not _CJK_RE.search(plain)


def _is_content_tag(tag: str) -> bool:
    return tag_width(tag) > 0 or bool(PAIR_TAG_RE.fullmatch(tag))


def split_edges(text: str) -> tuple[str, str, str]:
    parts = _EDGE_TOKEN_RE.split(text)

    def edge(k: int) -> bool:
        return (not parts[k].strip()) if k % 2 == 0 else not _is_content_tag(parts[k])

    i = 0
    while i < len(parts) and edge(i):
        i += 1
    j = len(parts)
    while j > i and edge(j - 1):
        j -= 1
    if i >= j:
        return text, "", ""
    head = "".join(parts[:i])
    tail = "".join(parts[j:])
    body = "".join(parts[i:j])
    stripped = body.lstrip()
    head += body[:len(body) - len(stripped)]
    body = stripped
    stripped = body.rstrip()
    tail = body[len(stripped):] + tail
    return head, stripped, tail


_ZERO_WIDTH_TAGS = frozenset({
    "voicel", "comeff", "motion", "motionoff", "emotion", "facial", "kfacial", "cdef",
    "textsize", "comcan", "lookat", "wait", "waitactor", "flow", "fcap", "class", "fqty",
    "oneline", "slide", "clear", "br", "padvib", "se2d", "se3d", "se",
})
_ARTICLE_TAGS = frozenset({"dart", "iart", "esa", "esde"})
_TAG_NAME_RE = re.compile(r"<\s*/?\s*\$?([A-Za-z_]+)")


def tag_width(tag: str) -> int:
    if tag.startswith("</"):
        return 0
    m = _TAG_NAME_RE.match(tag)
    name = m.group(1).lower() if m else ""
    if name in _ZERO_WIDTH_TAGS:
        return 0
    if name.endswith("icon"):
        return 2
    if name.startswith("nbs"):
        return 1
    if name in _ARTICLE_TAGS:
        return 3
    if name in ("gen", "qty", "genqty"):
        args = _GRAMMAR_ARGS_RE.match(tag)
        return max((len(a) for a in args.group(1).split(",")), default=0) if args else 1
    return 8


def visible_width(text: str) -> int:
    width = 0
    pos = 0
    for m in TAG_RE.finditer(text):
        width += len(text[pos:m.start()]) + tag_width(m.group(0))
        pos = m.end()
    return width + len(text[pos:])


def page_widths(text: str) -> list[tuple[int, int]]:
    out = []
    for page in text.split("<clear>"):
        lines = page.split(BR)
        if len(lines) > 1 and not TAG_RE.sub("", lines[0]).strip():
            lines = lines[1:]
        out.append((max((visible_width(x) for x in lines), default=0), len(lines)))
    return out


@dataclass
class Slot:
    n: int
    run: str
    hard: bool
    ws_before: bool
    ws_after: bool


@dataclass
class Segment:
    text: str
    breaks: list[str]
    page: int

    @property
    def soft_breaks(self) -> int:
        return len(self.breaks)

    def separators(self, count: int) -> list[str]:
        if SLIDE_BR not in self.breaks:
            return [BR] * count
        plain = self.breaks.index(SLIDE_BR)
        return [BR if k < plain else SLIDE_BR for k in range(count)]


@dataclass
class Protected:
    msgid: str
    lead: str
    trail: str
    text: str
    slots: dict[int, Slot]
    words: list[tuple[str, str]]
    segments: list[Segment]
    hard_order: list[int]
    fmt: PlaceholderFormat
    newline_mode: bool
    widths: list[tuple[int, int]]
    word_spans: list[tuple[int, int]] = field(default_factory=list)

    @property
    def has_breaks(self) -> bool:
        return BR in self.msgid


def _split_run(run: str, soft_slides: bool) -> list[tuple[str, str]]:
    if "<clear>" in run or (not soft_slides and "<slide>" in run):
        return [("hard", run)]
    tags = TAG_RE.findall(run)
    if run != "".join(tags):
        hard = BR in run or "<slide>" in run
        return [("hard" if hard else "slot", run)]
    units: list[str] = []
    for t in tags:
        if t == BR and units and units[-1] == "<slide>":
            units[-1] = SLIDE_BR
        else:
            units.append(t)
    breaks = (BR, SLIDE_BR)
    i = 0
    while i < len(units) and units[i] in breaks:
        i += 1
    j = len(units)
    while j > i and units[j - 1] in breaks:
        j -= 1
    out: list[tuple[str, str]] = []

    def edge(part: list[str]) -> None:
        if len(part) == 1:
            out.append(("br", part[0]))
        elif part:
            out.append(("hard", "".join(part)))

    edge(units[:i])
    if i < j:
        middle = "".join(units[i:j])
        out.append(("hard" if BR in middle or "<slide>" in middle else "slot", middle))
    edge(units[max(i, j):])
    return out


def protect(msgid: str, fmt: str = DEFAULT_FORMAT, newline_mode: bool = False,
            grammar: str = "expand", soft_slides: bool = True) -> Protected:
    p = _protect(msgid, fmt, newline_mode, grammar, soft_slides)
    if soft_slides and any(SLIDE_BR in s.breaks and BR in s.breaks[s.breaks.index(SLIDE_BR):]
                           for s in p.segments):
        p = _protect(msgid, fmt, newline_mode, grammar, False)
    return p


def _protect(msgid: str, fmt: str, newline_mode: bool, grammar: str,
             soft_slides: bool) -> Protected:
    pf = FORMATS[fmt]
    lead, body, trail = split_edges(msgid)
    slots: dict[int, Slot] = {}
    words: list[tuple[str, str]] = []
    model: list[str] = []
    segments: list[Segment] = []
    hard_order: list[int] = []
    seg_text: list[str] = []
    seg_breaks: list[str] = []
    page = lead.count("<clear>")

    plain_counts = collections.Counter(
        w.lower() for w in re.findall(rf"{LETTER}+", _BODY_TOKEN_RE.sub(" ", body)))

    def add_slot(run: str, hard: bool, start: int, end: int,
                 ws_before: bool | None = None, ws_after: bool | None = None) -> None:
        n = len(slots) + 1
        if ws_before is None:
            ws_before = start > 0 and body[start - 1].isspace()
        if ws_after is None:
            ws_after = end < len(body) and body[end].isspace()
        slots[n] = Slot(n, run, hard, ws_before, ws_after)
        token = pf.render(n)
        if hard:
            if model and not model[-1][-1:].isspace():
                token = " " + token
            if end < len(body) and not body[end].isspace():
                token += " "
        model.append(token)

    pos = 0
    for m in _BODY_TOKEN_RE.finditer(body):
        if m.start() > pos:
            chunk = body[pos:m.start()]
            model.append(chunk)
            seg_text.append(chunk)
        pos = m.end()
        if m.group("word"):
            original = m.group("word")
            expanded = expand_grammar(original)
            if grammar == "expand" and expanded and plain_counts[expanded.lower()] == 0 \
                    and sum(1 for e, _ in words if e.lower() == expanded.lower()) == 0:
                words.append((expanded, original))
                model.append(expanded)
            else:
                add_slot(original, False, m.start(), m.end())
            seg_text.append(original)
            continue
        parts = _split_run(m.group("run"), soft_slides)
        for k, (kind, part) in enumerate(parts):
            before = (parts[k - 1][0] == "br") if k > 0 else None
            after = (parts[k + 1][0] == "br") if k + 1 < len(parts) else None
            if kind == "br":
                model.append("\n" if newline_mode else " ")
                seg_text.append(part)
                seg_breaks.append(part)
            elif kind == "slot":
                add_slot(part, False, m.start(), m.end(), before, after)
                seg_text.append(part)
            else:
                segments.append(Segment("".join(seg_text), seg_breaks, page))
                seg_text, seg_breaks = [], []
                add_slot(part, True, m.start(), m.end(), before, after)
                hard_order.append(len(slots))
                page += part.count("<clear>")
    if pos < len(body):
        model.append(body[pos:])
        seg_text.append(body[pos:])
    segments.append(Segment("".join(seg_text), seg_breaks, page))
    text = "".join(model)
    if not newline_mode:
        text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    spans, pos = [], 0
    for expanded, _ in words:
        m = re.compile(rf"(?<!\w){re.escape(expanded)}(?!\w)").search(text, pos)
        spans.append((m.start(), m.end()))
        pos = m.end()
    return Protected(msgid, lead, trail, text, slots, words, segments, hard_order,
                     pf, newline_mode, page_widths(msgid), spans)


def wrap_words(text: str, width: int, offset: int = 0) -> list[str]:
    words = text.split()
    lines: list[list[str]] = [[]]
    used = offset
    for w in words:
        ww = visible_width(w)
        if lines[-1] and used + 1 + ww > width:
            lines.append([w])
            used = ww
        else:
            used += (1 if lines[-1] else 0) + ww
            lines[-1].append(w)
    return [" ".join(x) for x in lines if x]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(SLIDE_BR, " ").replace(BR, " ")).strip()


@dataclass
class Restored:
    text: str | None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_WRAPPING_QUOTES = [('"', '"'), ("“", "”"), ("«", "»"), ("'", "'")]


def clean_output(raw: str, source: str) -> str:
    out = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
    for a, b in _WRAPPING_QUOTES:
        if out.startswith(a) and out.endswith(b) and len(out) > 1 \
                and not (source.startswith(a) and source.endswith(b)):
            out = out[1:-1].strip()
    return out


_WORD_RE = re.compile(r"\w+")
_STEM_TAG_RE = re.compile(rf"^({LETTER}*)({GRAMMAR_TAG})$")


def _tokens(p: Protected, text: str) -> list[tuple[int, int]]:
    rx = re.compile(rf"{p.fmt.pattern.pattern}|\w+")
    return [(m.start(), m.end()) for m in rx.finditer(text)]


def _word_alignment(p: Protected, a: str, b: str) -> dict[tuple[int, int], tuple[int, int]]:
    wa, wb = _tokens(p, a), _tokens(p, b)

    def key(text: str, s: int, e: int) -> str:
        return re.sub(r"\s+", "", text[s:e]).lower()

    sm = difflib.SequenceMatcher(None, [key(a, *x) for x in wa], [key(b, *x) for x in wb],
                                 autojunk=False)
    out = {}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for d in range(i2 - i1):
                out[wa[i1 + d]] = wb[j1 + d]
    return out


def reattach_grammar(original: str, new_word: str) -> str | None:
    m = _STEM_TAG_RE.match(original)
    if not m or not m.group(1):
        return None
    first = _GRAMMAR_ARGS_RE.match(m.group(2)).group(1).split(",")[0]
    if first and not new_word.lower().endswith(first.lower()):
        return None
    stem = new_word[:len(new_word) - len(first)]
    return stem + m.group(2) if stem else None


def _restore_words(p: Protected, out: str) -> tuple[str, list[str], list[str]]:
    errors, notes, spans = [], [], []
    pos = 0
    align = None
    for (expanded, original), src_span in zip(p.words, p.word_spans):
        m = re.compile(rf"(?<!\w){re.escape(expanded)}(?!\w)", re.IGNORECASE).search(out, pos)
        if m:
            spans.append((m.start(), m.end(), original))
            pos = m.end()
            continue
        if align is None:
            align = _word_alignment(p, p.text, out)
        dst = align.get(src_span)
        new = reattach_grammar(original, out[dst[0]:dst[1]]) if dst and dst[0] >= pos else None
        if new is None:
            errors.append(f"grammar word '{expanded}' was changed or dropped (needs {original})")
            continue
        notes.append(f"grammar tag moved to a new word: {original} -> {new} "
                     f"({grammar_forms(new)}): check every form is correct")
        spans.append((dst[0], dst[1], new))
        pos = dst[1]
    for start, end, text in reversed(spans):
        out = out[:start] + text + out[end:]
    return out, errors, notes


def _first_alternative(p: Protected, out: str, notes: list[str]) -> str:
    if p.newline_mode:
        return out
    lines = [x.strip() for x in out.split("\n") if x.strip()]
    if any(difflib.SequenceMatcher(None, a, b).ratio() > 0.6
           for i, a in enumerate(lines) for b in lines[i + 1:]):
        notes.append(f"model listed {len(lines)} alternatives; using the first")
        return lines[0]
    return out


def _drop_unknown(p: Protected, out: str, notes: list[str]) -> str:
    for m in reversed(list(p.fmt.pattern.finditer(out))):
        if int(m.group(1)) in p.slots:
            continue
        notes.append(f"removed invented placeholder {m.group(0)}")
        start, end = m.start(), m.end()
        while start > 0 and out[start - 1] in " \t":
            start -= 1
        if start > 0 and end < len(out) and out[end] == out[start - 1] and out[end] in ".!?":
            end += 1
        out = out[:start] + out[end:]
    return out.strip()


_OPENING = "¿¡\"'«“("
_CLOSING = ".,;:!?…)\"'»”"


def _reinsertable(slot: Slot) -> bool:
    return slot.hard or not any(tag_width(t) > 0 for t in TAG_RE.findall(slot.run))


def _reinsert_dropped(p: Protected, out: str, notes: list[str]) -> str:
    found = collections.Counter(int(m.group(1)) for m in p.fmt.pattern.finditer(out))
    missing = [n for n in p.slots if found[n] == 0 and _reinsertable(p.slots[n])]
    if not missing:
        return out
    align = _word_alignment(p, p.text, out)
    src_tokens = _tokens(p, p.text)
    inserts = []
    for n in missing:
        m = re.search(re.escape(p.fmt.render(n)), p.text)
        if not m:
            continue
        slot = p.slots[n]
        nxt = next((t for t in src_tokens if t[0] >= m.end()), None)
        prv = next((t for t in reversed(src_tokens) if t[1] <= m.start()), None)
        token = p.fmt.render(n)
        if nxt in align:
            at = align[nxt][0]
            between = p.text[m.end():nxt[0]]
            while at > 0 and out[at - 1] in _OPENING and (slot.hard or out[at - 1] in between):
                at -= 1
            if not slot.ws_before:
                while at > 0 and out[at - 1] in " \t":
                    at -= 1
            space = " " if slot.ws_after and at < len(out) and not out[at].isspace() else ""
            inserts.append((at, f" {token} " if slot.hard else token + space))
        elif prv in align:
            at = align[prv][1]
            between = p.text[prv[1]:m.start()]
            while at < len(out) and out[at] in _CLOSING and (slot.hard or out[at] in between):
                at += 1
            inserts.append((at, f" {token} " if slot.hard else
                            (" " if slot.ws_before else "") + token))
        else:
            continue
        notes.append(f"re-inserted dropped {slot.run} (check its position)")
    for at, token in sorted(inserts, reverse=True):
        out = out[:at] + token + out[at:]
    return out


def _tidy_pieces(p: Protected, pieces: list[str | int]) -> None:
    for k, x in enumerate(pieces):
        if not isinstance(x, int) or k == 0 or k + 1 >= len(pieces):
            continue
        slot = p.slots[x]
        prev, nxt = pieces[k - 1], pieces[k + 1]
        if slot.hard:
            stripped = prev.rstrip()
            marks = len(stripped) - len(stripped.rstrip(_OPENING[:2]))
            if marks:
                pieces[k - 1] = stripped[:len(stripped) - marks]
                pieces[k + 1] = stripped[len(stripped) - marks:] + nxt.lstrip()
            continue
        if any(tag_width(t) > 0 for t in TAG_RE.findall(slot.run)):
            continue
        space_before = prev.endswith((" ", "\t")) or (k == 1 and not prev)
        space_after = nxt.startswith((" ", "\t"))
        if space_after and space_before and not slot.ws_after:
            pieces[k + 1] = nxt.lstrip(" \t")
        elif space_before and space_after and not slot.ws_before:
            pieces[k - 1] = prev.rstrip(" \t")


def grammar_forms(word: str) -> str:
    m = GRAMMAR_TAG_RE.search(word)
    if not m or len(GRAMMAR_TAG_RE.findall(word)) != 1:
        return expand_grammar(word)
    alts = _GRAMMAR_ARGS_RE.match(m.group(0)).group(1).split(",")
    return "/".join(word[:m.start()] + a + word[m.end():] for a in alts)


def restore(p: Protected, raw: str) -> Restored:
    notes: list[str] = []
    out = clean_output(raw, p.text)
    out = _first_alternative(p, out, notes)
    if len(out) > 2 * len(p.text) + 40:
        return Restored(None, ["output much longer than the source (extra text or a loop)"])
    out = _drop_unknown(p, out, notes)
    out = _reinsert_dropped(p, out, notes)
    out, errors, word_notes = _restore_words(p, out)
    notes += word_notes
    if errors:
        return Restored(None, errors)

    pieces: list[str | int] = []
    pos = 0
    seen: collections.Counter = collections.Counter()
    for m in p.fmt.pattern.finditer(out):
        n = int(m.group(1))
        if n not in p.slots:
            errors.append(f"unknown placeholder {m.group(0)}")
            continue
        seen[n] += 1
        pieces.append(out[pos:m.start()])
        pieces.append(n)
        pos = m.end()
    pieces.append(out[pos:])
    for n in p.slots:
        if seen[n] == 0:
            errors.append(f"placeholder {p.fmt.render(n)} dropped ({p.slots[n].run})")
        elif seen[n] > 1:
            errors.append(f"placeholder {p.fmt.render(n)} repeated")
    hard_seen = [x for x in pieces if isinstance(x, int) and p.slots[x].hard]
    if not errors and hard_seen != p.hard_order:
        errors.append("line/page break placeholders moved out of order")
    if errors:
        return Restored(None, errors)
    _tidy_pieces(p, pieces)
    for x in pieces:
        if isinstance(x, int) and GRAMMAR_TAG_RE.search(p.slots[x].run):
            notes.append(f"check the position of {p.slots[x].run} "
                         f"({grammar_forms(p.slots[x].run)})")

    seg_pieces: list[list[str | int]] = [[]]
    for x in pieces:
        if isinstance(x, int) and p.slots[x].hard:
            seg_pieces.append([])
        else:
            seg_pieces[-1].append(x)

    out_segments = []
    for i, (orig, parts) in enumerate(zip(p.segments, seg_pieces)):
        text = "".join(p.slots[x].run if isinstance(x, int) else x for x in parts)
        prev_slot = p.slots[p.hard_order[i - 1]] if i > 0 else None
        next_slot = p.slots[p.hard_order[i]] if i < len(p.hard_order) else None
        if prev_slot is None or not prev_slot.ws_after:
            text = text.lstrip()
        if next_slot is None or not next_slot.ws_before:
            text = text.rstrip()
        if not _has_words(text) and _has_words(orig.text):
            return Restored(None, [f"text segment {i + 1} emptied (a line/page break moved)"])
        out_segments.append(_layout_segment(p, orig, text, notes, i))

    body = []
    for i, seg in enumerate(out_segments):
        body.append(seg)
        if i < len(p.hard_order):
            body.append(p.slots[p.hard_order[i]].run)
    return Restored(p.lead + "".join(body) + p.trail, [], notes)


def _has_words(text: str) -> bool:
    return bool(re.search(LETTER, TAG_RE.sub("", text)))


def _layout_segment(p: Protected, orig: Segment, text: str, notes: list[str], i: int) -> str:
    if _norm(text) == _norm(orig.text):
        return orig.text
    if p.newline_mode:
        lines = [x.strip() for x in re.split(r"[ \t]*\n+[ \t]*", text)]
        if len(lines) - 1 == orig.soft_breaks:
            return _join_lines(lines, orig.breaks)
        notes.append(f"segment {i}: model changed the line count, re-wrapped")
    text = re.sub(r"\s+", " ", text).strip()
    if not p.has_breaks or orig.soft_breaks == 0 and not _too_wide(p, orig, text):
        return text
    width = p.widths[min(orig.page, len(p.widths) - 1)][0]
    lines = wrap_words(text, width)
    if len(lines) > orig.soft_breaks + 1:
        entry_width = max(w for w, _ in p.widths)
        wider = wrap_words(text, entry_width)
        if len(wider) < len(lines):
            lines = wider
    return _join_lines(lines, orig.separators(len(lines) - 1))


def _join_lines(lines: list[str], separators: list[str]) -> str:
    out = [lines[0]]
    for sep, line in zip(separators, lines[1:]):
        out += [sep, line]
    return "".join(out)


def _too_wide(p: Protected, orig: Segment, text: str) -> bool:
    width = p.widths[min(orig.page, len(p.widths) - 1)][0]
    return visible_width(text) > width


def tag_problems(msgid: str, text: str) -> list[str]:
    if not text:
        return ["empty text"]
    problems = list(lint_translation(PoText(0, 0, 0, msgid, text, False, "")))
    lead_a, _, trail_a = split_edges(msgid)
    lead_b, _, trail_b = split_edges(text)
    if lead_a != lead_b:
        problems.append(f"leading tags changed: {lead_a!r} -> {lead_b!r}")
    if trail_a != trail_b:
        problems.append(f"trailing tags changed: {trail_a!r} -> {trail_b!r}")
    if msgid.count("<clear>") != text.count("<clear>"):
        problems.append(f"<clear> count {msgid.count('<clear>')} -> {text.count('<clear>')}")
    if PAIR_TAG_RE.findall(msgid) != PAIR_TAG_RE.findall(text):
        problems.append("highlight/size tags out of order")
    leftovers = set(_ANY_PLACEHOLDER_RE.findall(text)) - set(_ANY_PLACEHOLDER_RE.findall(msgid))
    if leftovers:
        problems.append("leftover placeholders " + " ".join(sorted(leftovers)))
    return problems


def layout_warnings(msgid: str, text: str) -> list[str]:
    if BR not in msgid:
        grow = visible_width(text) - visible_width(msgid)
        if grow > max(4, visible_width(msgid) // 5):
            return [f"single-line text grew by {grow} characters"]
        return []
    out = []
    before, after = page_widths(msgid), page_widths(text)
    entry_width = max(w for w, _ in before)
    for i, ((w0, n0), (w1, n1)) in enumerate(zip(before, after), 1):
        if n1 > n0:
            out.append(f"page {i}: {n0} -> {n1} lines")
        if w1 > max(w0, entry_width) + 2:
            out.append(f"page {i}: line width {w1} > {max(w0, entry_width)}")
    return out


_W = r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]"
SPAINISM_PATTERNS: list[tuple[str, str]] = [
    ("vosotros", r"\bvosotr[oa]s\b"),
    ("vuestro", r"\bvuestr[oa]s?\b"),
    ("os (clitic)", r"\b[Oo]s\b"),
    ("-áis/-éis verb", rf"\b{_W}+(?:áis|éis)\b"),
    ("-ís verb", rf"\b(?!(?:{_W}*chís|porfaplís|país|anís|maís|parís|cutís|lis)\b){_W}{{2,}}ís\b"),
    ("vosotros imperative", r"\b(?:mirad|venid|id|esperad|tened|coged|escuchad|decid|oíd|haced|"
                            r"poned|dejad|salid|corred|ayudad|traed|subid|bajad|andad|sed|"
                            r"buscad|probad|pasad|volved|seguid|venga[dn]?)\b(?=\s*[,.!?¡¿…]|\s+\w)"
                            r"(?<!\bvenga)(?<!\bvengan)"),
    ("-aos (vosotros enclitic / dropped d)", r"\b(?:" + "|".join([
        rf"{_W}+aos(?<!\bcaos)(?<!\blaos)(?<!bacalaos)",
        r"poneos", r"meteos", r"moveos", r"haceos", r"vestíos", r"idos", r"reíos", r"divertíos",
        r"veníos", r"salíos", r"subíos", r"despedíos"]) + r")\b"),
    ("coger", r"\b(?:c[oó]g(?:e|é|i|í)" + _W + r"*|c[oó]j(?:o|a|as|amos|an|áis)"
              r"(?:lo|la|los|las|le|les|me|te|nos)?\b)"),
    ("vale (interjection)", r"(?:(?<=[¡¿])|(?<=^)|(?<=[.!?…]\s))[Vv]ale\b"
                            r"(?!\s+(?:la|el|lo|los|las|más|mucho|tanto|un|una|de|por|para)\b)"
                            r"|(?<!\bno )(?<!\bse )(?<!\blo )(?<!\bque )(?<!uánto )(?<!uanto )"
                            r"(?<!\bnada )(?<!\bpoco )\b[Vv]ale(?=\s*[,.!?…])"),
    ("guay", r"\bguay(?:s|es)?\b"),
    ("mola", r"\bmol(?:a|an|aba|aban|ar|ará|aría|ado|ones?|ona)\b"),
    ("chachi", r"\bchachi\b"),
    ("piruli", r"\bpirul[ií]\b"),
    ("tío/tía (address)", r"(?:(?<=,\s)|(?<=¡)|(?<=^))[Tt][íi][oa]s?\b|(?<!\bmi )(?<!\btu )"
                          r"(?<!\bsu )(?<!\bel )(?<!\bla )(?<!\bun )(?<!\buna )\b[Tt][íi][oa]s?\b"
                          r"(?=\s*[,.!?…])"),
    ("tronco/a (address)", r"\btronc[oa]\b(?=\s*[,.!?…])"),
    ("flipar", r"\bflip(?:ar|o|as|a|an|amos|ado|ada|ados|adas|ante|antes|ando|é|ó|ará)\b"),
    ("ordenador", r"\bordenador(?:es)?\b"),
    ("móvil (phone)", r"\bmóvil(?:es)?\b"),
    ("zumo", r"\bzumos?\b"),
    ("gilipollas", r"\bgilipollas\b"),
    ("hostia", r"\bhostias?\b"),
    ("joder", r"\bjoder\b"),
    ("ostras", r"\bostras\b(?=\s*[,.!?…])|¡\s*ostras\b"),
    ("mogollón", r"\bmogoll[óo]n(?:es)?\b"),
    ("currar", r"\bcurr(?:ar|o|as|a|an|amos|ado|ando|ito)\b"),
    ("mazo", r"\ba?\s?mazo\b(?=\s+de\b|\s*[,.!?…])"),
    ("peña (people)", r"\bpeña\b"),
    ("chaval", r"\bchaval(?:a|es|as)?\b"),
    ("majo/maja", r"\bmaj(?:o|a|os|as|ete|eta)\b"),
    ("jolín", r"\bjol[ií]n(?:es)?\b"),
    ("pasada", r"\bqué pasada\b"),
    ("enfadar", r"\benfad(?:ar|o|a|as|an|ado|ada|ados|adas|arse|arte|é|ó|es|en)\b"),
    ("apetecer", r"\bapetec\w*|\bapetezc\w*"),
    ("gafas", r"\bgafas\b"),
    ("patata", r"\bpatatas?\b"),
    ("melocotón", r"\bmelocot[oó]n(?:es)?\b"),
    ("aparcar", r"\baparc(?:ar|a|as|an|ado|amiento)\b"),
    ("billete", r"\bbilletes?\b"),
    ("chulo", r"\bchul[oa]s?\b|\bchulís\w*"),
    ("cachondo", r"\bcachond\w*"),
    ("chungo", r"\bchung[oa]s?\b"),
    ("pringado", r"\bpringad[oa]s?\b"),
    ("gamberro", r"\bgamberr\w*"),
    ("chorrada", r"\bchorrad\w*"),
    ("en plan", r"\ben plan\b(?!\s+de\b)"),
    ("a tope", r"\ba tope\b"),
    ("vaya tela", r"\bvaya tela\b|¡tela\b"),
    ("venga (interjection)", r"(?:(?<=[¡¿])|(?<=^)|(?<=[.!?…]\s))[Vv]enga\b(?=\s*[,.!?…])"),
]
_SPAINISM_RES = [(label, re.compile(rx, re.MULTILINE)) for label, rx in SPAINISM_PATTERNS]


def spainisms(text: str) -> list[tuple[str, str]]:
    plain = plain_text(text)
    found = []
    for label, rx in _SPAINISM_RES:
        for m in rx.finditer(plain):
            found.append((label, m.group(0).strip()))
    return found


def is_spain_only(msgid: str) -> bool:
    return bool(spainisms(msgid))


_USTED_RE = re.compile(r"\busted\b", re.IGNORECASE)
_VOSEO_RE = re.compile(r"\b(?:vos|sos|tenés|querés|podés|sabés|hacés|venís|decís|"
                       r"mirá|vení|tomá|decime|contame|fijate)\b", re.IGNORECASE)


def register_warnings(msgid: str, text: str) -> list[str]:
    out = []
    a, b = plain_text(msgid), plain_text(text)
    if len(_USTED_RE.findall(b)) > len(_USTED_RE.findall(a)):
        out.append("formal 'usted' introduced")
    if len(_VOSEO_RE.findall(b)) > len(_VOSEO_RE.findall(a)):
        out.append("voseo introduced")
    return out


_REPEAT_RE = re.compile(r"\b(\w{4,})\w{0,3}\s+\1\w{0,3}\b", re.IGNORECASE)


def repeat_warnings(msgid: str, text: str) -> list[str]:
    before = {m.group(0).lower() for m in _REPEAT_RE.finditer(plain_text(msgid))}
    return [f"repeated word: {m.group(0)}" for m in _REPEAT_RE.finditer(plain_text(text))
            if m.group(0).lower() not in before]


def review_warnings(msgid: str, text: str) -> list[str]:
    out = [f"Spain-ism left: {w} ({label})" for label, w in spainisms(text)]
    out += register_warnings(msgid, text)
    out += repeat_warnings(msgid, text)
    out += layout_warnings(msgid, text)
    return out
