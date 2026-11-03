from __future__ import annotations

import collections
import json
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import __version__
from .container import (DEFAULT_BLOCK_SIZE, DEFAULT_ENTRY_ALIGN, LATAM_MARKER, LinkData,
                        StoredBlob, pack_container, write_linkdata)
from .escape import from_display, tags, to_display
from .gamedata import (DEFAULT_ROMFS, RECORD_COUNT, SPARE_FIRST_INDEX, SPECIES_RECORD, TextUnit,
                       is_table_record, localized_records, slot_of, spanish_units)
from .mapping import resolve_entry_count, resolve_mapping
from .po import PoEntry, PoFile, quote, read_po, write_po
from .table import (TableError, TextTable, parse_table, serialize_table,
                    serialize_table_append)

Log = Callable[[str], None]

TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_ROOT = os.path.dirname(TOOLS_DIR)
DEFAULT_PO_DIR = os.path.join(MOD_ROOT, "text", "es-419")
DEFAULT_ALWAYS_ON_DIR = os.path.join(MOD_ROOT, "text", "always-on")
DEFAULT_OUT_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
    "eden", "load", "0400A3A01EE4C000", "ES-LATAM", "romfs")
LAYOUT_NAMES = {
    "patch": ("LINKDATA_PATCH.IDX", "LINKDATA_PATCH.BIN"),
    "archive": ("LI_C0_R_LATAM.IDX", "LD_C0_R_LATAM.BIN"),
}

CTXT_RE = re.compile(r"^(\d+):(\d+)(?::(\d+))?$")
LAYOUT_TAGS = frozenset({"<br>", "<clear>", "<slide>", "<oneline>"})


class PipelineError(RuntimeError):
    pass


def _print(msg: str) -> None:
    print(msg, flush=True)


def po_filename(record: int) -> str:
    return f"table_{record}.po"


def make_ctxt(record: int, row: int, col: int, multi_column: bool) -> str:
    return f"{record}:{row}:{col}" if multi_column else f"{record}:{row}"


def parse_ctxt(ctxt: str) -> tuple[int, int, int]:
    m = CTXT_RE.match(ctxt or "")
    if not m:
        raise ValueError(f"bad msgctxt {ctxt!r} (expected <record>:<row>[:<col>])")
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


@dataclass
class ExtractStats:
    files: int = 0
    entries: int = 0
    kept: int = 0
    fuzzy: int = 0
    obsolete: int = 0


def _header(unit: TextUnit) -> tuple[list[str], list[tuple[str, str]]]:
    if unit.record == SPECIES_RECORD:
        source = (f"LINKDATA C0_R record {unit.record}, column {unit.columns[0]} (es-ES); "
                  f"English reference: column {unit.english_column(unit.columns[0])}.")
    else:
        source = (f"LINKDATA C0_R record {unit.record} (es-ES slot); "
                  f"English reference: record {unit.english_record}.")
    comments = [
        "Pokemon Pokopia - Latin American Spanish (es-419).",
        source,
        "msgid = original Spain-Spanish text. Fill msgstr only where the LatAm text",
        "differs; empty msgstr keeps the original. Keep <...> tags intact; <U+XXXX>",
        "stands for an invisible character. See tools/README.md.",
    ]
    header = [
        ("Project-Id-Version", "pokopia-es-latam"),
        ("Language", "es_419"),
        ("MIME-Version", "1.0"),
        ("Content-Type", "text/plain; charset=UTF-8"),
        ("Content-Transfer-Encoding", "8bit"),
        ("Plural-Forms", "nplurals=2; plural=(n != 1);"),
        ("X-Generator", f"ldtool {__version__}"),
        ("X-Ldtool-Record", str(unit.record)),
        ("X-Ldtool-English-Record", str(unit.english_record)),
    ]
    return comments, header


def _unit_entries(unit: TextUnit, es: TextTable, en: TextTable | None) -> list[PoEntry]:
    multi = es.schema.string_count > 1
    cols = unit.columns if unit.columns is not None else range(es.schema.string_count)
    entries = []
    for row in range(es.count):
        for col in cols:
            text = es.text(row, col)
            if not text:
                continue
            entry = PoEntry(msgctxt=make_ctxt(unit.record, row, col, multi),
                            msgid=to_display(text))
            if en is not None and row < en.count:
                en_text = en.text(row, unit.english_column(col))
                entry.extracted_comments.append(f"en: {to_display(en_text)}" if en_text
                                                else "en: (empty)")
            entries.append(entry)
    return entries


def _merge(new: PoFile, old: PoFile, stats: ExtractStats) -> None:
    old_by_ctxt = {e.msgctxt: e for e in old.entries if not e.obsolete and e.msgctxt}
    for e in new.entries:
        o = old_by_ctxt.pop(e.msgctxt, None)
        if o is None:
            continue
        e.translator_comments = list(o.translator_comments)
        e.msgstr = o.msgstr
        e.flags = [f for f in o.flags if f != "fuzzy"]
        if o.msgstr and o.msgid != e.msgid:
            e.flags.append("fuzzy")
            e.previous = [f"msgid {quote(o.msgid)}"]
            stats.fuzzy += 1
        elif o.fuzzy:
            e.flags.append("fuzzy")
            e.previous = list(o.previous)
        if o.msgstr:
            stats.kept += 1
    for o in list(old_by_ctxt.values()) + [e for e in old.entries if e.obsolete]:
        if o.msgstr:
            o.obsolete = True
            new.entries.append(o)
            stats.obsolete += 1
    keys = {k.lower() for k, _ in new.header}
    new.header += [(k, v) for k, v in old.header if k.lower() not in keys]


def extract(romfs: str = DEFAULT_ROMFS, out_dir: str = DEFAULT_PO_DIR, *,
            include_species: bool = True, force: bool = False,
            records: Iterable[int] | None = None, log: Log = _print) -> ExtractStats:
    wanted = set(records) if records is not None else None
    os.makedirs(out_dir, exist_ok=True)
    stats = ExtractStats()
    with LinkData.open_dir(romfs) as ld:
        for unit in spanish_units(include_species):
            if wanted is not None and unit.record not in wanted:
                continue
            es = parse_table(ld.read(unit.record))
            if unit.english_record == unit.record:
                en: TextTable | None = es
            else:
                en = parse_table(ld.read(unit.english_record))
                if en.count != es.count or en.schema != es.schema:
                    log(f"warning: record {unit.english_record} does not match {unit.record}; "
                        "no English comments")
                    en = None
            comments, header = _header(unit)
            po = PoFile(_unit_entries(unit, es, en), header, comments)
            path = os.path.join(out_dir, po_filename(unit.record))
            if os.path.exists(path) and not force:
                _merge(po, read_po(path), stats)
            write_po(path, po)
            stats.files += 1
            stats.entries += sum(1 for e in po.entries if not e.obsolete)
    log(f"extract: {stats.files} files, {stats.entries} entries -> {out_dir}"
        f" (kept {stats.kept} translations, {stats.fuzzy} now fuzzy, {stats.obsolete} obsolete)")
    return stats


@dataclass(frozen=True)
class PoText:
    record: int
    row: int
    col: int
    source_text: str
    text: str
    fuzzy: bool
    where: str

    @property
    def key(self) -> tuple[int, int, int]:
        return self.record, self.row, self.col


def load_po_texts(po_dir: str) -> dict[tuple[int, int, int], PoText]:
    if not os.path.isdir(po_dir):
        raise PipelineError(f"po directory not found: {po_dir}")
    paths = []
    for root, _dirs, files in os.walk(po_dir):
        paths += [os.path.join(root, f) for f in files if f.endswith(".po")]
    result: dict[tuple[int, int, int], PoText] = {}
    for path in sorted(paths):
        po = read_po(path)
        for e in po.entries:
            if e.obsolete:
                continue
            where = f"{os.path.relpath(path, po_dir)}:{e.lineno}"
            if e.msgctxt is None:
                raise PipelineError(f"{where}: entry without msgctxt")
            try:
                rec, row, col = parse_ctxt(e.msgctxt)
                item = PoText(rec, row, col, from_display(e.msgid), from_display(e.msgstr),
                              e.fuzzy, where)
            except ValueError as exc:
                raise PipelineError(f"{where}: {exc}") from None
            if item.key in result:
                raise PipelineError(f"{where}: duplicate msgctxt {e.msgctxt} "
                                    f"(also in {result[item.key].where})")
            result[item.key] = item
    return result


def lint_translation(t: PoText) -> list[str]:
    problems = []
    src = collections.Counter(x for x in tags(t.source_text) if x not in LAYOUT_TAGS)
    dst = collections.Counter(x for x in tags(t.text) if x not in LAYOUT_TAGS)
    missing, extra = src - dst, dst - src
    if missing:
        problems.append("missing tags " + " ".join(sorted(missing.elements())))
    if extra:
        problems.append("extra tags " + " ".join(sorted(extra.elements())))
    if "\n" in t.text or "\r" in t.text:
        problems.append("raw line break (use <br>)")
    if t.text.count("<") != t.text.count(">"):
        problems.append("unbalanced < >")
    return problems


@dataclass(frozen=True)
class Override:
    record: int
    row: int
    col: int
    text: str | None
    ints: tuple[int, ...] | None
    where: str


def load_always_on(directory: str | None) -> dict[int, list[Override]]:
    if not directory or not os.path.isdir(directory):
        return {}
    result: dict[int, list[Override]] = collections.defaultdict(list)
    seen: dict[tuple[int, int, int], str] = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise PipelineError(f"{path}: {exc}") from None
        rows = doc.get("rows") if isinstance(doc, dict) else None
        if not isinstance(rows, dict):
            raise PipelineError(f"{path}: expected an object with a \"rows\" object")
        for key, spec in rows.items():
            where = f"{name}:{key}"
            try:
                rec, row, col = parse_ctxt(key)
            except ValueError as exc:
                raise PipelineError(f"{where}: {exc}") from None
            if not isinstance(spec, dict) or not spec or not set(spec) <= {"text", "ints"}:
                raise PipelineError(f"{where}: expected {{\"text\": ..., \"ints\": [...]}}")
            text, ints = spec.get("text"), spec.get("ints")
            if text is not None and not isinstance(text, str):
                raise PipelineError(f"{where}: text must be a string")
            if ints is not None and not (isinstance(ints, list) and all(
                    isinstance(v, int) and not isinstance(v, bool) for v in ints)):
                raise PipelineError(f"{where}: ints must be a list of integers")
            if not (is_table_record(rec) or rec == SPECIES_RECORD):
                raise PipelineError(f"{where}: record {rec} is not a localized text record")
            if (rec, row, col) in seen:
                raise PipelineError(f"{where}: duplicate (also in {seen[rec, row, col]})")
            seen[rec, row, col] = where
            try:
                game_text = from_display(text) if text is not None else None
            except ValueError as exc:
                raise PipelineError(f"{where}: {exc}") from None
            result[rec].append(Override(rec, row, col, game_text,
                                        tuple(ints) if ints is not None else None, where))
    return dict(result)


def apply_overrides(table: TextTable, overrides: Iterable[Override]) -> list[str]:
    errors = []
    for o in overrides:
        if o.row >= table.count:
            errors.append(f"{o.where}: row {o.row} out of range ({table.count} rows)")
            continue
        try:
            if o.text is not None:
                table.set_text(o.row, o.col, o.text)
            if o.ints is not None:
                table.set_ints(o.row, list(o.ints))
        except (TableError, IndexError, TypeError) as exc:
            errors.append(f"{o.where}: {exc}")
    return errors


@dataclass
class BuildConfig:
    romfs: str = DEFAULT_ROMFS
    po_dir: str = DEFAULT_PO_DIR
    out_dir: str = DEFAULT_OUT_DIR
    layout: str = "patch"
    bin_name: str | None = None
    idx_name: str | None = None
    map_spec: str = "identity"
    idx_entries: str = "full"
    records: str | None = None
    include_species: bool = True
    fill: str = "empty"
    include_fuzzy: bool = False
    strict: bool = False
    serializer: str = "canonical"
    entry_align: int = DEFAULT_ENTRY_ALIGN
    block_size: int = DEFAULT_BLOCK_SIZE
    always_on_dir: str | None = DEFAULT_ALWAYS_ON_DIR
    manifest: str | None = None
    extra_records: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.layout not in LAYOUT_NAMES:
            raise PipelineError(f"unknown layout {self.layout!r} (patch | archive)")
        if self.extra_records and self.layout != "patch":
            raise PipelineError("extra records need the patch layout")

    @property
    def idx_path(self) -> str:
        return os.path.join(self.out_dir, self.idx_name or LAYOUT_NAMES[self.layout][0])

    @property
    def bin_path(self) -> str:
        return os.path.join(self.out_dir, self.bin_name or LAYOUT_NAMES[self.layout][1])

    @property
    def selection(self) -> str:
        return self.records or ("changed" if self.layout == "patch" else "spanish")


@dataclass
class RecordResult:
    record: int
    data: bytes
    table: TextTable
    original: TextTable
    applied: int


@dataclass
class Prepared:
    records: dict[int, RecordResult]
    translations: dict[tuple[int, int, int], PoText]
    warnings: list[str] = field(default_factory=list)


def _active(t: PoText, include_fuzzy: bool) -> bool:
    return bool(t.text) and (include_fuzzy or not t.fuzzy)


def _serialize(cfg: BuildConfig, raw: bytes, table: TextTable) -> bytes:
    if cfg.serializer == "append":
        return serialize_table_append(raw, table)
    if cfg.serializer == "canonical":
        return serialize_table(table)
    raise PipelineError(f"unknown serializer {cfg.serializer!r}")


def _read_original(cfg: BuildConfig, ld: LinkData, rec: int,
                   errors: list[str]) -> tuple[bytes, TextTable] | None:
    raw = ld.read(rec)
    original = parse_table(raw)
    if cfg.serializer == "canonical" and serialize_table(original) != raw:
        errors.append(f"record {rec}: not in canonical layout (game data changed?); "
                      "use --serializer append")
        return None
    return raw, original


def prepare_records(cfg: BuildConfig, ld: LinkData, log: Log = _print) -> Prepared:
    units = {u.record: u for u in spanish_units(cfg.include_species)}
    texts = load_po_texts(cfg.po_dir)
    prepared = Prepared({}, texts)
    errors: list[str] = []
    by_record: dict[int, list[PoText]] = collections.defaultdict(list)
    for t in texts.values():
        if t.record not in units:
            errors.append(f"{t.where}: record {t.record} is not a target record")
            continue
        by_record[t.record].append(t)

    for rec, unit in units.items():
        loaded = _read_original(cfg, ld, rec, errors)
        if loaded is None:
            continue
        raw, original = loaded
        table = original.copy()
        applied = 0
        for t in sorted(by_record.get(rec, []), key=lambda x: x.key):
            if t.row >= table.count:
                errors.append(f"{t.where}: row {t.row} out of range ({table.count} rows)")
                continue
            if t.col >= table.schema.string_count or (
                    unit.columns is not None and t.col not in unit.columns):
                errors.append(f"{t.where}: column {t.col} is not translatable in record {rec}")
                continue
            current = original.text(t.row, t.col)
            if t.source_text != current:
                prepared.warnings.append(f"{t.where}: msgid differs from the game text "
                                         "(stale .po? re-run extract)")
            if not _active(t, cfg.include_fuzzy):
                continue
            for problem in lint_translation(t):
                prepared.warnings.append(f"{t.where}: {problem}")
            if t.text != current:
                try:
                    table.set_text(t.row, t.col, t.text)
                except (TableError, TypeError) as exc:
                    errors.append(f"{t.where}: {exc}")
                    continue
                applied += 1
        data = _serialize(cfg, raw, table)
        if not parse_table(data, table.schema).same_content(table):
            errors.append(f"record {rec}: serialized data does not parse back identically")
            continue
        prepared.records[rec] = RecordResult(rec, data, table, original, applied)

    if cfg.strict and prepared.warnings:
        errors += [f"(strict) {w}" for w in prepared.warnings]
    if errors:
        for e in errors[:50]:
            log(f"error: {e}")
        raise PipelineError(f"{len(errors)} error(s); nothing written")
    return prepared


def _selected(cfg: BuildConfig, prepared: Prepared) -> dict[int, RecordResult]:
    if cfg.selection == "spanish":
        return dict(prepared.records)
    if cfg.selection == "changed":
        return {r: res for r, res in prepared.records.items() if res.applied}
    raise PipelineError(f"unknown record selection {cfg.selection!r}")


def _layout(cfg: BuildConfig, selected: dict[int, RecordResult], source_count: int):
    mapping = resolve_mapping(cfg.map_spec, selected)
    count = resolve_entry_count(cfg.idx_entries, mapping, source_count)
    return mapping, count


@dataclass
class PatchEntry:
    index: int
    record: int
    latam: bool
    data: bytes
    changed_cells: int
    raw: bool = False

    @property
    def size_field(self) -> int:
        return len(self.data) | ((LATAM_MARKER << 32) if self.latam else 0)

    @property
    def flag_field(self) -> int:
        return (self.record << 32) if self.index != self.record else 0


def _patch_plan(cfg: BuildConfig, ld: LinkData, prepared: Prepared,
                overrides: dict[int, list[Override]], log: Log) -> list[PatchEntry]:
    if cfg.map_spec != "identity" or cfg.fill != "empty" or cfg.idx_entries != "full":
        raise PipelineError("the patch layout needs --map identity, --fill empty and "
                            "--idx-entries full (other values are for --layout archive)")
    if len(ld) != RECORD_COUNT:
        raise PipelineError(f"source IDX has {len(ld)} entries, expected {RECORD_COUNT}")
    errors: list[str] = []
    plan: list[PatchEntry] = []
    for rec in sorted(overrides):
        loaded = _read_original(cfg, ld, rec, errors)
        if loaded is None:
            continue
        raw, original = loaded
        table = original.copy()
        errors += apply_overrides(table, overrides[rec])
        plan.append(PatchEntry(rec, rec, False, _serialize(cfg, raw, table), 0))
    selected = _selected(cfg, prepared)
    for rec in sorted(cfg.extra_records):
        if rec in overrides or rec in selected or not 0 <= rec < SPARE_FIRST_INDEX:
            errors.append(f"extra record {rec}: already a text record or out of range")
            continue
        plan.append(PatchEntry(rec, rec, False, bytes(cfg.extra_records[rec]), 0, raw=True))
    spare = SPARE_FIRST_INDEX
    for rec, res in sorted(selected.items()):
        if rec not in overrides:
            plan.append(PatchEntry(rec, rec, True, res.data, res.applied))
            continue
        while spare < len(ld) and not ld.entry(spare).is_empty:
            spare += 1
        if spare >= len(ld):
            errors.append(f"record {rec}: no free index left for the Latam entry")
            continue
        table = res.table.copy()
        errors += apply_overrides(table, overrides[rec])
        plan.append(PatchEntry(spare, rec, True, _serialize(cfg, ld.read(rec), table),
                               res.applied))
        spare += 1
    if errors:
        for e in errors[:50]:
            log(f"error: {e}")
        raise PipelineError(f"{len(errors)} error(s); nothing written")
    return plan


def build(cfg: BuildConfig, log: Log = _print) -> dict:
    with LinkData.open_dir(cfg.romfs) as ld:
        prepared = prepare_records(cfg, ld, log)
        for w in prepared.warnings[:200]:
            log(f"warning: {w}")
        if len(prepared.warnings) > 200:
            log(f"warning: ... {len(prepared.warnings) - 200} more")
        if cfg.layout == "patch":
            summary = _build_patch(cfg, ld, prepared, log)
        else:
            summary = _build_archive(cfg, ld, prepared, log)
    if cfg.manifest:
        os.makedirs(os.path.dirname(os.path.abspath(cfg.manifest)), exist_ok=True)
        with open(cfg.manifest, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(summary, fh, indent=1)
            fh.write("\n")
    log(f"  {cfg.idx_path} ({os.path.getsize(cfg.idx_path)} bytes, "
        f"{summary['idx_entries']} entries)")
    log(f"  {cfg.bin_path} ({os.path.getsize(cfg.bin_path)} bytes)")
    return summary


def _summary(cfg: BuildConfig, count: int) -> dict:
    return {
        "tool": f"ldtool {__version__}",
        "layout": cfg.layout,
        "source_romfs": cfg.romfs,
        "po_dir": cfg.po_dir,
        "idx": cfg.idx_path,
        "bin": cfg.bin_path,
        "idx_entries": count,
        "serializer": cfg.serializer,
    }


def _build_patch(cfg: BuildConfig, ld: LinkData, prepared: Prepared, log: Log) -> dict:
    overrides = load_always_on(cfg.always_on_dir)
    plan = _patch_plan(cfg, ld, prepared, overrides, log)
    os.makedirs(cfg.out_dir, exist_ok=True)
    entries = write_linkdata(
        cfg.idx_path, cfg.bin_path, len(ld), {},
        stored={e.index: StoredBlob(e.data, e.size_field, e.flag_field) for e in plan},
        entry_align=cfg.entry_align)
    summary = _summary(cfg, len(ld))
    summary["always_on_dir"] = cfg.always_on_dir
    summary["records"] = [
        {"source": e.record, "index": e.index,
         "kind": "latam" if e.latam else "raw" if e.raw else "always-on",
         "size": len(e.data), "stored_size": entries[e.index].stored_size,
         "changed_cells": e.changed_cells}
        for e in sorted(plan, key=lambda e: (e.record, e.latam))]
    latam = [e for e in plan if e.latam]
    raw = [e for e in plan if e.raw]
    log(f"build: {len(latam)} Latam records ({sum(e.changed_cells for e in latam)} cells "
        f"changed), {len(plan) - len(latam) - len(raw)} always-on records "
        f"({sum(len(v) for v in overrides.values())} overrides), "
        f"{len(raw)} raw records, {len(prepared.warnings)} warnings")
    return summary


def _build_archive(cfg: BuildConfig, ld: LinkData, prepared: Prepared, log: Log) -> dict:
    selected = _selected(cfg, prepared)
    mapping, count = _layout(cfg, selected, len(ld))
    if cfg.fill not in ("empty", "source"):
        raise PipelineError(f"unknown fill mode {cfg.fill!r}")

    def from_source(index: int) -> StoredBlob | None:
        if index >= len(ld):
            return None
        e = ld.entry(index)
        return StoredBlob(ld.stored(index), e.size, e.flag)

    os.makedirs(cfg.out_dir, exist_ok=True)
    entries = write_linkdata(
        cfg.idx_path, cfg.bin_path, count,
        {mapping[r]: res.data for r, res in selected.items()},
        stored_source=from_source if cfg.fill == "source" else None,
        entry_align=cfg.entry_align, block_size=cfg.block_size)

    translated = sum(1 for t in prepared.translations.values() if _active(t, cfg.include_fuzzy))
    applied = sum(res.applied for res in selected.values())
    summary = _summary(cfg, count)
    summary.update({
        "map": cfg.map_spec,
        "fill": cfg.fill,
        "records": [
            {"source": r, "index": mapping[r], "size": len(res.data),
             "stored_size": entries[mapping[r]].stored_size, "changed_cells": res.applied}
            for r, res in sorted(selected.items())],
    })
    changed = sum(1 for res in selected.values() if res.applied)
    log(f"build: {len(selected)} records written ({changed} with changes), "
        f"{applied} cells replaced from {translated} translated entries, "
        f"{len(prepared.warnings)} warnings")
    return summary


def verify(cfg: BuildConfig, log: Log = _print) -> bool:
    problems: list[str] = []
    counts: collections.Counter = collections.Counter()
    with LinkData.open_dir(cfg.romfs) as src:
        prepared = prepare_records(cfg, src, log)
        with LinkData(cfg.idx_path, cfg.bin_path) as built:
            problems += _check_spans(cfg, built)
            if cfg.layout == "patch":
                _verify_patch(cfg, src, built, prepared, problems, counts, log)
            else:
                _verify_archive(cfg, src, built, prepared, problems, counts)
    for p in problems[:50]:
        log(f"FAIL: {p}")
    ok = not problems
    log(f"verify: {'OK' if ok else 'FAILED'} - {counts['records']} records parsed, "
        f"{counts['cells']} cells checked, {counts['translated_cells_ok']} translated cells "
        f"match the .po, {counts['override_cells_ok']} always-on cells match, "
        f"{counts['raw_records_ok']} raw records match, "
        f"{len(prepared.warnings)} .po warnings, {len(problems)} problems")
    return ok


def _check_spans(cfg: BuildConfig, built: LinkData) -> list[str]:
    problems = []
    spans = sorted((e.offset, e.offset + e.stored_size, i)
                   for i, e in enumerate(built.entries) if not e.is_empty)
    for (_a0, a1, ai), (b0, _b1, bi) in zip(spans, spans[1:]):
        if b0 < a1:
            problems.append(f"entries {ai} and {bi} overlap")
    for start, end, i in spans:
        if start % cfg.entry_align:
            problems.append(f"entry {i} offset 0x{start:x} not {cfg.entry_align}-aligned")
        if end > built.bin_size:
            problems.append(f"entry {i} runs past the end of the BIN")
    return problems


def _check_cells(cfg: BuildConfig, rec: int, label: str, data: bytes, original: TextTable,
                 translations: dict[tuple[int, int, int], PoText] | None,
                 overrides: list[Override], problems: list[str],
                 counts: collections.Counter) -> None:
    try:
        table = parse_table(data)
    except (ValueError, TableError) as exc:
        problems.append(f"record {rec} ({label}): {exc}")
        return
    counts["records"] += 1
    if (table.mode, table.reserved, table.schema, table.count) != \
            (original.mode, original.reserved, original.schema, original.count):
        problems.append(f"record {rec} ({label}): header/schema differs from the original")
        return
    o_text = {(o.row, o.col): o.text for o in overrides if o.text is not None}
    o_ints = {o.row: list(o.ints) for o in overrides if o.ints is not None}
    for row in range(table.count):
        if table.ints(row) != o_ints.get(row, original.ints(row)):
            problems.append(f"record {rec} ({label}) row {row}: attribute fields wrong")
        for col in range(table.schema.string_count):
            t = translations.get((rec, row, col)) if translations is not None else None
            if (row, col) in o_text:
                want, kind = o_text[row, col], "override_cells_ok"
            elif t is not None and _active(t, cfg.include_fuzzy):
                want, kind = t.text, "translated_cells_ok"
            else:
                want, kind = original.text(row, col), None
            got = table.text(row, col)
            if got != want:
                problems.append(f"record {rec} ({label}) row {row} col {col}: "
                                f"{got!r} != expected {want!r}")
            elif kind:
                counts[kind] += 1
            counts["cells"] += 1


def _verify_patch(cfg: BuildConfig, src: LinkData, built: LinkData, prepared: Prepared,
                  problems: list[str], counts: collections.Counter, log: Log) -> None:
    overrides = load_always_on(cfg.always_on_dir)
    plan = _patch_plan(cfg, src, prepared, overrides, log)
    if len(built) != len(src):
        problems.append(f"IDX has {len(built)} entries, expected {len(src)}")
        return
    by_index = {p.index: p for p in plan}
    for i, e in enumerate(built.entries):
        p = by_index.get(i)
        if p is None:
            if not e.is_empty:
                problems.append(f"entry {i} should be empty")
            continue
        label = f"{'Latam' if p.latam else 'always-on'} entry {i}"
        if e.is_empty:
            problems.append(f"record {p.record}: {label} is empty")
            continue
        if e.marker != (LATAM_MARKER if p.latam else 0):
            problems.append(f"{label}: size marker 0x{e.marker:x} is wrong")
        if e.target != (p.record if i != p.record else 0):
            problems.append(f"{label}: target record {e.target} is wrong")
        if e.compressed or e.real_size != e.stored_size:
            problems.append(f"{label}: must be stored raw (size {e.real_size}, "
                            f"stored {e.stored_size}, flag 0x{e.flag:x})")
            continue
        data = built.read(i)
        if p.raw:
            if data != p.data:
                problems.append(f"record {p.record} (raw entry {i}): bytes differ")
            counts["raw_records_ok" if data == p.data else "raw_records_bad"] += 1
            continue
        _check_cells(cfg, p.record, label, data, parse_table(src.read(p.record)),
                     prepared.translations if p.latam else None,
                     overrides.get(p.record, []), problems, counts)
        if data != p.data:
            problems.append(f"record {p.record} ({label}): bytes differ from a fresh "
                            "serialization")


def _verify_archive(cfg: BuildConfig, src: LinkData, built: LinkData, prepared: Prepared,
                    problems: list[str], counts: collections.Counter) -> None:
    selected = _selected(cfg, prepared)
    mapping, count = _layout(cfg, selected, len(src))
    if len(built) != count:
        problems.append(f"IDX has {len(built)} entries, expected {count}")
    for rec, res in sorted(selected.items()):
        idx = mapping[rec]
        if idx >= len(built) or built.entry(idx).is_empty:
            problems.append(f"record {rec}: output entry {idx} is empty")
            continue
        try:
            data = built.read(idx)
        except ValueError as exc:
            problems.append(f"record {rec} (entry {idx}): {exc}")
            continue
        _check_cells(cfg, rec, f"entry {idx}", data, res.original, prepared.translations, [],
                     problems, counts)
        if data != res.data:
            problems.append(f"record {rec}: bytes differ from a fresh serialization")
    used = set(mapping.values())
    for i in range(len(built)):
        if i in used:
            continue
        e = built.entry(i)
        if cfg.fill == "empty":
            if not e.is_empty:
                problems.append(f"entry {i} should be empty")
        else:
            if i < len(src):
                se = src.entry(i)
                same = (e.size, e.stored_size, e.flag) == (se.size, se.stored_size, se.flag) \
                    and (se.is_empty or built.stored(i) == src.stored(i))
            else:
                same = e.is_empty
            if not same:
                problems.append(f"entry {i} differs from the source archive")
            counts["copied_entries"] += 1
    unmatched = [t for t in prepared.translations.values()
                 if _active(t, cfg.include_fuzzy) and t.record not in selected]
    if unmatched:
        problems.append(f"{len(unmatched)} translated entries belong to records not in the output")


def roundtrip_records(ld: LinkData, records: Iterable[int] | None = None) -> dict:
    records = list(records) if records is not None else localized_records()
    result = {"checked": 0, "table_ok": 0, "container_ok": 0, "failures": [],
              "by_slot": collections.Counter()}
    for rec in records:
        e = ld.entry(rec)
        if e.is_empty:
            continue
        data = ld.read(rec)
        result["checked"] += 1
        try:
            ok = serialize_table(parse_table(data)) == data
        except (TableError, ValueError) as exc:
            ok = False
            result["failures"].append((rec, str(exc)))
        if ok:
            result["table_ok"] += 1
            result["by_slot"]["species" if rec == SPECIES_RECORD else slot_of(rec)] += 1
        elif not any(f[0] == rec for f in result["failures"]):
            result["failures"].append((rec, "serialize(parse(rec)) != rec"))
        if e.flag:
            block = struct.unpack_from("<I", ld.stored(rec))[0]
            if pack_container(data, block) == ld.stored(rec):
                result["container_ok"] += 1
        else:
            result["container_ok"] += 1
    return result


def analyze_patched_record(original: bytes, patched: bytes) -> dict:
    schema = parse_table(original).schema
    base = parse_table(original, schema)
    mod = parse_table(patched, schema)
    inferred_ok = parse_table(patched).same_content(mod)
    rebuilt = serialize_table_append(original, mod)
    refs = set()
    prefix = bytearray(original)
    for r in range(mod.count):
        for f in schema.string_fields:
            fp = 64 + r * schema.stride + 4 * f
            refs.add(fp + struct.unpack_from("<I", patched, fp)[0])
            prefix[fp:fp + 4] = patched[fp:fp + 4]
    stale = 0
    heap_ok = patched[len(original):].endswith(b"\0") or len(patched) == len(original)
    pos = len(original)
    for s in patched[len(original):].split(b"\0")[:-1]:
        if pos not in refs:
            stale += 1
        pos += len(s) + 1
    canon = serialize_table(mod)
    return {
        "exact": rebuilt == patched,
        "stale_strings": stale,
        "heap_ok": heap_ok,
        "prefix_ok": patched[:len(original)] == bytes(prefix),
        "ints_unchanged": all(mod.ints(r) == base.ints(r) for r in range(mod.count)),
        "changed_cells": sum(1 for r in range(mod.count) for c in range(schema.string_count)
                             if mod.text(r, c) != base.text(r, c)),
        "inferred_schema_ok": inferred_ok,
        "canonical_reparse_ok": parse_table(canon).same_content(mod),
    }


def compare_mod(src: LinkData, mod: LinkData) -> dict:
    changed = [i for i in range(min(len(src), len(mod)))
               if src.entry(i) != mod.entry(i) and src.read(i) != mod.read(i)]
    out = {"changed": len(changed), "localized": 0, "exact": 0, "stale": 0,
           "reordered": 0, "invalid": [], "slots": collections.Counter()}
    for rec in changed:
        if not (is_table_record(rec) or rec == SPECIES_RECORD):
            out["invalid"].append((rec, "not a localized record"))
            continue
        out["localized"] += 1
        out["slots"]["species" if rec == SPECIES_RECORD else slot_of(rec)] += 1
        try:
            a = analyze_patched_record(src.read(rec), mod.read(rec))
        except (TableError, ValueError) as exc:
            out["invalid"].append((rec, str(exc)))
            continue
        if not (a["heap_ok"] and a["prefix_ok"] and a["ints_unchanged"] and a["inferred_schema_ok"]
                and a["canonical_reparse_ok"]):
            out["invalid"].append((rec, a))
        if a["exact"]:
            out["exact"] += 1
        elif a["stale_strings"]:
            out["stale"] += 1
        else:
            out["reordered"] += 1
    return out
