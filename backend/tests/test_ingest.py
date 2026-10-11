"""Import des formats : résultats attendus calculés à la main sur des données synthétiques."""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from kpibridge.errors import LimitExceeded, UserError

from .conftest import agg, metric, value

FONT = Path(__file__).resolve().parents[1] / "kpibridge" / "exports" / "fonts" / "NotoSans-Variable.ttf"


def test_clean_csv_exact_kpis(env):
    data = "order;date;product;amount\nA1;2026-01-05;P1;10.10\nA1;2026-01-05;P2;0.20\nA2;2026-02-01;P1;5.00\n"
    tid = env.ingest_bytes("v.csv", data.replace(";", ",").encode())[0]
    amount = env.col(tid, "amount")
    dash = env.dashboard(tid, {"metrics": [metric("total", "number", agg("sum", amount))]})
    assert value(dash, "total") == [(None, "15.30")]


def test_french_csv_semicolon_decimals_leading_zero_ids(env):
    data = "﻿Rapport\n\nCode client;Date;Montant\n00042;25/01/2026;1 234,50\n00107;26/01/2026;10,25\nTotal;;1 244,75\n"
    tid = env.ingest_bytes("fr.csv", data.encode("utf-8"))[0]
    prof = env.profile(tid)
    cols = {c["name"]: c for c in prof["columns"]}
    assert cols["Code client"]["type"] == "id"
    assert cols["Code client"]["top"][0]["value"] in ("00042", "00107")  # zéros initiaux conservés
    assert cols["Montant"]["convention"] == "comma"
    assert prof["excluded"].get("total") == 1  # ligne Total exclue (pas de double total)
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Montant")))]})
    assert value(dash, "t") == [(None, "1244.75")]


def test_ambiguous_dates_require_clarification(env):
    data = "date,amount\n03/04/2026,10\n05/06/2026,20\n"
    tid = env.ingest_bytes("d.csv", data.encode())[0]
    d, a = env.col(tid, "date"), env.col(tid, "amount")
    plan = env.builder_plan(tid, {"metrics": [metric("t", "number", agg("sum", a))], "charts": [{"id": "c", "type": "line", "metric": "t", "dimension": "__period__"}], "period_field": d})
    assert plan["status"] == "needs_clarification"
    q = next(q for q in plan["questions"] if q["kind"] == "date_order")
    assert "03/04/2026" in q["params"]["examples"]
    plan = env.plans.answer(plan["id"], {q["key"]: "MDY"})
    assert plan["status"] == "ready"
    env.plans.validate(plan["id"])
    dash = env.plans.dashboard(plan["id"], None)
    xs = [p["x"] for p in dash["charts"][0]["series"][0]["points"]]
    assert xs[0] == "2026-03-01" and "2026-05-01" in xs  # 3 mars et 6 mai (MDY)


def _xlsx_complex() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Ventes"
    ws["A1"] = "Rapport fictif"
    ws.merge_cells("A1:C1")
    ws.append([])
    ws.append(["Région", "Produit", "Montant"])
    ws.append(["Nord", "P1", 100])
    ws.append([None, "P2", 50.5])
    ws.merge_cells("A4:A5")  # Région fusionnée sur deux lignes
    ws.append(["Sous-total Nord", None, 150.5])
    ws.append(["Sud", "P1", 20])
    ws.append([None, None, 20])  # total implicite : égal à la somme précédente ? non (une seule ligne) -> conservé
    ws.append(["Total général", None, 190.5])
    other = wb.create_sheet("Coûts")
    other.append(["Produit", "Coût"])
    other.append(["P1", 40])
    other.append(["P2", 10])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_titles_merged_multisheet_subtotals(env):
    tids = env.ingest_bytes("c.xlsx", _xlsx_complex())
    assert len(tids) == 2  # deux feuilles = deux tableaux
    ventes = next(t for t in tids if "Ventes" in env.svc.table_row(t)["name"])
    prof = env.profile(ventes)
    assert prof["excluded"].get("total") == 2 and prof["excluded"].get("title", 0) >= 1
    m = env.col(ventes, "Montant")
    r = env.col(ventes, "Région")
    dash = env.dashboard(ventes, {"metrics": [metric("t", "number", agg("sum", m))], "charts": [{"id": "c", "type": "bar", "metric": "t", "dimension": r}]})
    # 100 + 50.5 + 20 + 20 = 190.5 : sous-total et total général non additionnés
    assert value(dash, "t") == [(None, "190.5")]
    pts = {p["x"]: p["y"] for p in dash["charts"][0]["series"][0]["points"]}
    assert pts["Nord"] == "150.5"  # la cellule fusionnée Région s'applique aux deux lignes


def test_xlsx_formula_without_cache_is_unavailable(env):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Produit", "Prix", "Qté", "Total"])
    ws.append(["A", 2, 3, "=B2*C2"])
    ws.append(["B", 4, 1, "=B3*C3"])
    buf = io.BytesIO()
    wb.save(buf)  # openpyxl n'écrit pas de valeur en cache
    tid = env.ingest_bytes("f.xlsx", buf.getvalue())[0]
    prof = env.profile(tid)
    total = next(c for c in prof["columns"] if c["name"] == "Total")
    assert total["formula_missing"] == 2
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", total["id"]))]})
    c = dash["cards"][0]
    assert c["status"] == "unavailable"  # jamais un zéro fictif


def test_old_xls_is_usable(env):
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Feuille1")
    for j, h in enumerate(["Mois", "Ventes"]):
        ws.write(0, j, h)
    for i, (mth, v) in enumerate([("Jan", 10.5), ("Fév", 20.25)], start=1):
        ws.write(i, 0, mth)
        ws.write(i, 1, v)
    buf = io.BytesIO()
    wb.save(buf)
    tid = env.ingest_bytes("old.xls", buf.getvalue())[0]
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Ventes")))]})
    assert value(dash, "t") == [(None, "30.75")]


def _multipage_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table

    rows = [["Ref", "Montant"]] + [[f"R{i:03d}", f"{i}.50"] for i in range(1, 121)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build([Table(rows, repeatRows=1, style=[("GRID", (0, 0), (-1, -1), 0.5, "black")])])
    return buf.getvalue()


def test_text_pdf_multipage_repeated_headers_and_provenance(env):
    tid = env.ingest_bytes("r.pdf", _multipage_pdf())[0]
    prof = env.profile(tid)
    assert prof["n_rows"] == 120
    assert prof["excluded"].get("repeated_header", 0) >= 1
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Montant")))]})
    # Σ (i + 0.5) pour i = 1..120 = 7260 + 60 = 7320
    assert value(dash, "t") == [(None, "7320.00")]
    detail = dash["detail"]["rows"][0]["src"]
    assert detail.startswith("page=1;bbox=")


def _table_png(rows: list[list[str]], degrade: bool = False) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    font = ImageFont.truetype(str(FONT), 34)
    img = Image.new("RGB", (900, 90 * len(rows) + 60), "white")
    d = ImageDraw.Draw(img)
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            d.text((40 + j * 300, 30 + i * 90), cell, fill="black", font=font)
    if degrade:
        img = img.resize((img.width // 5, img.height // 5)).filter(ImageFilter.GaussianBlur(1.5))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


ROWS = [["Mois", "Ventes"], ["Janvier", "1250"], ["Fevrier", "980"], ["Mars", "1310"]]


@pytest.mark.skipif(not __import__("kpibridge.config", fromlist=["x"]).load_settings().tesseract_cmd, reason="Tesseract absent")
def test_scanned_pdf_ocr_readable(env):
    from PIL import Image

    png = _table_png(ROWS)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150)
    tid = env.ingest_bytes("scan.pdf", buf.getvalue())[0]
    assert env.svc.table_row(tid)["kind"] == "ocr"
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Ventes")))]})
    assert value(dash, "t") == [(None, "3540")]
    # la provenance pointe vers la page et une zone, et l'image source est conservée
    src = dash["detail"]["rows"][0]["src"]
    assert src.startswith("page=1;bbox=") and "ocr=1" in src


@pytest.mark.skipif(not __import__("kpibridge.config", fromlist=["x"]).load_settings().tesseract_cmd, reason="Tesseract absent")
def test_degraded_scan_never_fabricates(env):
    try:
        tids = env.ingest_bytes("bad.png", _table_png(ROWS, degrade=True))
    except UserError as exc:
        assert exc.code in ("image_unreadable", "image_no_table", "no_data_found")
        return
    for tid in tids:
        prof = env.profile(tid)
        for c in prof["columns"]:
            if c["type"] in ("number", "money"):
                # tout nombre présent doit provenir d'une cellule lisible : on ne peut pas dépasser les vraies valeurs
                assert Decimal(c["max"]) <= Decimal("99999")
        # les ambiguïtés sont visibles (cellules peu lisibles ou valeurs illisibles signalées)
        assert any(c.get("ocr_low") or c.get("invalid") for c in prof["columns"]) or prof["n_rows"] <= 3


def test_docx_table_text_without_data_json_supported(env):
    import docx

    d = docx.Document()
    d.add_paragraph("Rapport fictif")
    t = d.add_table(rows=3, cols=2)
    for i, row in enumerate([["Produit", "Ventes"], ["A", "12"], ["B", "30"]]):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    buf = io.BytesIO()
    d.save(buf)
    tid = env.ingest_bytes("t.docx", buf.getvalue())[0]
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Ventes")))]})
    assert value(dash, "t") == [(None, "42")]

    with pytest.raises(UserError) as exc:
        env.ingest_bytes("vide.txt", "Bonjour,\nce texte ne contient aucune donnée chiffrée utile.\n".encode())
    assert exc.value.code == "no_data_found_text"

    tid = env.ingest_bytes("vals.txt", "Chiffre d'affaires : 12 500 €\nNombre de clients : 340\nUne phrase sans valeur.\n".encode())[0]
    prof = env.profile(tid)
    assert prof["n_rows"] == 2
    rows = env.svc.preview(tid)["rows"]
    assert rows[0]["src"].startswith("line=1;chars=")  # passage source localisable

    js = json.dumps({"meta": {"v": 1}, "items": [{"id": "1", "price": {"amount": 2.5}}, {"id": "2", "price": {"amount": 1.25}}]})
    tid = env.ingest_bytes("d.json", js.encode())[0]
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "price.amount")))]})
    assert value(dash, "t") == [(None, "3.75")]

    with pytest.raises(UserError) as exc:
        env.ingest_bytes("bad.json", b'{"a": 1, "b": "x"}')
    assert exc.value.code == "json_unsupported_structure"


@pytest.mark.skipif(not __import__("kpibridge.config", fromlist=["x"]).load_settings().tesseract_cmd, reason="Tesseract absent")
def test_tabular_image(env):
    tid = env.ingest_bytes("t.png", _table_png(ROWS))[0]
    dash = env.dashboard(tid, {"metrics": [metric("t", "number", agg("sum", env.col(tid, "Ventes")))]})
    assert value(dash, "t") == [(None, "3540")]


# ---------------------------------------------------------------------------- sécurité des fichiers


def test_fake_mime_and_refused_types(env):
    with pytest.raises(UserError) as e:
        env.ingest_bytes("evil.csv", b"MZ\x90\x00\x03" + b"\x00" * 100)
    assert e.value.code == "content_mismatch"
    with pytest.raises(UserError) as e:
        env.svc.save_upload(env.pid, "macro.xlsm", iter([b"PK"]))
    assert e.value.code == "macro_file_refused"
    with pytest.raises(UserError) as e:
        env.svc.save_upload(env.pid, "a.zip", iter([b"PK"]))
    assert e.value.code == "archive_refused"
    with pytest.raises(UserError) as e:
        env.svc.save_upload(env.pid, "run.exe", iter([b"MZ"]))
    assert e.value.code == "executable_refused"
    with pytest.raises(UserError) as e:
        env.ingest_bytes("fake.xlsx", b"not a zip at all")
    assert e.value.code == "content_mismatch"


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_traversal_bomb_and_vba_rejected_before_extraction(env):
    base = {"[Content_Types].xml": b"<x/>", "xl/workbook.xml": b"<x/>"}
    with pytest.raises(UserError) as e:
        env.ingest_bytes("t.xlsx", _zip({**base, "../../evil.txt": b"x"}))
    assert e.value.code == "zip_dangerous_path"
    with pytest.raises(UserError) as e:
        env.ingest_bytes("b.xlsx", _zip({**base, "xl/worksheets/sheet1.xml": b"0" * (60 * 1024 * 1024)}))
    assert e.value.code in ("zip_bomb_suspected", "zip_too_large")
    with pytest.raises(UserError) as e:
        env.ingest_bytes("v.xlsx", _zip({**base, "xl/vbaProject.bin": b"x"}))
    assert e.value.code == "macro_file_refused"


def test_oversized_file_stopped_during_streaming(env, settings):
    chunks_read = []

    def gen():
        for _ in range(100):
            chunks_read.append(1)
            yield b"a" * (1024 * 1024)

    with pytest.raises(LimitExceeded) as e:
        env.svc.save_upload(env.pid, "big.csv", gen())
    assert e.value.code == "file_too_large"
    assert len(chunks_read) <= settings.limits.max_file_bytes // (1024 * 1024) + 1  # arrêt borné
    assert not list((settings.projects_dir / env.pid / "sources").iterdir())


def test_row_limit_enforced_during_parse(env, monkeypatch):
    from kpibridge.config import Limits

    env.svc.settings = env.settings.__class__(**{**env.settings.__dict__, "limits": Limits(max_rows=100)})
    data = "a,b\n" + "".join(f"{i},{i}\n" for i in range(2000))
    with pytest.raises(LimitExceeded) as e:
        env.ingest_bytes("many.csv", data.encode())
    assert e.value.code == "too_many_rows"
