"""Détection de structure sur une grille de cellules brutes.

- découpage en blocs (tableaux multiples séparés par des lignes vides) ;
- lignes de titre au-dessus de l'en-tête ;
- choix de la ligne d'en-tête (avec niveau de confiance ; incertitude ⇒ choix utilisateur) ;
- en-têtes répétés, lignes vides, totaux / sous-totaux (mots-clés multilingues ou somme vérifiée).

Aucune ligne n'est supprimée : les lignes écartées sont marquées dans `excluded` avec un motif, et l'utilisateur
peut les réintégrer (transformation réversible).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .base import RawTable, decode

TOTAL_RE = re.compile(
    r"(?i)(^|\b)(sous[-\s]?totaux?|totaux?|total\s+général|grand\s+total|subtotal|sub-total|total|sum|"
    r"summe|gesamt(summe)?|zwischensumme|insgesamt|итого|всего|подытог|suma|subtotal|total\s+general)(\b|$)"
    r"|合计|总计|小计|共计"
)

_NUM_LIKE = re.compile(r"^[\s(+-]*[$€£¥₽]?\s*\d[\d\s.,'  ]*\s*(%|€|\$|£|¥|₽|[A-Z]{3})?\)?\s*$")


def is_empty_row(row: list[str | None]) -> bool:
    return all(c is None or (isinstance(c, str) and c.strip() == "") for c in row)


def cell_is_numeric(cell: str | None) -> bool:
    kind, value, _ = decode(cell)
    if kind in ("N", "P"):
        return True
    if kind == "text" and value is not None:
        return bool(_NUM_LIKE.match(value))
    return False


def cell_is_text(cell: str | None) -> bool:
    kind, value, _ = decode(cell)
    return kind == "text" and value is not None and value.strip() != "" and not _NUM_LIKE.match(value)


def _filled(row: list[str | None]) -> int:
    return sum(1 for c in row if c not in (None, ""))


def header_score(rows: list[list[str | None]], idx: int, width: int) -> float:
    row = rows[idx]
    filled = [c for c in row if c not in (None, "")]
    if not filled or width == 0:
        return 0.0
    texts = [c for c in filled if cell_is_text(c)]
    fill_ratio = len(filled) / width
    text_ratio = len(texts) / len(filled)
    distinct = len({decode(c)[1] for c in filled}) / len(filled)
    # La ligne suivante doit ressembler à des données (au moins un nombre ou date) pour renforcer la confiance
    nxt = rows[idx + 1] if idx + 1 < len(rows) else []
    nxt_num = sum(1 for c in nxt if cell_is_numeric(c) or decode(c)[0] == "D")
    data_bonus = 0.2 if nxt_num > 0 else 0.0
    return fill_ratio * 0.4 + text_ratio * 0.3 + distinct * 0.1 + data_bonus


def detect_header(rows: list[list[str | None]], width: int) -> tuple[int, bool]:
    """Retourne (index, confiant)."""
    best_idx, best = 0, -1.0
    scores = []
    for i in range(min(len(rows) - 1 if len(rows) > 1 else 1, 8)):
        s = header_score(rows, i, width)
        scores.append(s)
        if s > best + 0.05:
            best_idx, best = i, s
    confident = best >= 0.75
    # Ambiguïté : deux candidats proches
    close = [i for i, s in enumerate(scores) if i != best_idx and abs(s - best) < 0.05 and s > 0.6]
    if close:
        confident = False
    return best_idx, confident


def split_blocks(rows: list[list[str | None]]) -> list[tuple[int, int]]:
    """Blocs [start, end) de lignes non vides ; une seule ligne vide entre deux blocs de même largeur reste un bloc."""
    blocks: list[tuple[int, int]] = []
    start = None
    empty_run = 0
    for i, row in enumerate(rows):
        if is_empty_row(row):
            empty_run += 1
            if start is not None and empty_run >= 2:
                blocks.append((start, i - empty_run + 1))
                start = None
            continue
        if start is None:
            start = i
        elif empty_run == 1:
            # une ligne vide isolée : nouveau tableau si la ligne courante ressemble à un en-tête différent
            prev = rows[i - 2] if i >= 2 else []
            if _looks_like_new_header(row, prev):
                blocks.append((start, i - 1))
                start = i
        empty_run = 0
    if start is not None:
        end = len(rows)
        while end > start and is_empty_row(rows[end - 1]):
            end -= 1
        blocks.append((start, end))
    return blocks


def _looks_like_new_header(row: list[str | None], prev: list[str | None]) -> bool:
    texts = [c for c in row if cell_is_text(c)]
    filled = [c for c in row if c not in (None, "")]
    if not filled or len(texts) / len(filled) < 0.8 or len(filled) < 2:
        return False
    prev_num = sum(1 for c in prev if cell_is_numeric(c))
    return prev_num > 0


def _to_decimal(cell: str | None) -> Decimal | None:
    kind, value, _ = decode(cell)
    if kind in ("N", "P") and value is not None:
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    if kind == "text" and value is not None and _NUM_LIKE.match(value):
        cleaned = re.sub(r"[^\d,.\-]", "", value)
        if cleaned.count(",") == 1 and cleaned.count(".") == 0:
            cleaned = cleaned.replace(",", ".")
        elif cleaned.count(",") >= 1 and cleaned.count(".") == 1 and cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        elif cleaned.count(".") >= 1 and cleaned.count(",") == 1 and cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def mark_rows(table: RawTable) -> None:
    """Marque en-têtes répétés, lignes vides et totaux dans la zone de données."""
    if table.header_row is None:
        return
    start = table.header_row + table.header_depth
    header = [decode(c)[1] for c in table.rows[table.header_row]]
    width = table.n_cols
    run_sums: list[Decimal] = [Decimal(0)] * width
    run_counts = [0] * width
    for i in range(start, len(table.rows)):
        row = table.rows[i]
        if i in table.excluded:
            continue
        if is_empty_row(row):
            table.excluded[i] = "blank"
            continue
        values = [decode(c)[1] for c in row]
        if values[: len(header)] == header[: len(values)] and any(header):
            table.excluded[i] = "repeated_header"
            continue
        texts = [v for c, v in zip(row, values, strict=False) if cell_is_text(c) and v]
        if any(TOTAL_RE.search(t) for t in texts):
            table.excluded[i] = "total"
            run_sums = [Decimal(0)] * width
            run_counts = [0] * width
            continue
        # Ligne sans libellé dont chaque nombre égale la somme des lignes précédentes ⇒ total implicite
        nums = {j: _to_decimal(c) for j, c in enumerate(row) if cell_is_numeric(c)}
        nums = {j: v for j, v in nums.items() if v is not None}
        if nums and not texts and len(nums) >= 1 and all(run_counts[j] >= 2 for j in nums):
            if all(run_sums[j] == v for j, v in nums.items()):
                table.excluded[i] = "total"
                run_sums = [Decimal(0)] * width
                run_counts = [0] * width
                continue
        for j, v in nums.items():
            if j < width:
                run_sums[j] += v
                run_counts[j] += 1


def structure_grid(
    rows: list[list[str | None]],
    row_src: list[str],
    name: str,
    kind: str,
    col_refs: list[str] | None = None,
    cell_meta: dict | None = None,
) -> list[RawTable]:
    """Découpe une grille en tableaux et détecte leur structure."""
    tables: list[RawTable] = []
    blocks = split_blocks(rows)
    pending_titles: list[str] = []
    for b_idx, (s, e) in enumerate(blocks):
        block_rows = rows[s:e]
        width = max((len(r) for r in block_rows), default=0)
        filled_rows = [_filled(r) for r in block_rows]
        if e - s <= 2 and max(filled_rows, default=0) <= 1:
            # bloc de titre isolé
            pending_titles.extend(str(decode(c)[1]) for r in block_rows for c in r if c not in (None, ""))
            continue
        # retirer les colonnes entièrement vides du bloc
        keep = [j for j in range(width) if any(j < len(r) and r[j] not in (None, "") for r in block_rows)]
        block = [[(r[j] if j < len(r) else None) for j in keep] for r in block_rows]
        refs = [col_refs[j] for j in keep] if col_refs else [str(j + 1) for j in keep]
        meta: dict = {}
        if cell_meta:
            for key, v in cell_meta.items():
                r, c = (int(x) for x in key.split(","))
                if s <= r < e and c in keep:
                    meta[f"{r - s},{keep.index(c)}"] = v
        header_idx, confident = detect_header(block, len(keep))
        t = RawTable(
            name=name if len(blocks) == 1 else f"{name} · {len(tables) + 1}",
            kind=kind,
            rows=block,
            row_src=row_src[s:e],
            header_row=header_idx,
            header_confident=confident,
            col_refs=refs,
            cell_meta=meta,
        )
        # lignes au-dessus de l'en-tête = titres
        for i in range(header_idx):
            t.excluded[i] = "title"
        if pending_titles:
            t.note("titles_above", titles=pending_titles[:3])
            pending_titles = []
        if len(keep) < width:
            t.note("empty_columns_removed", count=width - len(keep))
        if not confident:
            t.note("header_uncertain")
        mark_rows(t)
        tables.append(t)
    if len(tables) > 1:
        for t in tables:
            t.note("multiple_tables", count=len(tables))
    return tables
