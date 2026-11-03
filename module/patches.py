from __future__ import annotations

import struct
from dataclasses import dataclass

BUILD_ID = "76A489F3BD3642FA5E18BD83D32C98BACC5F7D0E"
MODULE_OFFSET = 0x3E60000
NSO_HEADER_SIZE = 0x100

LANGUAGES = {
    "LANG_JA": 0, "LANG_EN": 1, "LANG_FR": 2, "LANG_IT": 3, "LANG_DE": 4,
    "LANG_ES": 5, "LANG_ZH_HANT": 6, "LANG_KO": 7, "LANG_ZH_HANS": 8,
    "LANG_LATAM": 9,
}

GAME = {
    "GRID_BUILD": 0xFFB060,
    "PATCH_LOOP_NEXT": 0x11238C8,
    "PATCH_TABLE_FROM_X22": 0x650,
    "LANG_STORE_RESUME": 0x2306E4,
    "CURRENT_LANGUAGE_PTR": 0x36CA680,
    "HID_GET_NPAD_STYLE_SET": 0x27468A0,
    "HID_GET_NPAD_STATE_FULLKEY": 0x27468B0,
}

LINKDATA_RECORDS = 12298
PATCH_MARKER_LATAM = 0x4C41544D


def ins(word: int) -> bytes:
    return struct.pack("<I", word)


@dataclass(frozen=True)
class Patch:
    name: str
    offset: int
    original: bytes
    kind: str
    new: bytes = b""
    target: str = ""
    debug_only: bool = False
    note: str = ""


PATCHES: list[Patch] = [
    Patch("H1_GRID_ENTRIES", 0xFFA520, ins(0x940002D0), "bl", target="hook_grid_entries",
          note="was `bl 0xffb060`; append entry {0x2f, n, 9} to the language list, then build"),
    Patch("GRID_SHOW_10", 0xFFB138, ins(0x7100277F), "bytes", ins(0x71002B7F),
          note="cmp w27,#9 -> #10: button for language 9 is visible"),
    Patch("GRID_LABEL_9", 0xFFB1F0, ins(0x7100237F), "bytes", ins(0x7100277F),
          note="cmp w27,#8 -> #9: language 9 gets a label from the table below"),
    Patch("LABEL_9", 0x283EBCE, b"\x00\x00", "bytes", b"\x02\x01",
          note="u16 label resource for language 9 (padding after the 9-entry table at "
               "0x283ebbc) = 0x102, a row of the label table (LINKDATA record 1552) that is "
               "empty in the game; LINKDATA_PATCH fills it (assettool build): the es-419 "
               "label sprite, or a copy of the 'Espanol (Espana)' row 0x101"),
    Patch("CONFIRM_ROW_CHECK", 0xFFB010, ins(0x710022DF), "bytes", ins(0x710026DF),
          note="cmp w22,#8 -> #9: language 9 has a confirm prompt"),
    Patch("CONFIRM_ROW_9", 0x283EBBA, b"\x00\x00", "bytes", b"\x50\x00",
          note="u16 confirm-prompt row for language 9 = 80 (row 80 of records 2323-2331)"),
    Patch("BOOT_ACCEPT_9", 0x230F5C, ins(0x7100201F), "bytes", ins(0x7100241F),
          note="cmp w0,#8 -> #9: a saved m_Language of 9 is accepted at boot"),
    Patch("H_CMP_BOOT", 0x230F74, ins(0xB940012A), "bl", target="hook_lang_compare",
          note="was `ldr w10,[x9]`; boot: map 9 -> 5 + Latam on, force the change callback"),
    Patch("H_CMP_MENU", 0xFFAA38, ins(0xB940012A), "bl", target="hook_lang_compare",
          note="was `ldr w10,[x9]`; grid confirm: same as boot"),
    Patch("H4_STORE", 0x2306E0, ins(0xB8296900), "b", target="hook_lang_store",
          note="was `str w0,[x8,x9]` (x30 live: `b`); save 9 instead of 5 while Latam is on"),
    Patch("H5_CONFIRM", 0xFFACA4, ins(0xB9400108), "bl", target="hook_lang_current",
          note="was `ldr w8,[x8]`; current language reads as 9 while Latam is on"),
    Patch("H5_GRID", 0xFFB11C, ins(0xB9400108), "bl", target="hook_lang_current",
          note="was `ldr w8,[x8]`; grid marks the Latam button as current"),
    Patch("H_IDX", 0x11238DC, ins(0xB4FFFF6C), "bl", target="hook_patch_entry",
          note="was `cbz x12,0x11238c8`; keep 'LATM'-marked LINKDATA_PATCH entries for swapping"),
    Patch("DBG_NPAD_STYLE", 0x1127478, ins(0x94587D0A), "bl", target="hook_dbg_npad_style",
          debug_only=True, note="was `bl GetNpadStyleSet` (player 1)"),
    Patch("DBG_NPAD_STATE", 0x11274F0, ins(0x94587CF0), "bl", target="hook_dbg_npad_state",
          debug_only=True, note="was `bl GetNpadState(NpadFullKeyState*)` (player 1)"),
]


def branch(kind: str, src: int, dst: int) -> bytes:
    delta = dst - src
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError(f"branch {src:#x} -> {dst:#x} out of range")
    op = {"b": 0x14000000, "bl": 0x94000000}[kind]
    return ins(op | ((delta >> 2) & 0x3FFFFFF))


def selected(debug: bool) -> list[Patch]:
    return [p for p in PATCHES if debug or not p.debug_only]


def resolve(patches: list[Patch], module_symbols: dict[str, int]) -> list[tuple[Patch, bytes]]:
    out = []
    for p in patches:
        if p.kind == "bytes":
            data = p.new
        elif p.kind in ("b", "bl"):
            if p.target not in module_symbols:
                raise KeyError(f"{p.name}: module symbol {p.target!r} not found")
            data = branch(p.kind, p.offset, MODULE_OFFSET + module_symbols[p.target])
        else:
            raise ValueError(f"{p.name}: unknown kind {p.kind!r}")
        if len(data) != len(p.original):
            raise ValueError(f"{p.name}: new data size differs from the original")
        out.append((p, data))
    return out


def check_original(patches: list[Patch], main_image: bytes) -> list[str]:
    problems = []
    for p in patches:
        got = main_image[p.offset:p.offset + len(p.original)]
        if got != p.original:
            problems.append(f"{p.name} @ {p.offset:#x}: expected {p.original.hex()}, found {got.hex()}")
    spans = sorted((p.offset, p.offset + len(p.original), p.name) for p in patches)
    for (a0, a1, an), (b0, _b1, bn) in zip(spans, spans[1:]):
        if b0 < a1:
            problems.append(f"{an} and {bn} overlap")
    return problems


def ips32(records: list[tuple[int, bytes]]) -> bytes:
    out = bytearray(b"IPS32")
    for offset, data in sorted(records):
        file_offset = NSO_HEADER_SIZE + offset
        if file_offset == 0x45454F46:
            raise ValueError("offset collides with the IPS32 footer")
        if not 0 < len(data) <= 0xFFFF:
            raise ValueError("bad record size")
        out += struct.pack(">IH", file_offset, len(data)) + data
    out += b"EEOF"
    return bytes(out)


def header_text() -> str:
    lines = ["/* Generated by module/build.py from module/patches.py - do not edit. */",
             "#pragma once",
             f"#define MODULE_OFFSET 0x{MODULE_OFFSET:X}",
             f"#define LINKDATA_RECORDS {LINKDATA_RECORDS}",
             f"#define PATCH_MARKER_LATAM 0x{PATCH_MARKER_LATAM:X}"]
    lines += [f"#define {k} {v}" for k, v in LANGUAGES.items()]
    lines += [f"#define GAME_{k} 0x{v:X}" for k, v in GAME.items()]
    return "\n".join(lines) + "\n"
