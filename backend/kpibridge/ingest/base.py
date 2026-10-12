"""Structures shared by all parsers.

Raw cells are strings. Values natively typed by the format (Excel number, Excel date, JSON boolean…) are
encoded with a control prefix `\\x1f` that cannot appear in user text (control characters are stripped
from texts):

- ``\\x1fN<decimal>`` native number (canonical representation, decimal point)
- ``\\x1fP<decimal>`` native percentage-formatted number (fraction: 0.125 = 12.5 %)
- ``\\x1fD<iso>`` native ISO 8601 date
- ``\\x1fB<true|false>`` native boolean
- ``\\x1fF`` formula with no cached value (value unavailable)
- ``\\x1fC`` + one of the codes above: value taken from a formula's cache
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

MARK = "\x1f"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ProgressFn = Callable[[str, float], None]


def clean_text(value: str) -> str:
    return _CTRL_RE.sub("", value).strip()


def native_number(value: int | float | Decimal, percent: bool = False) -> str:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return ""
        text = repr(value)
        dec = Decimal(text)
    else:
        dec = Decimal(value)
    canonical = format(dec.normalize(), "f") if dec == dec.to_integral_value() else format(dec, "f")
    return f"{MARK}{'P' if percent else 'N'}{canonical}"


def native_date(iso: str) -> str:
    return f"{MARK}D{iso}"


def native_bool(value: bool) -> str:
    return f"{MARK}B{'true' if value else 'false'}"


FORMULA_NO_CACHE = f"{MARK}F"


def cached(encoded: str) -> str:
    return f"{MARK}C{encoded}"


def decode(cell: str | None) -> tuple[str, str | None, bool]:
    """Retourne (kind, valeur, depuis_cache). kind ∈ {'empty','text','N','P','D','B','F'}."""
    if cell is None or cell == "":
        return "empty", None, False
    if not cell.startswith(MARK):
        return "text", cell, False
    from_cache = False
    body = cell[1:]
    if body.startswith("C"):
        from_cache = True
        body = body[1:]
        if body.startswith(MARK):
            body = body[1:]
    if not body:
        return "empty", None, from_cache
    code, rest = body[0], body[1:]
    if code == "F":
        return "F", None, True
    if code in ("N", "P", "D", "B"):
        return code, rest, from_cache
    return "text", rest, from_cache


def display(cell: str | None) -> str:
    kind, value, _ = decode(cell)
    if kind == "F":
        return "⟨formule sans cache⟩"
    if kind == "P" and value is not None:
        return f"{Decimal(value) * 100}%"
    return value or ""


@dataclass
class RawTable:
    """Extracted, untyped table with per-row provenance."""

    name: str
    kind: str  # csv | sheet | pdf_text | ocr | docx | text | json | join
    rows: list[list[str | None]]
    row_src: list[str]
    header_row: int | None = 0
    header_depth: int = 1
    header_confident: bool = True
    excluded: dict[int, str] = field(default_factory=dict)
    cell_meta: dict[str, dict[str, Any]] = field(default_factory=dict)  # "r,c" -> {conf, bbox, page}
    col_refs: list[str] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    images: dict[str, str] = field(default_factory=dict)  # page -> rasterised image file name (OCR)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def note(self, code: str, **params: Any) -> None:
        self.notes.append({"code": code, "params": params})

    def meta_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "header_row": self.header_row,
            "header_depth": self.header_depth,
            "header_confident": self.header_confident,
            "excluded": {str(k): v for k, v in self.excluded.items()},
            "cell_meta": self.cell_meta,
            "col_refs": self.col_refs,
            "notes": self.notes,
            "images": self.images,
        }


@dataclass
class IngestContext:
    """Import context: limits, user options and the progress/cancellation callback."""

    limits: Any
    options: dict[str, Any]
    work_dir: Any
    progress: ProgressFn
    tesseract_cmd: str | None = None
    ui_lang: str = "fr"
