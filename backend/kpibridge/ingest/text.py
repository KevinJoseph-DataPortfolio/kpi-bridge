"""Extracts values explicitly present in text, each linked to its source passage.

Rule: only lines shaped like "label: number [unit]" are kept. The backend checks that the value appears
verbatim in the quoted passage. No completion of a missing figure, no free interpretation.
"""

from __future__ import annotations

import re

from .base import RawTable, clean_text

VALUE_LINE = re.compile(
    r"^\s*(?P<label>[^\d:=|\t]{2,80}?)\s*(?::|=|\t|\s[-–—]\s|\.{3,})\s*"
    r"(?P<value>[-+−]?\s?[$€£¥₽]?\s?\d[\d\s.,'  ]*\d|[-+−]?\s?[$€£¥₽]?\s?\d)"
    r"\s*(?P<unit>%|€|\$|£|¥|₽|EUR|USD|GBP|RUB|CNY|CHF|k€|M€|[A-Za-zА-Яа-яЁё一-鿿]{1,8})?\s*\.?\s*$"
)


def extract_text_values(lines: list[tuple[str, str]], name: str) -> RawTable | None:
    rows: list[list[str | None]] = [["Libellé / Label", "Valeur / Value", "Unité / Unit", "Passage"]]
    srcs = ["header"]
    for text, src in lines:
        line = clean_text(text)
        if not line:
            continue
        m = VALUE_LINE.match(line)
        if not m:
            continue
        value = m.group("value").strip()
        # Evidence: the value must appear verbatim in the passage
        if value not in line:
            continue
        label = m.group("label").strip(" .-–—")
        if not label or label.isdigit():
            continue
        rows.append([label, value, (m.group("unit") or None), line[:200]])
        srcs.append(f"{src};chars={m.start('value')}-{m.end('value')}")
    if len(rows) < 2:
        return None
    t = RawTable(name=f"{name} · texte", kind="text", rows=rows, row_src=srcs, header_row=0, col_refs=["1", "2", "3", "4"])
    t.note("text_values_extracted", count=len(rows) - 1)
    return t
