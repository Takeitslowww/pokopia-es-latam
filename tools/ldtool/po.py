from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_KEYWORD_RE = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(".*")\s*$')
_SIMPLE_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r",
                   "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


class PoSyntaxError(ValueError):
    pass


@dataclass
class PoEntry:
    msgid: str = ""
    msgstr: str = ""
    msgctxt: str | None = None
    msgid_plural: str | None = None
    msgstr_plural: dict[int, str] = field(default_factory=dict)
    translator_comments: list[str] = field(default_factory=list)
    extracted_comments: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    previous: list[str] = field(default_factory=list)
    obsolete: bool = False
    lineno: int = 0

    @property
    def fuzzy(self) -> bool:
        return "fuzzy" in self.flags

    @property
    def is_header(self) -> bool:
        return self.msgid == "" and self.msgctxt is None and not self.obsolete


@dataclass
class PoFile:
    entries: list[PoEntry]
    header: list[tuple[str, str]]
    header_comments: list[str]
    path: str | None = None

    def header_value(self, key: str) -> str | None:
        for k, v in self.header:
            if k.lower() == key.lower():
                return v
        return None


def unquote(literal: str, where: str = "") -> str:
    if len(literal) < 2 or literal[0] != '"' or literal[-1] != '"':
        raise PoSyntaxError(f"{where}: expected a quoted string, got {literal!r}")
    body = literal[1:-1]
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            raise PoSyntaxError(f"{where}: unescaped quote inside string")
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise PoSyntaxError(f"{where}: dangling backslash")
        esc = body[i]
        if esc in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[esc])
            i += 1
        elif esc in "01234567":
            j = i
            while j < len(body) and j < i + 3 and body[j] in "01234567":
                j += 1
            out.append(chr(int(body[i:j], 8)))
            i = j
        elif esc == "x":
            j = i + 1
            while j < len(body) and body[j] in "0123456789abcdefABCDEF":
                j += 1
            if j == i + 1:
                raise PoSyntaxError(f"{where}: bad \\x escape")
            out.append(chr(int(body[i + 1:j], 16)))
            i = j
        else:
            raise PoSyntaxError(f"{where}: unknown escape \\{esc}")
    return "".join(out)


def parse_po(text: str, filename: str = "<po>") -> PoFile:
    if text.startswith("﻿"):
        text = text[1:]
    entries: list[PoEntry] = []
    cur = PoEntry()
    has_keyword = False
    last_key: tuple[str, int | None] | None = None

    def finish() -> None:
        nonlocal cur, has_keyword, last_key
        if has_keyword:
            entries.append(cur)
        cur = PoEntry()
        has_keyword = False
        last_key = None

    def append_to(key: tuple[str, int | None], value: str) -> None:
        name, n = key
        if name == "msgctxt":
            cur.msgctxt = (cur.msgctxt or "") + value
        elif name == "msgid":
            cur.msgid += value
        elif name == "msgid_plural":
            cur.msgid_plural = (cur.msgid_plural or "") + value
        elif n is not None:
            cur.msgstr_plural[n] = cur.msgstr_plural.get(n, "") + value
        else:
            cur.msgstr += value

    for lineno, raw in enumerate(text.splitlines(), 1):
        where = f"{filename}:{lineno}"
        line = raw.strip()
        if not line:
            finish()
            continue
        obsolete = False
        if line.startswith("#~"):
            obsolete = True
            line = line[2:].strip()
            if line.startswith("|"):
                if has_keyword and last_key and last_key[0] == "msgstr":
                    finish()
                cur.previous.append(line[1:].strip())
                continue
            if not line:
                continue
        elif line.startswith("#"):
            if has_keyword:
                finish()
            kind, payload = line[1:2], line[2:]
            if kind == ".":
                cur.extracted_comments.append(payload.strip())
            elif kind == ":":
                cur.references.extend(payload.split())
            elif kind == ",":
                cur.flags.extend(f.strip() for f in payload.split(",") if f.strip())
            elif kind == "|":
                cur.previous.append(payload.strip())
            else:
                cur.translator_comments.append(line[2:] if line[1:2] == " " else line[1:])
            continue
        m = _KEYWORD_RE.match(line)
        if m:
            name, n, literal = m.group(1), m.group(2), m.group(3)
            if name.startswith("msgstr"):
                key = ("msgstr", int(n) if n is not None else None)
            else:
                key = (name, None)
            if has_keyword and key[0] in ("msgctxt", "msgid") and last_key and last_key[0] == "msgstr":
                finish()
            if not has_keyword:
                cur.lineno = lineno
            has_keyword = True
            cur.obsolete = cur.obsolete or obsolete
            last_key = key
            append_to(key, unquote(literal, where))
            continue
        if line.startswith('"'):
            if last_key is None:
                raise PoSyntaxError(f"{where}: string continuation without a keyword")
            append_to(last_key, unquote(line, where))
            continue
        raise PoSyntaxError(f"{where}: cannot parse line {raw!r}")
    finish()

    header: list[tuple[str, str]] = []
    header_comments: list[str] = []
    body = []
    for e in entries:
        if e.is_header and not header and not body:
            header_comments = e.translator_comments
            for hline in e.msgstr.split("\n"):
                if ":" in hline:
                    k, v = hline.split(":", 1)
                    header.append((k.strip(), v.strip()))
        else:
            body.append(e)
    return PoFile(body, header, header_comments, filename)


def read_po(path: str | os.PathLike) -> PoFile:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_po(fh.read(), os.fspath(path))


def quote(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _string_lines(keyword: str, value: str, prefix: str = "") -> list[str]:
    if "\n" in value[:-1]:
        parts = value.splitlines(keepends=True)
        return [f"{prefix}{keyword} \"\""] + [f"{prefix}{quote(p)}" for p in parts]
    return [f"{prefix}{keyword} {quote(value)}"]


def format_entry(e: PoEntry) -> str:
    lines = []
    lines += [f"# {c}" if c else "#" for c in e.translator_comments]
    lines += [f"#. {c}" for c in e.extracted_comments]
    if e.references:
        lines.append("#: " + " ".join(e.references))
    if e.flags:
        lines.append("#, " + ", ".join(e.flags))
    prefix = "#~ " if e.obsolete else ""
    lines += [(f"#~| {p}" if e.obsolete else f"#| {p}") for p in e.previous]
    if e.msgctxt is not None:
        lines += _string_lines("msgctxt", e.msgctxt, prefix)
    lines += _string_lines("msgid", e.msgid, prefix)
    if e.msgid_plural is not None:
        lines += _string_lines("msgid_plural", e.msgid_plural, prefix)
        for n in sorted(e.msgstr_plural):
            lines += _string_lines(f"msgstr[{n}]", e.msgstr_plural[n], prefix)
    else:
        lines += _string_lines("msgstr", e.msgstr, prefix)
    return "\n".join(lines)


def format_po(po: PoFile) -> str:
    chunks = []
    head = [f"# {c}" if c else "#" for c in po.header_comments]
    head.append('msgid ""')
    head.append('msgstr ""')
    head += [quote(f"{k}: {v}\n") for k, v in po.header]
    chunks.append("\n".join(head))
    chunks += [format_entry(e) for e in po.entries]
    return "\n\n".join(chunks) + "\n"


def write_po(path: str | os.PathLike, po: PoFile) -> None:
    tmp = os.fspath(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(format_po(po))
    os.replace(tmp, path)
