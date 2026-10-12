"""Six languages: key parity, keys used by the frontend present, exports with no missing glyph."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from kpibridge.exports import format_number, render_pdf, render_png
from kpibridge.i18n import LANGS, translate

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "shared" / "locales"


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        kk = f"{p}.{k}" if p else k
        out.update(flat(v, kk) if isinstance(v, dict) else {kk: v})
    return out


def test_key_parity_and_placeholders():
    data = {lang: flat(json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))) for lang in LANGS}
    ref = data["fr"]
    for lang, d in data.items():
        assert set(d) == set(ref), (lang, set(ref) ^ set(d))
        for k, v in d.items():
            assert v.strip(), (lang, k)
            assert set(re.findall(r"\{\{(\w+)\}\}", v)) == set(re.findall(r"\{\{(\w+)\}\}", ref[k])), (lang, k)


def test_frontend_static_keys_exist():
    src = ROOT / "frontend" / "src"
    keys = set()
    for f in src.rglob("*.tsx"):
        keys |= set(re.findall(r"""\bt\(\s*["']([a-z_]+(?:\.[a-z_A-Z0-9-]+)+)["']""", f.read_text(encoding="utf-8")))
    fr = flat(json.loads((LOCALES / "fr.json").read_text(encoding="utf-8")))
    missing = sorted(k for k in keys if k not in fr)
    assert not missing, missing
    assert len(keys) > 120


def test_error_codes_raised_in_backend_are_translated():
    codes = set()
    for f in (ROOT / "backend" / "kpibridge").rglob("*.py"):
        codes |= set(re.findall(r"""(?:UserError|LimitExceeded)\(\s*["']([a-z_]+)["']""", f.read_text(encoding="utf-8")))
    fr = flat(json.loads((LOCALES / "fr.json").read_text(encoding="utf-8")))
    missing = sorted(c for c in codes if f"errors.{c}" not in fr)
    assert not missing, missing


PAYLOAD = {
    "cards": [{"id": "s", "label": "Выручка / 销售额", "unit": "currency", "decimals": None, "status": "ok", "values": [{"currency": "EUR", "value": "1234567.891", "reason": None}],
               "variation": {"status": "ok", "values": [{"rate": "12.5"}]}},
              {"id": "u", "label": "Marge", "unit": "currency", "decimals": None, "status": "unavailable", "reason": "division_by_zero", "values": []}],
    "charts": [{"id": "c", "type": "line", "title": "По месяцам 按月", "unit": "currency", "status": "ok",
                "series": [{"currency": "EUR", "points": [{"x": "2026-01-01", "y": "10"}, {"x": "2026-02-01", "y": None}, {"x": "2026-03-01", "y": "30"}]}]},
               {"id": "b", "type": "hbar", "title": "Top", "unit": "number", "status": "ok",
                "series": [{"currency": None, "points": [{"x": "Товар Б", "y": "5"}, {"x": "产品甲", "y": "3"}]}]}],
    "detail": {"columns": [{"id": "c0", "label": "Название", "type": "text"}, {"id": "c1", "label": "金额", "type": "number"}],
               "rows": [{"row": i, "src": "", "values": [f"Строка {i} 行", f"{i}.5"]} for i in range(120)], "total": 120},
    "active_filters": {"period_from": "2026-01-01", "period_to": "2026-03-31", "dims": {"c0": ["Товар Б"]}},
    "filters": {"dims": [{"field": "c0", "label": "Товар"}]},
    "global_filters": [],
    "dataset_version": 3,
    "rows_in_scope": 120,
    "coverage": ["2026-01-01", "2026-03-31"],
}


@pytest.mark.parametrize("lang", LANGS)
def test_pdf_export_glyphs_pagination_and_filters(lang):
    pdf = render_pdf(PAYLOAD, lang, "Проект 项目")
    doc = pdfium.PdfDocument(pdf)
    assert len(doc) >= 2  # the detail table is paginated
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    for s in ("Выручка", "销售额", "Строка 0", "Проект", "项目"):
        assert s in text, (lang, s)
    assert "2026-01-01" in text and "Товар Б" in text  # period and filters visible
    assert translate(lang, "export.page", {"n": 1}) in text
    # no replacement glyph ("tofu"): every character is mapped in the embedded font
    assert "�" not in text
    # every character of the translated export texts exists in an embedded font (no "□" replacement)
    from kpibridge.exports import MISSING_GLYPH, _runs

    loc = flat(json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8")))
    sample = " ".join(v for k, v in loc.items() if k.startswith(("export.", "reasons.", "dashboard.unavailable")))
    assert MISSING_GLYPH not in "".join(t for _f, t in _runs(sample + "Выручка 销售额 Строка 行 € ₽ ¥")), lang
    assert MISSING_GLYPH in "".join(t for _f, t in _runs("⋈"))  # uncovered character: visibly replaced
    png = render_png(pdf)
    assert png.startswith(b"\x89PNG") and len(png) > 20_000


def test_locale_number_formatting():
    assert format_number("1234567.891", "fr", "currency", "EUR") == "1 234 567,89 €"
    assert format_number("1234567.891", "en", "currency", "EUR") == "€1,234,567.89"
    assert format_number("1234567.891", "de", "number") == "1.234.567,89"
    assert format_number("12.345", "ru", "percent") == "12,3 %"
    assert format_number(None, "zh-Hans") == translate("zh-Hans", "dashboard.unavailable")
