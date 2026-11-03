from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Union

HEADER_SIZE = 64
DEFAULT_PAD = 8
_MAX_STRIDE = 256

Value = Union[str, int]


class TableError(ValueError):
    pass


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass(frozen=True)
class TableSchema:
    stride: int
    kinds: str

    def __post_init__(self) -> None:
        if self.stride <= 0 or self.stride != 4 * len(self.kinds):
            raise TableError(f"stride {self.stride} does not match kinds {self.kinds!r}")
        if set(self.kinds) - {"s", "i"}:
            raise TableError(f"bad field kinds {self.kinds!r}")

    @property
    def string_fields(self) -> tuple[int, ...]:
        return tuple(i for i, k in enumerate(self.kinds) if k == "s")

    @property
    def string_count(self) -> int:
        return self.kinds.count("s")

    def __str__(self) -> str:
        return f"{self.stride}:{self.kinds}"


@dataclass
class TextTable:
    mode: int
    reserved: bytes
    schema: TableSchema
    rows: list[list[Value]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)

    def _field(self, col: int) -> int:
        fields = self.schema.string_fields
        if not 0 <= col < len(fields):
            raise IndexError(f"string column {col} out of range (table has {len(fields)})")
        return fields[col]

    def text(self, row: int, col: int = 0) -> str:
        return self.rows[row][self._field(col)]

    def set_text(self, row: int, col: int, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("text must be str")
        if "\0" in value:
            raise TableError("text may not contain NUL")
        self.rows[row][self._field(col)] = value

    def texts(self, row: int) -> list[str]:
        return [self.rows[row][f] for f in self.schema.string_fields]

    def ints(self, row: int) -> list[int]:
        return [v for v, k in zip(self.rows[row], self.schema.kinds) if k == "i"]

    def set_ints(self, row: int, values: list[int]) -> None:
        fields = [i for i, k in enumerate(self.schema.kinds) if k == "i"]
        if len(values) != len(fields):
            raise TableError(f"row {row}: {len(values)} integers given, table has {len(fields)}")
        for f, v in zip(fields, values):
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 0xFFFFFFFF:
                raise TableError(f"row {row}: expected u32, got {v!r}")
            self.rows[row][f] = v

    def copy(self) -> "TextTable":
        return TextTable(self.mode, self.reserved, self.schema, [list(r) for r in self.rows])

    def same_content(self, other: "TextTable") -> bool:
        return (self.mode, self.reserved, self.schema, self.rows) == \
               (other.mode, other.reserved, other.schema, other.rows)


def _is_string_start(data: bytes, target: int, pool_start: int) -> bool:
    if not pool_start <= target < len(data):
        return False
    if target != pool_start and data[target - 1] != 0:
        return False
    return data.find(b"\0", target) >= 0


def _classify(data: bytes, count: int, stride: int) -> str | None:
    pool_start = HEADER_SIZE + count * stride
    if pool_start > len(data):
        return None
    kinds = []
    for f in range(stride // 4):
        is_str = True
        for r in range(count):
            fp = HEADER_SIZE + r * stride + 4 * f
            (value,) = struct.unpack_from("<I", data, fp)
            if not _is_string_start(data, fp + value, pool_start):
                is_str = False
                break
        kinds.append("s" if is_str else "i")
    return "".join(kinds)


def infer_schema(data: bytes) -> TableSchema:
    if len(data) < HEADER_SIZE + 4:
        raise TableError("record too short for a text table")
    count = struct.unpack_from("<I", data, 0)[0]
    if count == 0:
        raise TableError("cannot infer the schema of an empty table")
    first = struct.unpack_from("<I", data, HEADER_SIZE)[0]
    candidates = []
    if first % count == 0 and (first // count) % 4 == 0 and 4 <= first // count <= _MAX_STRIDE:
        candidates.append(first // count)
    max_stride = min(_MAX_STRIDE, (len(data) - HEADER_SIZE) // count)
    candidates += [s for s in range(4, max_stride + 1, 4) if s not in candidates]
    for stride in candidates:
        kinds = _classify(data, count, stride)
        if kinds and kinds[0] == "s":
            return TableSchema(stride, kinds)
    raise TableError("no consistent row layout found")


def parse_table(data: bytes, schema: TableSchema | None = None) -> TextTable:
    if len(data) < HEADER_SIZE:
        raise TableError(f"record of {len(data)} bytes is shorter than the 64-byte header")
    count, mode = struct.unpack_from("<II", data, 0)
    reserved = bytes(data[8:HEADER_SIZE])
    if count == 0:
        return TextTable(mode, reserved, schema or TableSchema(4, "s"), [])
    if schema is None:
        schema = infer_schema(data)
    pool_start = HEADER_SIZE + count * schema.stride
    if pool_start > len(data):
        raise TableError(f"{count} rows of {schema.stride} bytes do not fit in {len(data)} bytes")
    nfields = len(schema.kinds)
    rows: list[list[Value]] = []
    for r in range(count):
        base = HEADER_SIZE + r * schema.stride
        values = struct.unpack_from(f"<{nfields}I", data, base)
        row: list[Value] = []
        for f, (kind, value) in enumerate(zip(schema.kinds, values)):
            if kind == "i":
                row.append(value)
                continue
            target = base + 4 * f + value
            if not pool_start <= target < len(data):
                raise TableError(f"row {r} field {f}: string offset 0x{target:x} outside the pool")
            end = data.find(b"\0", target)
            if end < 0:
                raise TableError(f"row {r} field {f}: unterminated string")
            try:
                row.append(data[target:end].decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise TableError(f"row {r} field {f}: invalid UTF-8 ({exc})") from None
        rows.append(row)
    return TextTable(mode, reserved, schema, rows)


def _encode(value: Value, r: int, f: int) -> bytes:
    if not isinstance(value, str):
        raise TableError(f"row {r} field {f}: expected str, got {type(value).__name__}")
    raw = value.encode("utf-8")
    if b"\0" in raw:
        raise TableError(f"row {r} field {f}: text contains NUL")
    return raw


def _check_int(value: Value, r: int, f: int) -> int:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise TableError(f"row {r} field {f}: expected u32, got {value!r}")
    return value


def _header(table: TextTable) -> bytearray:
    if len(table.reserved) != HEADER_SIZE - 8:
        raise TableError("reserved header area must be 56 bytes")
    return bytearray(struct.pack("<II", table.count, table.mode) + table.reserved)


def serialize_table(table: TextTable, pad: int = DEFAULT_PAD) -> bytes:
    schema = table.schema
    out = _header(table)
    pool_start = HEADER_SIZE + table.count * schema.stride
    out += bytes(table.count * schema.stride)
    pool = bytearray()
    for r, row in enumerate(table.rows):
        if len(row) != len(schema.kinds):
            raise TableError(f"row {r} has {len(row)} fields, schema has {len(schema.kinds)}")
        base = HEADER_SIZE + r * schema.stride
        for f, (kind, value) in enumerate(zip(schema.kinds, row)):
            fp = base + 4 * f
            if kind == "s":
                struct.pack_into("<I", out, fp, pool_start + len(pool) - fp)
                pool += _encode(value, r, f) + b"\0"
            else:
                struct.pack_into("<I", out, fp, _check_int(value, r, f))
    out += pool
    if pad > 1:
        out += bytes(_align(len(out), pad) - len(out))
    return bytes(out)


def serialize_table_append(original: bytes, table: TextTable, *, dedup: bool = True,
                           pad: int = 1) -> bytes:
    base_table = parse_table(original, table.schema)
    if base_table.count != table.count:
        raise TableError("append mode cannot change the number of rows")
    out = bytearray(original)
    if table.mode != base_table.mode:
        struct.pack_into("<I", out, 4, table.mode)
    if table.reserved != base_table.reserved:
        if len(table.reserved) != HEADER_SIZE - 8:
            raise TableError("reserved header area must be 56 bytes")
        out[8:HEADER_SIZE] = table.reserved
    appended: dict[bytes, int] = {}
    for r, (row, old_row) in enumerate(zip(table.rows, base_table.rows)):
        base = HEADER_SIZE + r * table.schema.stride
        for f, (kind, value, old) in enumerate(zip(table.schema.kinds, row, old_row)):
            if value == old:
                continue
            fp = base + 4 * f
            if kind == "i":
                struct.pack_into("<I", out, fp, _check_int(value, r, f))
                continue
            raw = _encode(value, r, f)
            target = appended.get(raw) if dedup else None
            if target is None:
                target = len(out)
                out += raw + b"\0"
                appended[raw] = target
            struct.pack_into("<I", out, fp, target - fp)
    if pad > 1:
        out += bytes(_align(len(out), pad) - len(out))
    return bytes(out)
