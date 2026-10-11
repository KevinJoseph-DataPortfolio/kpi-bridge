"""Normalisation : configuration de table (réversible) → jeu typé versionné + profil de qualité.

La configuration (`TableConfig`) contient toutes les décisions : ligne d'en-tête, type et convention de chaque colonne,
lignes exclues (avec motif), corrections de cellules (avec valeur d'origine). Une nouvelle configuration produit une
nouvelle `DatasetVersion` ; revenir en arrière = réappliquer une configuration précédente.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Literal

import pyarrow as pa
from pydantic import BaseModel, Field

from ..ingest.base import decode, display
from .parse import (
    date_shape,
    has_leading_zero_id,
    infer_convention,
    infer_date_order,
    parse_bool,
    parse_date,
    parse_number,
    split_number,
)
from .roles import guess_roles, is_cancel_value

ColType = Literal["text", "id", "number", "money", "percent", "date", "bool", "ignore"]
MAX_SCALE = 12


class ColumnSpec(BaseModel):
    id: str
    raw_index: int
    name: str
    type: ColType
    convention: Literal["dot", "comma"] | None = None
    date_order: Literal["DMY", "MDY"] | None = None
    currency: str | None = None


class CellCorrection(BaseModel):
    value: str
    original: str | None = None


class TableConfig(BaseModel):
    header_row: int = 0
    header_depth: int = 1
    columns: list[ColumnSpec] = Field(default_factory=list)
    excluded: dict[int, str] = Field(default_factory=dict)
    corrections: dict[str, CellCorrection] = Field(default_factory=dict)
    confirmed: dict[str, Any] = Field(default_factory=dict)


# ----------------------------------------------------------------------------- inférence


def header_names(rows: list[list[str | None]], header_row: int, header_depth: int, width: int) -> list[str]:
    names: list[str] = []
    seen: Counter = Counter()
    for j in range(width):
        parts = []
        for d in range(header_depth):
            r = header_row + d
            if 0 <= r < len(rows) and j < len(rows[r]):
                txt = display(rows[r][j]).strip()
                if txt and txt not in parts:
                    parts.append(txt)
        base = " / ".join(parts) or f"Colonne {j + 1}"
        seen[base] += 1
        names.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return names


def data_row_indices(n_rows: int, config: TableConfig) -> list[int]:
    start = config.header_row + config.header_depth
    return [i for i in range(start, n_rows) if i not in config.excluded]


def _infer_column(values: list[str | None]) -> dict[str, Any]:
    """Infère type et conventions d'une colonne à partir de ses cellules brutes."""
    kinds = Counter()
    texts: list[str] = []
    for cell in values:
        kind, val, _ = decode(cell)
        if kind == "empty":
            continue
        kinds[kind] += 1
        if kind == "text" and val is not None:
            texts.append(val)
    total = sum(kinds.values())
    out: dict[str, Any] = {"type": "text", "convention": None, "date_order": None, "currency": None, "ambiguous": []}
    if total == 0:
        out["type"] = "text"
        return out
    native_num = kinds["N"] + kinds["P"]
    if kinds["F"] and kinds["F"] == total - native_num:
        # formules (avec ou sans cache) : résultat numérique, valeurs sans cache indisponibles
        out["type"] = "number"
        return out
    if kinds["D"] and kinds["D"] + len([t for t in texts if date_shape(t)]) >= 0.8 * total:
        out["type"] = "date"
    elif native_num and native_num >= 0.8 * (total - kinds["F"]) and native_num > 0:
        out["type"] = "percent" if kinds["P"] > kinds["N"] else "number"
    elif texts:
        if any(has_leading_zero_id(t) for t in texts):
            out["type"] = "id"
            return out
        shapes = [date_shape(t) for t in texts]
        date_like = sum(1 for s in shapes if s)
        if date_like >= 0.8 * len(texts):
            out["type"] = "date"
            order, ambiguous, examples = infer_date_order(texts)
            out["date_order"] = order
            if ambiguous:
                out["ambiguous"].append({"kind": "date_order", "examples": examples})
            return out
        parts = [split_number(t) for t in texts]
        numeric = [p for p in parts if p is not None]
        if len(numeric) + native_num >= 0.8 * (len(texts) + native_num) and numeric:
            conv, ambiguous, _ok, examples = infer_convention(texts)
            out["convention"] = conv
            if ambiguous:
                out["ambiguous"].append({"kind": "number_convention", "examples": examples})
            currencies = {p[1] for p in numeric if p[1]}
            percents = sum(1 for p in numeric if p[2])
            if currencies:
                out["type"] = "money"
                if len(currencies) == 1:
                    out["currency"] = next(iter(currencies))
                else:
                    out["mixed_currencies"] = sorted(currencies)
            elif percents >= 0.8 * len(numeric):
                out["type"] = "percent"
            else:
                out["type"] = "number"
            return out
        bools = [parse_bool(t) for t in texts]
        if all(b is not None for b in bools) and len({t.lower() for t in texts}) <= 2 and not all(t.isdigit() for t in texts):
            out["type"] = "bool"
    return out


def infer_config(rows: list[list[str | None]], meta: dict[str, Any]) -> tuple[TableConfig, list[dict[str, Any]]]:
    header_row = meta.get("header_row") or 0
    depth = meta.get("header_depth") or 1
    width = max((len(r) for r in rows), default=0)
    excluded = {int(k): v for k, v in (meta.get("excluded") or {}).items()}
    cfg = TableConfig(header_row=header_row, header_depth=depth, excluded=excluded)
    names = header_names(rows, header_row, depth, width)
    data_idx = data_row_indices(len(rows), cfg)
    questions: list[dict[str, Any]] = []
    if not meta.get("header_confident", True):
        questions.append({"kind": "header_row", "params": {"current": header_row}})
    for j in range(width):
        col_vals = [rows[i][j] if j < len(rows[i]) else None for i in data_idx]
        info = _infer_column(col_vals)
        if all(decode(v)[0] == "empty" for v in col_vals) and {"amount", "cost", "quantity"} & set(guess_roles(names[j], "number")):
            # colonne vide nommée comme une mesure : typée nombre, ses agrégats seront « indisponibles », jamais 0
            info["type"] = "number"
        spec = ColumnSpec(
            id=f"c{j}",
            raw_index=j,
            name=names[j],
            type=info["type"],
            convention=info["convention"],
            date_order=info["date_order"],
            currency=info["currency"],
        )
        cfg.columns.append(spec)
        for amb in info["ambiguous"]:
            questions.append({"kind": amb["kind"], "params": {"column": spec.id, "name": spec.name, "examples": amb["examples"]}})
    return cfg, questions


# ----------------------------------------------------------------------------- application


class NormalizedResult:
    def __init__(self, table: pa.Table, profile: dict[str, Any]) -> None:
        self.table = table
        self.profile = profile


def _scale_of(d: Decimal) -> int:
    exp = d.as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def apply_config(rows: list[list[str | None]], row_src: list[str], meta: dict[str, Any], cfg: TableConfig) -> NormalizedResult:
    data_idx = data_row_indices(len(rows), cfg)
    cell_meta = meta.get("cell_meta") or {}
    arrays: dict[str, pa.Array] = {}
    col_profiles: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    derived_currency: dict[str, str] = {}
    typed_cols: dict[str, list[Any]] = {}
    for spec in cfg.columns:
        if spec.type == "ignore":
            continue
        j = spec.raw_index
        values: list[Any] = []
        invalid: list[dict[str, Any]] = []
        formula_missing = 0
        ocr_low = 0
        currencies: list[str | None] = []
        for i in data_idx:
            cell = rows[i][j] if j < len(rows[i]) else None
            corr = cfg.corrections.get(f"{i},{j}")
            if corr is not None:
                cell = corr.value
            elif cell_meta.get(f"{i},{j}", {}).get("low"):
                ocr_low += 1
            kind, val, _from_cache = decode(cell)
            cur: str | None = None
            if kind == "empty":
                values.append(None)
            elif kind == "F":
                values.append(None)
                formula_missing += 1
            elif spec.type in ("text", "id"):
                values.append(display(cell) if kind != "text" else val)
            elif spec.type in ("number", "money", "percent"):
                if kind == "N":
                    values.append(Decimal(val or "0"))
                elif kind == "P":
                    d = Decimal(val or "0")
                    values.append(d * 100 if spec.type == "percent" else d)
                elif kind == "text" and val is not None:
                    parsed = parse_number(val, spec.convention or "dot")
                    if parsed is None:
                        values.append(None)
                        if len(invalid) < 50:
                            invalid.append({"row": i, "raw": val[:60]})
                        else:
                            invalid.append({"row": i})
                    else:
                        values.append(parsed.value)
                        cur = parsed.currency
                else:
                    values.append(None)
                    invalid.append({"row": i, "raw": str(val)[:60]})
            elif spec.type == "date":
                if kind == "D" and val:
                    try:
                        values.append(dt.date.fromisoformat(val[:10]))
                    except ValueError:
                        values.append(None)
                        invalid.append({"row": i, "raw": val[:60]})
                elif kind == "text" and val is not None:
                    d = parse_date(val, spec.date_order)
                    values.append(d)
                    if d is None:
                        invalid.append({"row": i, "raw": val[:60]})
                else:
                    values.append(None)
                    invalid.append({"row": i, "raw": str(val)[:60]})
            elif spec.type == "bool":
                b = (val == "true") if kind == "B" else (parse_bool(val) if val is not None else None)
                values.append(b)
                if b is None and val is not None:
                    invalid.append({"row": i, "raw": val[:60]})
            currencies.append(cur)
        # ---- typage Arrow
        if spec.type in ("number", "money", "percent"):
            scale = min(MAX_SCALE, max((_scale_of(v) for v in values if v is not None), default=0))
            q = Decimal(1).scaleb(-scale)
            values = [v.quantize(q) if v is not None else None for v in values]
            arr = pa.array(values, type=pa.decimal128(38, scale))
        elif spec.type == "date":
            arr = pa.array(values, type=pa.date32())
        elif spec.type == "bool":
            arr = pa.array(values, type=pa.bool_())
        else:
            arr = pa.array(values, type=pa.string())
        arrays[spec.id] = arr
        typed_cols[spec.id] = values
        labels[spec.id] = spec.name
        distinct_cur = sorted({c for c in currencies if c})
        col_currency = spec.currency
        if spec.type == "money" and not col_currency and len(distinct_cur) > 1:
            did = f"{spec.id}_cur"
            arrays[did] = pa.array(currencies, type=pa.string())
            typed_cols[did] = currencies
            labels[did] = f"{spec.name} · devise"
            derived_currency[spec.id] = did
        elif spec.type == "money" and not col_currency and len(distinct_cur) == 1:
            col_currency = distinct_cur[0]
        col_profiles.append(
            _profile_column(spec, values, invalid, formula_missing, ocr_low, col_currency, distinct_cur)
        )
    arrays["_row"] = pa.array(data_idx, type=pa.int32())
    arrays["_src"] = pa.array([row_src[i] if i < len(row_src) else "" for i in data_idx], type=pa.string())
    table = pa.table(arrays)
    profile = _profile_table(cfg, col_profiles, typed_cols, data_idx, labels, derived_currency, meta, len(rows))
    return NormalizedResult(table, profile)


def _profile_column(
    spec: ColumnSpec, values: list[Any], invalid: list[dict], formula_missing: int, ocr_low: int, currency: str | None, currencies: list[str]
) -> dict[str, Any]:
    non_null = [v for v in values if v is not None]
    distinct_set: set = set()
    for v in non_null:
        distinct_set.add(v)
        if len(distinct_set) > 10000:
            break
    prof: dict[str, Any] = {
        "id": spec.id,
        "name": spec.name,
        "type": spec.type,
        "convention": spec.convention,
        "date_order": spec.date_order,
        "currency": currency,
        "currencies": currencies,
        "count": len(values),
        "nulls": len(values) - len(non_null),
        "invalid": len(invalid),
        "invalid_samples": invalid[:20],
        "formula_missing": formula_missing,
        "ocr_low": ocr_low,
        "distinct": len(distinct_set),
        "distinct_capped": len(distinct_set) > 10000,
    }
    if spec.type in ("number", "money", "percent", "date") and non_null:
        prof["min"] = str(min(non_null))
        prof["max"] = str(max(non_null))
    if spec.type in ("text", "id", "bool"):
        top = Counter(str(v) for v in non_null).most_common(12)
        prof["top"] = [{"value": k[:80], "count": c} for k, c in top]
    prof["samples"] = [str(v)[:40] for v in non_null[:5]]
    prof["roles"] = guess_roles(spec.name, spec.type)
    if spec.type in ("text",) and len(distinct_set) <= 30:
        cancel = sorted({str(v) for v in distinct_set if is_cancel_value(str(v))})
        if cancel:
            prof["cancel_values"] = cancel
            if "status" not in prof["roles"]:
                prof["roles"].append("status")
    if spec.type == "text" and prof["roles"] == [] and len(distinct_set) <= 50 and len(values) > 0:
        prof["roles"].append("dimension")
    if spec.type == "text" and set(str(v).upper() for v in distinct_set) and all(
        len(str(v)) == 3 and str(v).isalpha() and str(v).isupper() for v in distinct_set
    ) and 0 < len(distinct_set) <= 10:
        if "currency" not in prof["roles"]:
            prof["roles"].append("currency")
    return prof


def _profile_table(
    cfg: TableConfig,
    cols: list[dict[str, Any]],
    typed: dict[str, list[Any]],
    data_idx: list[int],
    labels: dict[str, str],
    derived_currency: dict[str, str],
    meta: dict[str, Any],
    n_raw: int,
) -> dict[str, Any]:
    n = len(data_idx)
    # doublons exacts : signalés, jamais supprimés automatiquement
    keys = [c["id"] for c in cols]
    hashes: dict[str, list[int]] = defaultdict(list)
    for pos in range(n):
        h = hashlib.blake2b(repr(tuple(typed[k][pos] for k in keys)).encode(), digest_size=12).hexdigest()
        hashes[h].append(data_idx[pos])
    dup_groups = [rows for rows in hashes.values() if len(rows) > 1]
    # grain : clé de commande candidate et champs de niveau commande (constants par commande)
    order_keys = [c["id"] for c in cols if "order_id" in c["roles"] and c["type"] in ("text", "id", "number")]
    grain: dict[str, Any] = {}
    for ok in order_keys:
        key_vals = typed[ok]
        distinct = len({v for v in key_vals if v is not None})
        if distinct == 0:
            continue
        info: dict[str, Any] = {"key": ok, "distinct": distinct, "rows": n, "line_level": distinct < n, "order_level_fields": []}
        if distinct < n:
            key_counts = Counter(v for v in key_vals if v is not None)
            for c in cols:
                if c["type"] not in ("number", "money") or not ({"amount", "cost"} & set(c["roles"])):
                    continue
                per: dict[Any, set] = defaultdict(set)
                for kv, v in zip(key_vals, typed[c["id"]], strict=False):
                    if kv is not None:
                        per[kv].add(v)
                multi = sum(1 for s in per.values() if len(s) > 1)
                repeated_non_null = sum(1 for kv, s in per.items() if key_counts[kv] > 1 and s - {None})
                if multi == 0 and repeated_non_null > 0:
                    info["order_level_fields"].append(c["id"])
        grain[ok] = info
    coverage: dict[str, Any] = {}
    for c in cols:
        if c["type"] == "date" and c.get("min"):
            months = sorted({(d.year, d.month) for d in typed[c["id"]] if d is not None})
            missing: list[str] = []
            if months:
                y, m = months[0]
                present = set(months)
                while (y, m) <= months[-1]:
                    if (y, m) not in present:
                        missing.append(f"{y:04d}-{m:02d}")
                    m += 1
                    if m > 12:
                        y, m = y + 1, 1
            coverage[c["id"]] = {"min": c["min"], "max": c["max"], "months": len(months), "missing_months": missing[:24]}
    excluded_summary = Counter(cfg.excluded.values())
    currency_cols = [c["id"] for c in cols if "currency" in c["roles"]]
    return {
        "n_rows": n,
        "n_raw_rows": n_raw,
        "columns": cols,
        "labels": labels,
        "derived_currency": derived_currency,
        "currency_columns": currency_cols,
        "duplicates": {"groups": len(dup_groups), "rows": sum(len(g) for g in dup_groups), "sample": dup_groups[:20]},
        "excluded": dict(excluded_summary),
        "grain": grain,
        "coverage": coverage,
        "notes": meta.get("notes", []),
        "corrections": len(cfg.corrections),
    }
