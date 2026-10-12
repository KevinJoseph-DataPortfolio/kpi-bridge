"""PDF : distinction texte / scanné, extraction des tableaux, en-têtes répétés, provenance page+zone.

Le PDF n'est jamais rendu activement : pas de JavaScript, pas d'actions, pas de liens suivis. Les aperçus sont des
images rasterisées par PDFium.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError

from ..errors import LimitExceeded, UserError
from .base import IngestContext, RawTable, clean_text
from .ocr import ocr_langs, prepare_image, run_ocr, words_to_grid
from .tables import mark_rows, structure_grid
from .text import extract_text_values

MIN_CHARS_TEXT_PAGE = 25
RENDER_DPI = 300


def parse_page_selection(spec: str | None, n_pages: int) -> list[int]:
    if not spec:
        return list(range(1, n_pages + 1))
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            pages.update(range(max(1, lo), min(n_pages, hi) + 1))
        else:
            p = int(part)
            if 1 <= p <= n_pages:
                pages.add(p)
    if not pages:
        raise UserError("invalid_page_selection")
    return sorted(pages)


def _header_key(row: list[Any]) -> tuple:
    return tuple((clean_text(str(c)) if c else "") for c in row)


def parse_pdf(path: Path, name: str, ctx: IngestContext) -> list[RawTable]:
    limits = ctx.limits
    try:
        pdf = pdfplumber.open(path)
    except PDFPasswordIncorrect as exc:
        raise UserError("protected_file") from exc
    except (PDFSyntaxError, Exception) as exc:  # noqa: BLE001
        raise UserError("corrupted_file") from exc
    try:
        n_pages = len(pdf.pages)
        try:
            pages = parse_page_selection(ctx.options.get("pages"), n_pages)
        except ValueError as exc:
            raise UserError("invalid_page_selection") from exc
        if len(pages) > limits.max_pdf_pages:
            raise LimitExceeded("too_many_pages", count=n_pages, limit=limits.max_pdf_pages, needs_selection=True)
        text_pages: list[int] = []
        scanned_pages: list[int] = []
        for p in pages:
            page = pdf.pages[p - 1]
            if len(page.chars) >= MIN_CHARS_TEXT_PAGE:
                text_pages.append(p)
            else:
                scanned_pages.append(p)
        if len(scanned_pages) > limits.max_ocr_pages:
            raise LimitExceeded("too_many_ocr_pages", count=len(scanned_pages), limit=limits.max_ocr_pages, needs_selection=True)
        tables: list[RawTable] = []
        # --- pages texte : tableaux avec fusion inter-pages
        current: RawTable | None = None
        current_header: tuple | None = None
        text_lines_total: list[tuple[str, str]] = []
        for i, p in enumerate(text_pages):
            page = pdf.pages[p - 1]
            found = page.find_tables()
            if not found:
                found = page.find_tables(table_settings={"vertical_strategy": "text", "horizontal_strategy": "text", "min_words_vertical": 2})
            if not found:
                for ln_no, line in enumerate((page.extract_text() or "").splitlines(), start=1):
                    text_lines_total.append((line, f"page={p};line={ln_no}"))
            for tbl in found:
                rows = tbl.extract()
                if not rows or max(len(r) for r in rows) < 2:
                    continue
                if len(rows[0]) > limits.max_cols:
                    raise LimitExceeded("too_many_columns", limit=limits.max_cols, count=len(rows[0]))
                clean_rows = [[(clean_text(c) if c else None) or None for c in r] for r in rows]
                srcs = []
                for row_obj in tbl.rows:
                    b = row_obj.bbox
                    srcs.append(f"page={p};bbox={b[0]:.0f},{b[1]:.0f},{b[2]:.0f},{b[3]:.0f}")
                while len(srcs) < len(clean_rows):
                    srcs.append(f"page={p}")
                key = _header_key(clean_rows[0])
                continues = (
                    current is not None
                    and current.n_cols == len(clean_rows[0])
                    and (key == current_header or not any(c and not c[0].isdigit() for c in key if c))
                )
                if continues and current is not None:
                    offset = len(current.rows)
                    current.rows.extend(clean_rows)
                    current.row_src.extend(srcs[: len(clean_rows)])
                    if key == current_header:
                        current.excluded[offset] = "repeated_header"
                    current.note("pages_merged", page=p)
                else:
                    current = RawTable(
                        name=f"{name} · p.{p}",
                        kind="pdf_text",
                        rows=clean_rows,
                        row_src=srcs[: len(clean_rows)],
                        header_row=0,
                        col_refs=[str(j + 1) for j in range(len(clean_rows[0]))],
                    )
                    current_header = key
                    tables.append(current)
            ctx.progress("extract", 0.1 + 0.4 * (i + 1) / max(1, len(text_pages)))
        for t in tables:
            mark_rows(t)
        # --- pages scannées : OCR
        if scanned_pages:
            langs = ocr_langs(ctx.ui_lang, ctx.options.get("ocr_lang"))
            doc = pdfium.PdfDocument(str(path))
            try:
                for i, p in enumerate(scanned_pages):
                    page = doc[p - 1]
                    w, h = page.get_size()
                    scale = RENDER_DPI / 72
                    if w * h * scale * scale > limits.max_image_pixels:
                        scale = (limits.max_image_pixels / (w * h)) ** 0.5
                    bitmap = page.render(scale=scale, may_draw_forms=False)
                    img = bitmap.to_pil()
                    img_name = f"ocr_p{p}.png"
                    img_path = Path(ctx.work_dir) / img_name
                    img.save(img_path)
                    gray = prepare_image(img_path, limits.max_image_pixels)
                    words = run_ocr(gray, ctx.tesseract_cmd, langs)
                    grid, srcs, meta, low = words_to_grid(words, p)
                    # Les coordonnées OCR sont celles de l'image préparée (même taille si pas d'agrandissement)
                    for t in structure_grid(grid, srcs, f"{name} · OCR p.{p}", "ocr", cell_meta=meta):
                        t.images[str(p)] = img_name
                        t.note("ocr_page", page=p, langs=langs)
                        if low:
                            t.note("ocr_low_confidence_cells", count=low)
                        tables.append(t)
                    ctx.progress("ocr", 0.5 + 0.45 * (i + 1) / len(scanned_pages))
            finally:
                doc.close()
        if not tables and text_lines_total:
            text_table = extract_text_values(text_lines_total, name)
            if text_table is not None:
                tables.append(text_table)
        if not tables:
            raise UserError("no_table_found_pdf")
        return tables
    finally:
        pdf.close()


def parse_image(path: Path, name: str, ctx: IngestContext) -> list[RawTable]:
    img = prepare_image(path, ctx.limits.max_image_pixels)
    ctx.progress("ocr", 0.3)
    langs = ocr_langs(ctx.ui_lang, ctx.options.get("ocr_lang"))
    words = run_ocr(img, ctx.tesseract_cmd, langs)
    img_name = "ocr_p1.png"
    img.save(Path(ctx.work_dir) / img_name)
    grid, srcs, meta, low = words_to_grid(words, 1)
    if not grid:
        raise UserError("image_unreadable")
    tables = structure_grid(grid, srcs, f"{name} · OCR", "ocr", cell_meta=meta)
    numeric_tables = [t for t in tables if t.n_cols >= 2]
    if not numeric_tables:
        raise UserError("image_no_table")
    for t in numeric_tables:
        t.images["1"] = img_name
        t.note("ocr_page", page=1, langs=langs)
        if low:
            t.note("ocr_low_confidence_cells", count=low)
    return numeric_tables
