from __future__ import annotations

import os
from dataclasses import dataclass

MOD_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROMFS = os.environ.get("LDTOOL_ROMFS", os.path.join(MOD_ROOT, "boot", "romfs"))
SOURCE_IDX = "LI_C0_R.IDX"
SOURCE_BIN = "LD_C0_R.BIN"

FIRST_TABLE_RECORD = 1909
LAST_TABLE_RECORD = 5220
SLOT_COUNT = 9
GROUP_COUNT = (LAST_TABLE_RECORD - FIRST_TABLE_RECORD + 1) // SLOT_COUNT

SLOT_LANGUAGES = ("ja", "en", "fr", "it", "de", "es", "zh-Hant", "ko", "zh-Hans")
ENGLISH_SLOT = SLOT_LANGUAGES.index("en")
SPANISH_SLOT = SLOT_LANGUAGES.index("es")

SPECIES_RECORD = 1474
SPECIES_COLUMNS = ("ja", "en", "fr", "it", "de", "es", "zh-Hans", "zh-Hant", "ko")
SPECIES_ENGLISH_COLUMN = SPECIES_COLUMNS.index("en")
SPECIES_SPANISH_COLUMN = SPECIES_COLUMNS.index("es")

LANGUAGE_NAMES_GROUP_BASE = 4663

RECORD_COUNT = 12298
SPARE_FIRST_INDEX = 8953


def is_table_record(record: int) -> bool:
    return FIRST_TABLE_RECORD <= record <= LAST_TABLE_RECORD


def slot_of(record: int) -> int:
    if not is_table_record(record):
        raise ValueError(f"record {record} is not in the localized table range")
    return (record - FIRST_TABLE_RECORD) % SLOT_COUNT


def group_base(record: int) -> int:
    return record - slot_of(record)


def table_records(slot: int) -> list[int]:
    return [FIRST_TABLE_RECORD + SLOT_COUNT * g + slot for g in range(GROUP_COUNT)]


def localized_records() -> list[int]:
    return [SPECIES_RECORD] + list(range(FIRST_TABLE_RECORD, LAST_TABLE_RECORD + 1))


@dataclass(frozen=True)
class TextUnit:
    record: int
    english_record: int
    columns: tuple[int, ...] | None
    english_columns: dict[int, int] | None = None

    def english_column(self, col: int) -> int:
        if self.english_columns is None:
            return col
        return self.english_columns[col]


def spanish_units(include_species: bool = True) -> list[TextUnit]:
    units = []
    if include_species:
        units.append(TextUnit(SPECIES_RECORD, SPECIES_RECORD, (SPECIES_SPANISH_COLUMN,),
                              {SPECIES_SPANISH_COLUMN: SPECIES_ENGLISH_COLUMN}))
    for rec in table_records(SPANISH_SLOT):
        units.append(TextUnit(rec, rec - SPANISH_SLOT + ENGLISH_SLOT, None))
    return units
