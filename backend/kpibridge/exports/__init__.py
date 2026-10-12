"""Exports: PDF (snapshot), PNG (page 1 of the PDF rendered), CSV (filtered scope, text cells neutralised).

Embedded Noto fonts (OFL): Latin/Cyrillic (Noto Sans) and Simplified Chinese (Noto Sans SC). No CDN.
The PDF and PNG are snapshots: they state the period, the filters and the data version explicitly.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from ..i18n import translate

FONT_DIR = Path(__file__).resolve().parent / "fonts"
_FONTS_READY = False
CJK_RE = re.compile(r"[　-鿿＀-￯]")

SEP = {
    "fr": (" ", ","),
    "en": (",", "."),
    "ru": (" ", ","),
    "zh-Hans": (",", "."),
    "de": (".", ","),
    "es": (".", ","),
}
SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£", "CNY": "¥", "RUB": "₽", "CHF": "CHF", "JPY": "¥"}
PREFIX_CURRENCY = {"en", "zh-Hans"}
DEFAULT_DECIMALS = {"currency": 2, "percent": 1, "count": 0, "ratio": 2, "number": 2}


def format_number(value: str | None, lang: str, unit: str = "number", currency: str | None = None, decimals: int | None = None) -> str:
    if value is None:
        return translate(lang, "dashboard.unavailable")
    d = Decimal(value)
    places = DEFAULT_DECIMALS.get(unit, 2) if decimals is None else decimals
    if unit == "number" and decimals is None and d == d.to_integral_value():
        places = 0
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN) if places >= 0 else d
    neg = q < 0
    s = format(abs(q), "f")
    int_part, _, frac = s.partition(".")
    group, dec = SEP.get(lang, SEP["en"])
    groups: list[str] = []
    while len(int_part) > 3:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    groups.insert(0, int_part)
    out = group.join(groups) + (dec + frac if frac else "")
    if neg:
        out = "-" + out
    if unit == "percent":
        return out + (" %" if lang in ("fr", "de", "es", "ru") else "%")
    if unit == "currency" and currency:
        sym = SYMBOL.get(currency, currency)
        return f"{sym}{out}" if lang in PREFIX_CURRENCY else f"{out} {sym}"
    return out


def _register_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("KPI-Sans", str(FONT_DIR / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("KPI-SC", str(FONT_DIR / "NotoSansSC-Regular.ttf")))
    _FONTS_READY = True


def _font(text: str) -> str:
    return "KPI-SC" if CJK_RE.search(text or "") else "KPI-Sans"


MISSING_GLYPH = "□"


def _runs(text: str) -> list[tuple[str, str]]:
    """Splits a text into (font, text) segments according to the real coverage of the embedded fonts.
    A character missing from both fonts is visibly replaced by '□' (never silently dropped)."""
    from reportlab.pdfbase import pdfmetrics

    _register_fonts()
    sans = pdfmetrics.getFont("KPI-Sans").face.charToGlyph
    sc = pdfmetrics.getFont("KPI-SC").face.charToGlyph
    runs: list[tuple[str, str]] = []
    for ch in text or "":
        o = ord(ch)
        if o in sans:
            font = "KPI-Sans"
        elif o in sc:
            font = "KPI-SC"
        elif ord(MISSING_GLYPH) in sc:
            font, ch = "KPI-SC", MISSING_GLYPH
        else:
            font, ch = "KPI-Sans", "?"
        if runs and runs[-1][0] == font:
            runs[-1] = (font, runs[-1][1] + ch)
        else:
            runs.append((font, ch))
    return runs


def _width(text: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return sum(stringWidth(t, f, size) for f, t in _runs(text))


PALETTE = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#4d7c0f"]


def _filters_text(payload: dict[str, Any], lang: str) -> list[str]:
    af = payload.get("active_filters") or {}
    lines = []
    pf, pt = af.get("period_from"), af.get("period_to")
    cov = payload.get("coverage")
    if pf or pt:
        lines.append(translate(lang, "export.period", {"from": pf or "…", "to": pt or "…"}))
    elif cov:
        lines.append(translate(lang, "export.period_all", {"from": cov[0], "to": cov[1]}))
    labels = {d["field"]: d["label"] for d in (payload.get("filters") or {}).get("dims", [])}
    for field, values in (af.get("dims") or {}).items():
        lines.append(f"{labels.get(field, field)} : {', '.join(values[:12])}{' …' if len(values) > 12 else ''}")
    for gf in payload.get("global_filters") or []:
        lines.append(translate(lang, "export.global_filter", {"field": gf["field"], "op": gf["op"], "values": ", ".join(gf.get("values", [])[:6])}))
    if not lines:
        lines.append(translate(lang, "export.no_filter"))
    return lines


def render_pdf(payload: dict[str, Any], lang: str, project_name: str) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas

    _register_fonts()
    buf = io.BytesIO()
    w, h = landscape(A4)
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"KPI Bridge — {project_name}")
    c.setAuthor("KPI Bridge")
    margin = 32

    def text(x: float, y: float, s: str, size: float = 9, color: str = "#111827", right: bool = False) -> None:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        c.setFillColor(color)
        if right:
            x -= _width(s, size)
        for font, part in _runs(s):
            c.setFont(font, size)
            c.drawString(x, y, part)
            x += stringWidth(part, font, size)

    def fit(s: str, size: float, width: float) -> str:
        if _width(s, size) <= width:
            return s
        while s and _width(s + "…", size) > width:
            s = s[:-1]
        return s + "…"

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    page_no = [1]

    def header() -> float:
        text(margin, h - margin - 6, fit(project_name, 15, w - 2 * margin - 200), 15)
        text(w - margin, h - margin - 6, translate(lang, "export.snapshot", {"date": generated}), 8, "#6b7280", right=True)
        y = h - margin - 22
        for line in _filters_text(payload, lang):
            text(margin, y, fit(line, 8.5, w - 2 * margin), 8.5, "#374151")
            y -= 11
        text(margin, y, translate(lang, "export.data_version", {"version": payload.get("dataset_version"), "rows": payload.get("rows_in_scope")}), 7.5, "#6b7280")
        return y - 14

    def footer() -> None:
        text(w - margin, 16, translate(lang, "export.page", {"n": page_no[0]}), 7.5, "#6b7280", right=True)
        text(margin, 16, translate(lang, "export.footer"), 7.5, "#6b7280")

    y = header()
    # --- cartes KPI
    cards = payload.get("cards", [])
    per_row = min(4, max(1, len(cards)))
    card_w = (w - 2 * margin - (per_row - 1) * 10) / per_row
    card_h = 58
    for i, card in enumerate(cards):
        col = i % per_row
        if col == 0 and i > 0:
            y -= card_h + 10
        x = margin + col * (card_w + 10)
        c.setStrokeColor("#d1d5db")
        c.setFillColor("#f9fafb")
        c.roundRect(x, y - card_h, card_w, card_h, 6, fill=1, stroke=1)
        text(x + 8, y - 14, fit(card["label"], 8.5, card_w - 16), 8.5, "#374151")
        vals = card.get("values") or []
        if card["status"] != "ok":
            text(x + 8, y - 36, translate(lang, "dashboard.unavailable"), 13, "#9ca3af")
            text(x + 8, y - 50, fit(translate(lang, f"reasons.{card.get('reason', 'no_values')}"), 7, card_w - 16), 7, "#9ca3af")
        else:
            lines = [format_number(v["value"], lang, card["unit"], v.get("currency"), card.get("decimals")) if v["value"] is not None
                     else translate(lang, "dashboard.unavailable") for v in vals[:3]]
            size = 15 if len(lines) == 1 else 10
            yy = y - 34 if len(lines) == 1 else y - 28
            for ln in lines:
                text(x + 8, yy, fit(ln, size, card_w - 16), size)
                yy -= size + 2
            var = card.get("variation")
            if var and var.get("status") == "ok" and var.get("values") and var["values"][0].get("rate") is not None:
                r = var["values"][0]["rate"]
                color = "#15803d" if Decimal(r) >= 0 else "#b91c1c"
                text(x + card_w - 8, y - 14, ("+" if Decimal(r) >= 0 else "") + format_number(r, lang, "percent"), 8, color, right=True)
    y -= card_h + 16
    # --- charts (2 per row)
    charts = [ch for ch in payload.get("charts", [])]
    chart_w = (w - 2 * margin - 14) / 2
    chart_h = 170
    for i, ch in enumerate(charts):
        col = i % 2
        if col == 0 and i > 0:
            y -= chart_h + 18
        if y - chart_h < 40:
            footer()
            c.showPage()
            page_no[0] += 1
            y = header()
        x = margin + col * (chart_w + 14)
        _draw_chart(c, ch, x, y - chart_h, chart_w, chart_h, lang, text, fit)
    if charts:
        y -= chart_h + 20
    # --- detail table (paginated)
    detail = payload.get("detail") or {}
    columns = detail.get("columns", [])[:10]
    rows = detail.get("rows", [])
    if columns and rows:
        if y < 120:
            footer()
            c.showPage()
            page_no[0] += 1
            y = header()
        text(margin, y, translate(lang, "export.detail_title", {"shown": len(rows), "total": detail.get("total", len(rows))}), 9.5)
        y -= 14
        col_w = (w - 2 * margin) / len(columns)

        def table_header(yy: float) -> float:
            c.setFillColor("#f3f4f6")
            c.rect(margin, yy - 4, w - 2 * margin, 14, fill=1, stroke=0)
            for j, col_def in enumerate(columns):
                text(margin + j * col_w + 3, yy, fit(col_def["label"], 7.5, col_w - 6), 7.5, "#374151")
            return yy - 14

        y = table_header(y)
        for r in rows:
            if y < 36:
                footer()
                c.showPage()
                page_no[0] += 1
                y = header()
                y = table_header(y)
            for j, v in enumerate(r["values"][: len(columns)]):
                ctype = columns[j].get("type")
                if v is None:
                    s = "—"
                elif ctype in ("number", "money", "percent"):
                    s = format_number(str(v), lang, "number", decimals=None if "." not in str(v) else min(4, len(str(v).split(".")[1])))
                else:
                    s = str(v)
                text(margin + j * col_w + 3, y, fit(s, 7.5, col_w - 6), 7.5)
            y -= 11
    footer()
    c.save()
    return buf.getvalue()


def _draw_chart(c: Any, ch: dict[str, Any], x: float, y: float, w: float, h: float, lang: str, text: Any, fit: Any) -> None:
    c.setStrokeColor("#e5e7eb")
    c.setFillColor("#ffffff")
    c.roundRect(x, y, w, h, 6, fill=1, stroke=1)
    text(x + 8, y + h - 14, fit(ch.get("title", ""), 9, w - 16), 9)
    series = ch.get("series") or []
    if ch.get("status") != "ok" or not series:
        text(x + 8, y + h / 2, translate(lang, "dashboard.unavailable"), 10, "#9ca3af")
        return
    pts = series[0]["points"]
    vals = [Decimal(p["y"]) for s in series for p in s["points"] if p["y"] is not None]
    if not vals:
        text(x + 8, y + h / 2, translate(lang, "dashboard.unavailable"), 10, "#9ca3af")
        return
    vmax = max(max(vals), Decimal(0))
    vmin = min(min(vals), Decimal(0))
    span = (vmax - vmin) or Decimal(1)
    left, bottom, right, top = x + 46, y + 24, x + w - 10, y + h - 24
    pw, ph = right - left, top - bottom
    c.setStrokeColor("#d1d5db")
    c.line(left, bottom, right, bottom)
    text(left - 4, top - 6, format_number(str(vmax), lang, str(ch.get("unit")) if ch.get("unit") != "currency" else "number"), 6.5, "#6b7280", right=True)
    text(left - 4, bottom, format_number(str(vmin), lang, str(ch.get("unit")) if ch.get("unit") != "currency" else "number"), 6.5, "#6b7280", right=True)

    def ypos(v: Decimal) -> float:
        # drawing coordinates only (displayed values stay decimal)
        return bottom + float((v - vmin) / span) * ph

    if ch["type"] == "line":
        for si, s in enumerate(series[:4]):
            c.setStrokeColor(PALETTE[si % len(PALETTE)])
            c.setLineWidth(1.4)
            n = max(1, len(s["points"]) - 1)
            prev = None
            for i, p in enumerate(s["points"]):
                px = left + pw * i / n
                if p["y"] is None:
                    prev = None  # gap: missing data, no fake zero
                    continue
                py = ypos(Decimal(p["y"]))
                if prev:
                    c.line(prev[0], prev[1], px, py)
                c.setFillColor(PALETTE[si % len(PALETTE)])
                c.circle(px, py, 1.4, stroke=0, fill=1)
                prev = (px, py)
        step = max(1, len(pts) // 6)
        for i in range(0, len(pts), step):
            px = left + pw * i / max(1, len(pts) - 1)
            text(px - 12, bottom - 10, pts[i]["x"][:7], 6.5, "#6b7280")
    else:
        horizontal = ch["type"] == "hbar"
        n = len(pts)
        if horizontal:
            bh = ph / max(1, n)
            for i, p in enumerate(pts):
                yy = top - (i + 1) * bh + 2
                text(x + 6, yy + bh / 2 - 3, fit(p["x"], 6.5, 38), 6.5, "#374151")
                if p["y"] is not None:
                    v = Decimal(p["y"])
                    c.setFillColor(PALETTE[0])
                    x0 = left + float((Decimal(0) - vmin) / span) * pw
                    x1 = left + float((v - vmin) / span) * pw
                    c.rect(min(x0, x1), yy, abs(x1 - x0), bh - 4, fill=1, stroke=0)
        else:
            bw = pw / max(1, n)
            for i, p in enumerate(pts):
                xx = left + i * bw + 2
                if p["y"] is not None:
                    v = Decimal(p["y"])
                    c.setFillColor(PALETTE[0])
                    y0, y1 = ypos(Decimal(0)), ypos(v)
                    c.rect(xx, min(y0, y1), bw - 4, abs(y1 - y0), fill=1, stroke=0)
                text(xx, bottom - 10, fit(p["x"], 6.5, bw - 2), 6.5, "#6b7280")
    if len(series) > 1:
        lx = left
        for si, s in enumerate(series[:4]):
            c.setFillColor(PALETTE[si % len(PALETTE)])
            c.rect(lx, y + h - 26, 6, 6, fill=1, stroke=0)
            text(lx + 8, y + h - 26, str(s.get("currency") or "—"), 6.5, "#374151")
            lx += 50


def render_png(pdf_bytes: bytes, scale: float = 2.0) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[0]
        img = page.render(scale=scale, may_draw_forms=False).to_pil()
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    finally:
        doc.close()


DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n", "|", "%")


def neutralize(value: Any) -> str:
    """Neutralises formulas in TEXT cells only; real numbers (Decimal) are left untouched."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s.startswith(DANGEROUS_PREFIX):
        return "'" + s
    return s


def render_csv(labels: list[str], rows: list[list[Any]], lang: str) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([neutralize(x) for x in [translate(lang, "export.source_column")] + labels])
    for r in rows:
        writer.writerow([neutralize(v) for v in r])
    return ("﻿" + out.getvalue()).encode("utf-8")
