"""Clarifications prioritaires (1 à 3 par étape) avant calcul, déduites déterministiquement du plan et du profil.

Ordre de priorité : convention de date ambiguë > convention numérique ambiguë > devise inconnue > lignes annulées /
remboursées > montant de niveau commande répété par ligne > HT/TTC > doublons exacts.
Aucune question cosmétique (couleur, titre).
"""

from __future__ import annotations

from typing import Any

from ..normalize import TableConfig
from .grammar import Agg, AnalysisPlan, Filter, iter_aggs
from .validate import columns_by_id, fields_used

MAX_QUESTIONS = 3
COMMON_CURRENCIES = ["EUR", "USD", "GBP", "CHF", "RUB", "CNY"]


def _money_fields(plan: AnalysisPlan, cols: dict[str, dict]) -> list[str]:
    out = []
    for m in plan.metrics:
        if m.unit == "currency":
            for a in iter_aggs(m.expression):
                if a.fn not in ("sum", "avg", "median", "min", "max"):
                    continue
                if a.field and cols.get(a.field, {}).get("type") in ("number", "money") and a.field not in out:
                    out.append(a.field)
    return out


def questions_for(plan: AnalysisPlan, profile: dict[str, Any], cfg: TableConfig, table_questions: list[dict], answers: dict[str, Any]) -> list[dict[str, Any]]:
    cols = columns_by_id(profile)
    used = fields_used(plan)
    qs: list[dict[str, Any]] = []
    # 1-2. conventions ambiguës sur des champs utilisés (dates : seulement si un KPI temporel / période est utilisé)
    for tq in table_questions:
        col = tq.get("params", {}).get("column")
        if col not in used:
            continue
        if tq["kind"] == "date_order":
            qs.append({"key": tq["key"], "kind": "date_order", "params": tq["params"], "options": ["DMY", "MDY"]})
        elif tq["kind"] == "number_convention":
            qs.append({"key": tq["key"], "kind": "number_convention", "params": tq["params"], "options": ["dot", "comma"]})
    money = _money_fields(plan, cols)
    # 3. devise inconnue
    for f in money:
        c = cols[f]
        key = f"currency:{f}"
        if key in answers or cfg.confirmed.get(key):
            continue
        has_currency = c.get("currency") or f in (profile.get("derived_currency") or {}) or plan.currency_field or (profile.get("currency_columns") or [])
        if not has_currency:
            qs.append({"key": key, "kind": "currency", "params": {"column": f, "name": c["name"]}, "options": COMMON_CURRENCIES + ["none"]})
    # 4. annulations / remboursements
    if money:
        for c in profile["columns"]:
            vals = c.get("cancel_values")
            key = f"cancelled:{c['id']}"
            if not vals or key in answers:
                continue
            if any(f.field == c["id"] for f in plan.global_filters):
                continue
            qs.append({"key": key, "kind": "cancelled_rows", "params": {"column": c["id"], "name": c["name"], "values": vals[:8]}, "options": ["exclude", "include"]})
    # 5. montant de niveau commande répété sur chaque ligne
    for g in (profile.get("grain") or {}).values():
        for f in g.get("order_level_fields", []):
            key = f"order_level:{f}"
            if key in answers:
                continue
            for m in plan.metrics:
                if any(a.field == f and a.fn in ("sum", "avg", "median") and a.per is None for a in iter_aggs(m.expression)):
                    qs.append({"key": key, "kind": "order_level_amount", "params": {"column": f, "name": cols[f]["name"], "key_column": g["key"],
                               "key_name": cols[g["key"]]["name"]}, "options": ["once_per_order", "per_line"]})
                    break
    # 6. HT / TTC
    ht = [c for c in profile["columns"] if "tax_mode_ht" in c.get("roles", []) and c["type"] in ("number", "money")]
    ttc = [c for c in profile["columns"] if "tax_mode_ttc" in c.get("roles", []) and c["type"] in ("number", "money")]
    if ht and ttc and "amount_basis" not in answers and any(f in money for f in [ht[0]["id"], ttc[0]["id"]]):
        qs.append({"key": "amount_basis", "kind": "amount_basis",
                   "params": {"ht": ht[0]["name"], "ttc": ttc[0]["name"], "option_labels": {ht[0]["id"]: ht[0]["name"], ttc[0]["id"]: ttc[0]["name"]}},
                   "options": [ht[0]["id"], ttc[0]["id"]]})
    # 7. doublons exacts
    dup = profile.get("duplicates") or {}
    if dup.get("groups") and "duplicates" not in answers and not cfg.confirmed.get("duplicates"):
        qs.append({"key": "duplicates", "kind": "duplicates", "params": {"groups": dup["groups"], "rows": dup["rows"]}, "options": ["keep", "exclude"]})
    # dédoublonnage des clés, max 3
    seen: set[str] = set()
    unique = []
    for q in qs:
        if q["key"] in seen or q["key"] in answers:
            continue
        seen.add(q["key"])
        unique.append(q)
    return unique[:MAX_QUESTIONS]


def apply_plan_answer(plan: AnalysisPlan, question: dict[str, Any], answer: str) -> AnalysisPlan:
    """Applique une réponse qui modifie le plan (les réponses de configuration de table sont gérées par le service)."""
    kind = question["kind"]
    data = plan.model_copy(deep=True)
    if kind == "cancelled_rows" and answer == "exclude":
        data.global_filters.append(Filter(field=question["params"]["column"], op="not_in", values=question["params"]["values"]))
    elif kind == "order_level_amount" and answer == "once_per_order":
        field, key = question["params"]["column"], question["params"]["key_column"]
        for m in data.metrics:
            for a in iter_aggs(m.expression):
                if isinstance(a, Agg) and a.field == field and a.fn in ("sum", "avg", "median"):
                    a.per = key
    elif kind == "amount_basis":
        chosen = answer
        other = [o for o in question["options"] if o != chosen]
        for m in data.metrics:
            for a in iter_aggs(m.expression):
                if a.field in other:
                    a.field = chosen
    return data
