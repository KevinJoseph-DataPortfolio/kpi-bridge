"""Delimited CSV / TSV / TXT: encoding, BOM, separator, bounded streaming read."""

from __future__ import annotations

import codecs
import csv
import io
from pathlib import Path

from charset_normalizer import from_bytes

from ..errors import LimitExceeded, UserError
from .base import IngestContext, RawTable, clean_text
from .tables import structure_grid

DELIMS = [",", ";", "\t", "|"]


def detect_encoding(data: bytes) -> tuple[str, bool]:
    """Retourne (encodage, certain)."""
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", True
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16", True
    try:
        data.decode("utf-8")
        return "utf-8", True
    except UnicodeDecodeError:
        pass
    best = from_bytes(data[:200_000]).best()
    if best is None:
        raise UserError("encoding_unknown")
    return best.encoding, False


def sniff_delimiter(sample: str, forced: str | None = None) -> tuple[str, bool]:
    if forced:
        return forced, True
    lines = [ln for ln in sample.splitlines()[:60] if ln.strip()]
    if not lines:
        return ",", False
    scores: dict[str, tuple[float, int]] = {}
    for d in DELIMS:
        try:
            counts = [len(r) for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=d)]
        except csv.Error:
            continue
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode < 2:
            continue
        consistency = counts.count(mode) / len(counts)
        scores[d] = (consistency, mode)
    if not scores:
        return ",", False
    ranked = sorted(scores.items(), key=lambda kv: (kv[1][0], kv[1][1]), reverse=True)
    best = ranked[0]
    certain = len(ranked) == 1 or ranked[1][1][0] < best[1][0] - 0.2
    return best[0], certain


def looks_delimited(text: str) -> bool:
    delim, _ = sniff_delimiter(text[:20000])
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    if len(lines) < 3:
        return False
    counts = [len(r) for r in csv.reader(io.StringIO("\n".join(lines)), delimiter=delim)]
    mode = max(set(counts), key=counts.count)
    return mode >= 2 and counts.count(mode) / len(counts) >= 0.7


def parse_delimited_text(text: str, name: str, ctx: IngestContext, delimiter: str | None, encoding_note: dict | None) -> list[RawTable]:
    limits = ctx.limits
    delim, certain = sniff_delimiter(text[:50000], delimiter)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    rows: list[list[str | None]] = []
    srcs: list[str] = []
    max_raw_rows = limits.max_rows + 1000  # margin for titles/headers/totals
    cells = 0
    try:
        for rec_no, rec in enumerate(reader, start=1):
            if len(rows) >= max_raw_rows:
                raise LimitExceeded("too_many_rows", limit=limits.max_rows)
            if len(rec) > limits.max_cols:
                raise LimitExceeded("too_many_columns", limit=limits.max_cols, count=len(rec))
            cells += len(rec)
            if cells > limits.max_cells:
                raise LimitExceeded("too_many_cells", limit=limits.max_cells)
            rows.append([clean_text(c) or None for c in rec])
            srcs.append(f"rec={rec_no};line={reader.line_num}")
            if rec_no % 5000 == 0:
                ctx.progress("extract", min(0.9, rec_no / max_raw_rows))
    except csv.Error as exc:
        raise UserError("csv_malformed", line=reader.line_num) from exc
    if not rows:
        raise UserError("no_data_found")
    tables = structure_grid(rows, srcs, name, "csv")
    for t in tables:
        t.note("delimiter", value={"\t": "TAB"}.get(delim, delim), certain=certain)
        if encoding_note:
            t.notes.append(encoding_note)
    data_rows = sum(len(t.rows) - len(t.excluded) for t in tables)
    if data_rows > limits.max_rows:
        raise LimitExceeded("too_many_rows", limit=limits.max_rows)
    return tables


def read_text(path: Path, ctx: IngestContext) -> tuple[str, dict]:
    data = path.read_bytes()
    forced = ctx.options.get("encoding")
    if forced:
        try:
            return data.decode(forced), {"code": "encoding", "params": {"value": forced, "certain": True}}
        except (UnicodeDecodeError, LookupError) as exc:
            raise UserError("encoding_failed", encoding=forced) from exc
    enc, certain = detect_encoding(data)
    try:
        text = data.decode(enc)
    except UnicodeDecodeError as exc:
        raise UserError("encoding_unknown") from exc
    return text, {"code": "encoding", "params": {"value": enc, "certain": certain}}


def parse_csv(path: Path, name: str, ctx: IngestContext, tsv: bool = False) -> list[RawTable]:
    text, enc_note = read_text(path, ctx)
    delim = "\t" if tsv else ctx.options.get("delimiter")
    return parse_delimited_text(text, name, ctx, delim, enc_note)
