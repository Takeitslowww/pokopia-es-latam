from __future__ import annotations

import json
from typing import Iterable


class MappingError(ValueError):
    pass


def resolve_mapping(spec: str, sources: Iterable[int]) -> dict[int, int]:
    sources = sorted(set(sources))
    kind, _, arg = spec.partition(":")
    kind = kind.strip().lower()
    if kind == "identity":
        mapping = {s: s for s in sources}
    elif kind == "sequential":
        start = _int(arg, spec)
        mapping = {s: start + i for i, s in enumerate(sources)}
    elif kind == "offset":
        delta = _int(arg, spec)
        mapping = {s: s + delta for s in sources}
    elif kind == "file":
        if not arg:
            raise MappingError("file: mapping needs a path")
        with open(arg, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise MappingError(f"{arg}: expected a JSON object")
        table = {int(k): int(v) for k, v in raw.items()}
        missing = [s for s in sources if s not in table]
        if missing:
            raise MappingError(f"{arg}: no output index for records {missing[:10]}")
        mapping = {s: table[s] for s in sources}
    else:
        raise MappingError(f"unknown mapping spec {spec!r}")
    bad = {s: d for s, d in mapping.items() if d < 0}
    if bad:
        raise MappingError(f"negative output indices: {bad}")
    seen: dict[int, int] = {}
    for s, d in mapping.items():
        if d in seen:
            raise MappingError(f"records {seen[d]} and {s} both map to index {d}")
        seen[d] = s
    return mapping


def resolve_entry_count(spec: str | int, mapping: dict[int, int], source_count: int) -> int:
    needed = max(mapping.values(), default=-1) + 1
    if isinstance(spec, int) or str(spec).isdigit():
        count = int(spec)
    elif spec == "full":
        count = source_count
    elif spec == "compact":
        count = needed
    else:
        raise MappingError(f"unknown IDX size spec {spec!r}")
    if count < needed:
        raise MappingError(f"IDX with {count} entries cannot hold index {needed - 1}")
    return count


def _int(text: str, spec: str) -> int:
    try:
        return int(text, 0)
    except ValueError:
        raise MappingError(f"bad number in mapping spec {spec!r}") from None
