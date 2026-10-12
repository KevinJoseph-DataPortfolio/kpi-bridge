"""Ingestion: format detection, then a dedicated parser. Runs in the worker, never in the API."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .base import IngestContext, RawTable
from .csvtab import parse_csv
from .detect import sniff_format
from .docs import parse_docx, parse_json, parse_txt
from .excel import parse_xls, parse_xlsx
from .pdf import parse_image, parse_pdf


def ingest_file(path: Path, ext: str, display_name: str, ctx: IngestContext) -> list[RawTable]:
    fmt = sniff_format(path, ext, ctx.limits)
    ctx.progress("extract", 0.05)
    if fmt == "csv":
        tables = parse_csv(path, display_name, ctx)
    elif fmt == "tsv":
        tables = parse_csv(path, display_name, ctx, tsv=True)
    elif fmt == "txt":
        tables = parse_txt(path, display_name, ctx)
    elif fmt == "xlsx":
        tables = parse_xlsx(path, display_name, ctx)
    elif fmt == "xls":
        tables = parse_xls(path, display_name, ctx)
    elif fmt == "pdf":
        tables = parse_pdf(path, display_name, ctx)
    elif fmt == "docx":
        tables = parse_docx(path, display_name, ctx)
    elif fmt == "json":
        tables = parse_json(path, display_name, ctx)
    elif fmt == "image":
        tables = parse_image(path, display_name, ctx)
    else:  # pragma: no cover - liste blanche
        raise ValueError(fmt)
    for t in tables:
        t.note("format", value=fmt)
    return tables


def save_raw(table: RawTable, path: Path) -> None:
    width = table.n_cols
    cols = {f"c{j}": [(r[j] if j < len(r) else None) for r in table.rows] for j in range(width)}
    cols["_src"] = list(table.row_src)
    arrays = {k: pa.array(v, type=pa.string()) for k, v in cols.items()}
    pq.write_table(pa.table(arrays), path, compression="zstd")


def load_raw_rows(path: Path, offset: int = 0, limit: int | None = None) -> tuple[list[list[str | None]], list[str]]:
    tbl = pq.read_table(path)
    if offset or limit is not None:
        tbl = tbl.slice(offset, limit if limit is not None else tbl.num_rows - offset)
    names = [n for n in tbl.column_names if n != "_src"]
    columns = [tbl.column(n).to_pylist() for n in names]
    rows = [list(r) for r in zip(*columns, strict=False)] if columns else [[] for _ in range(tbl.num_rows)]
    srcs = tbl.column("_src").to_pylist()
    return rows, srcs


__all__ = ["IngestContext", "RawTable", "ingest_file", "load_raw_rows", "save_raw"]
