"""Extraction de valeurs explicitement présentes dans du texte, chacune reliée à son passage source.

Règle : seules les lignes de la forme « libellé : nombre [unité] » sont retenues. Le backend vérifie que la valeur
figure littéralement dans le passage cité. Aucune complétion de chiffre manquant, aucune interprétation libre.
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
        # Preuve : la valeur doit apparaître telle quelle dans le passage
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
