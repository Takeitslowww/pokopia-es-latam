from __future__ import annotations

import re
import unicodedata

ESCAPE_RE = re.compile(r"<U\+([0-9A-Fa-f]{4,6})>")
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})


def needs_escape(ch: str) -> bool:
    return unicodedata.category(ch) in _ESCAPED_CATEGORIES


def to_display(text: str) -> str:
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "<" and ESCAPE_RE.match(text, i):
            out.append("<U+003C>")
        elif needs_escape(ch):
            out.append(f"<U+{ord(ch):04X}>")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _unescape(match: re.Match) -> str:
    code = int(match.group(1), 16)
    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        raise ValueError(f"invalid escape {match.group(0)}")
    return chr(code)


def from_display(text: str) -> str:
    return ESCAPE_RE.sub(_unescape, text)


TAG_RE = re.compile(r"<[^<>]*>")


def tags(text: str) -> list[str]:
    return TAG_RE.findall(text)
