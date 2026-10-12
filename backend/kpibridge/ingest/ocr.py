"""OCR local (Tesseract) et reconstruction de tableau à partir de la disposition des mots.

Le score OCR est conservé par cellule, mais n'est PAS présenté comme une probabilité d'exactitude du tableau :
il sert uniquement à signaler des cellules à vérifier (« cellule OCR peu lisible »).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import UserError

LANG_MAP = {"fr": "fra", "en": "eng", "ru": "rus", "zh-Hans": "chi_sim", "de": "deu", "es": "spa"}
LOW_CONF = 70.0


@dataclass
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def cy(self) -> float:
        return self.top + self.height / 2


def tessdata_dir(tesseract_cmd: str | None) -> str | None:
    """Répertoire des modèles de langue pour une installation Tesseract locale (conda-forge) si présent."""
    from pathlib import Path

    if not tesseract_cmd:
        return None
    exe = Path(tesseract_cmd)
    for cand in (exe.parent.parent.parent / "share" / "tessdata", exe.parent / "tessdata", exe.parent.parent / "share" / "tessdata"):
        if (cand / "eng.traineddata").exists():
            return str(cand)
    return None


def ocr_langs(ui_lang: str, explicit: str | None) -> str:
    if explicit:
        parts = [p for p in explicit.split("+") if p in set(LANG_MAP.values())]
        if parts:
            return "+".join(parts)
    base = LANG_MAP.get(ui_lang, "eng")
    return base if base == "eng" else f"{base}+eng"


def run_ocr(image: Image.Image, tesseract_cmd: str | None, langs: str, timeout: int = 120) -> list[Word]:
    if not tesseract_cmd:
        raise UserError("ocr_unavailable")
    import os

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    tessdata = tessdata_dir(tesseract_cmd)
    if tessdata and not os.environ.get("TESSDATA_PREFIX"):
        os.environ["TESSDATA_PREFIX"] = tessdata
    try:
        data = pytesseract.image_to_data(
            image, lang=langs, config="--psm 6", output_type=pytesseract.Output.DICT, timeout=timeout
        )
    except pytesseract.TesseractError as exc:  # sous-classe de RuntimeError : à tester en premier
        raise UserError("ocr_failed") from exc
    except RuntimeError as exc:  # délai dépassé
        raise UserError("ocr_timeout") from exc
    words: list[Word] = []
    for i, txt in enumerate(data["text"]):
        text = (txt or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if not text or conf < 0:
            continue
        words.append(Word(text, int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i]), conf))
    return words


def _lines(words: list[Word]) -> list[list[Word]]:
    if not words:
        return []
    med_h = statistics.median(w.height for w in words) or 10
    ordered = sorted(words, key=lambda w: (w.cy, w.left))
    lines: list[list[Word]] = []
    for w in ordered:
        if lines and abs(w.cy - statistics.mean(x.cy for x in lines[-1])) <= med_h * 0.6:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w.left) for line in lines]


def _cells(line: list[Word], gap: float) -> list[list[Word]]:
    cells: list[list[Word]] = []
    for w in line:
        if cells and w.left - cells[-1][-1].right <= gap:
            cells[-1].append(w)
        else:
            cells.append([w])
    return cells


def words_to_grid(words: list[Word], page_no: int) -> tuple[list[list[str | None]], list[str], dict[str, dict[str, Any]], int]:
    """Construit une grille (lignes × colonnes) à partir des mots et de leurs coordonnées.

    Retourne (grille, provenance par ligne, métadonnées par cellule, nombre de cellules peu lisibles).
    """
    lines = _lines(words)
    if not lines:
        return [], [], {}, 0
    med_h = statistics.median(w.height for w in words) or 10
    gap = med_h * 1.2
    line_cells = [_cells(line, gap) for line in lines]
    # Colonnes de référence : la ligne avec le plus de cellules (souvent l'en-tête ou une ligne de données complète)
    n_cols = max(len(c) for c in line_cells)
    anchors_line = next(c for c in line_cells if len(c) == n_cols)
    anchors = [(min(w.left for w in cell), max(w.right for w in cell)) for cell in anchors_line]
    bounds = []
    for i in range(n_cols):
        lo = -10**9 if i == 0 else (anchors[i - 1][1] + anchors[i][0]) / 2
        hi = 10**9 if i == n_cols - 1 else (anchors[i][1] + anchors[i + 1][0]) / 2
        bounds.append((lo, hi))
    grid: list[list[str | None]] = []
    srcs: list[str] = []
    meta: dict[str, dict[str, Any]] = {}
    low = 0
    for r, cells in enumerate(line_cells):
        row: list[str | None] = [None] * n_cols
        row_words = [w for c in cells for w in c]
        for cell in cells:
            cx = (min(w.left for w in cell) + max(w.right for w in cell)) / 2
            col = next((i for i, (lo, hi) in enumerate(bounds) if lo <= cx < hi), n_cols - 1)
            text = " ".join(w.text for w in cell)
            conf = min(w.conf for w in cell)
            bbox = [min(w.left for w in cell), min(w.top for w in cell), max(w.right for w in cell), max(w.top + w.height for w in cell)]
            if row[col]:
                row[col] = f"{row[col]} {text}"
                prev = meta.get(f"{r},{col}", {})
                conf = min(conf, prev.get("conf", conf))
                pb = prev.get("bbox", bbox)
                bbox = [min(pb[0], bbox[0]), min(pb[1], bbox[1]), max(pb[2], bbox[2]), max(pb[3], bbox[3])]
            else:
                row[col] = text
            meta[f"{r},{col}"] = {"conf": round(conf, 1), "bbox": bbox, "page": page_no}
        for key, m in list(meta.items()):
            if key.startswith(f"{r},") and m["conf"] < LOW_CONF:
                m["low"] = True
                low += 1
        lb = [min(w.left for w in row_words), min(w.top for w in row_words), max(w.right for w in row_words), max(w.top + w.height for w in row_words)]
        grid.append(row)
        srcs.append(f"page={page_no};bbox={lb[0]},{lb[1]},{lb[2]},{lb[3]};ocr=1")
    return grid, srcs, meta, low


def prepare_image(path: Path, max_pixels: int) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(path) as probe:
            probe.verify()
        img = Image.open(path)
        if img.width * img.height > max_pixels:
            raise UserError("image_too_large")
        img.load()
    except Image.DecompressionBombError as exc:
        raise UserError("image_too_large") from exc
    except UserError:
        raise
    except Exception as exc:  # noqa: BLE001 — tout échec de décodage est un fichier illisible
        raise UserError("corrupted_file") from exc
    gray: Image.Image = img.convert("L")
    if gray.width < 1200:
        factor = min(3, max(1, 1600 // max(1, gray.width)))
        if factor > 1:
            gray = gray.resize((gray.width * factor, gray.height * factor), Image.Resampling.LANCZOS)
    return gray
