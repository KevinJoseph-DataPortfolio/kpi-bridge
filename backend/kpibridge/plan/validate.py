"""Validation sémantique d'un plan contre le profil réel de la table (champs, types, unités, devises, grain)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from .grammar import PERIOD, Agg, AnalysisPlan, BinOp, Const, Expr, Filter, expr_depth, iter_aggs

NUMERIC = {"number", "money", "percent"}
DIMENSION_TYPES = {"text", "id", "bool", "date"}


class PlanIssue(dict):
    def __init__(self, code: str, severity: str = "error", metric: str | None = None, **params: Any) -> None:
        super().__init__(code=code, severity=severity, metric=metric, params=params)


def columns_by_id(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cols = {c["id"]: c for c in profile["columns"]}
    for base, did in (profile.get("derived_currency") or {}).items():
        cols[did] = {"id": did, "name": profile["labels"].get(did, did), "type": "text", "roles": ["currency"], "derived_from": base}
    return cols


def parse_plan(data: Any) -> tuple[AnalysisPlan | None, list[PlanIssue]]:
    try:
        return AnalysisPlan.model_validate(data), []
    except ValidationError as exc:
        issues = []
        for err in exc.errors()[:10]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            issues.append(PlanIssue("schema_error", where=loc[:120], detail=str(err.get("type", ""))))
        return None, issues


def coerce_value(value: str, col_type: str) -> Any:
    """Convertit une valeur de filtre (chaîne) vers le type canonique de la colonne. Lève ValueError."""
    if col_type in NUMERIC:
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("nombre attendu") from exc
    if col_type == "date":
        return date.fromisoformat(value[:10])
    if col_type == "bool":
        if value.lower() in ("true", "1"):
            return True
        if value.lower() in ("false", "0"):
            return False
        raise ValueError("booléen attendu")
    return value


def _check_filter(f: Filter, cols: dict[str, dict], issues: list[PlanIssue], metric: str | None) -> None:
    col = cols.get(f.field)
    if col is None:
        issues.append(PlanIssue("unknown_field", metric=metric, field=f.field))
        return
    if f.op in ("gt", "gte", "lt", "lte", "between") and col["type"] not in NUMERIC | {"date"}:
        issues.append(PlanIssue("incompatible_filter", metric=metric, field=f.field, op=f.op))
    if f.op in ("eq", "neq", "gt", "gte", "lt", "lte") and len(f.values) != 1:
        issues.append(PlanIssue("filter_arity", metric=metric, field=f.field, op=f.op))
    if f.op == "between" and len(f.values) != 2:
        issues.append(PlanIssue("filter_arity", metric=metric, field=f.field, op=f.op))
    if f.op in ("in", "not_in") and not f.values:
        issues.append(PlanIssue("filter_arity", metric=metric, field=f.field, op=f.op))
    for v in f.values:
        try:
            coerce_value(v, col["type"])
        except ValueError:
            issues.append(PlanIssue("filter_value_type", metric=metric, field=f.field, value=v[:40]))


def _money_currency(col: dict[str, Any], profile: dict[str, Any]) -> str | None:
    if col["type"] != "money":
        return None
    if col.get("currency"):
        return col["currency"]
    if col["id"] in (profile.get("derived_currency") or {}):
        return "per_row"
    return None


def validate_plan(plan: AnalysisPlan, profile: dict[str, Any]) -> list[PlanIssue]:
    cols = columns_by_id(profile)
    issues: list[PlanIssue] = []
    metric_ids = set()
    for m in plan.metrics:
        if m.id in metric_ids:
            issues.append(PlanIssue("duplicate_metric_id", metric=m.id))
        metric_ids.add(m.id)
        if expr_depth(m.expression) > 4:
            issues.append(PlanIssue("expression_too_deep", metric=m.id))
        aggs = iter_aggs(m.expression)
        if not aggs:
            issues.append(PlanIssue("expression_without_aggregate", metric=m.id))
        if len(aggs) > 6:
            issues.append(PlanIssue("expression_too_complex", metric=m.id))
        currencies: set[str] = set()
        has_money = False
        for a in aggs:
            _check_agg(a, m.id, m.rate_rule, cols, issues)
            if a.field and a.field in cols:
                cur = _money_currency(cols[a.field], profile)
                if cols[a.field]["type"] == "money":
                    has_money = True
                if cur and cur != "per_row":
                    currencies.add(cur)
            for f in a.filters:
                _check_filter(f, cols, issues, m.id)
        if len(currencies) > 1:
            issues.append(PlanIssue("currency_mismatch", metric=m.id, currencies=sorted(currencies)))
        if m.unit == "currency" and not has_money and not _has_amount_like(aggs, cols):
            issues.append(PlanIssue("unit_mismatch", severity="warning", metric=m.id, unit=m.unit))
        for f in m.filters:
            _check_filter(f, cols, issues, m.id)
        if m.compare == "previous_period" and not plan.period_field:
            issues.append(PlanIssue("compare_needs_period", metric=m.id))
        _check_div(m.expression, m.id, issues)
    for f in plan.global_filters:
        _check_filter(f, cols, issues, None)
    if plan.period_field:
        pc = cols.get(plan.period_field)
        if pc is None:
            issues.append(PlanIssue("unknown_field", field=plan.period_field))
        elif pc["type"] != "date":
            issues.append(PlanIssue("period_not_date", field=plan.period_field))
    if plan.currency_field:
        cc = cols.get(plan.currency_field)
        if cc is None or cc["type"] not in ("text", "id"):
            issues.append(PlanIssue("invalid_currency_field", field=plan.currency_field))
    for d in plan.filter_dimensions:
        c = cols.get(d)
        if c is None:
            issues.append(PlanIssue("unknown_field", field=d))
        elif c["type"] not in DIMENSION_TYPES - {"date"}:
            issues.append(PlanIssue("invalid_dimension", field=d))
    chart_ids = set()
    for ch in plan.charts:
        if ch.id in chart_ids:
            issues.append(PlanIssue("duplicate_chart_id", chart=ch.id))
        chart_ids.add(ch.id)
        if ch.metric not in metric_ids:
            issues.append(PlanIssue("unknown_metric", chart=ch.id, metric_ref=ch.metric))
        if ch.dimension == PERIOD:
            if not plan.period_field:
                issues.append(PlanIssue("chart_needs_period", chart=ch.id))
        else:
            c = cols.get(ch.dimension)
            if c is None:
                issues.append(PlanIssue("unknown_field", chart=ch.id, field=ch.dimension))
            elif c["type"] not in DIMENSION_TYPES:
                issues.append(PlanIssue("invalid_dimension", chart=ch.id, field=ch.dimension))
            if ch.type == "line":
                issues.append(PlanIssue("line_needs_period", severity="warning", chart=ch.id))
            if c is not None and c.get("distinct", 0) > 20 and not ch.top_n:
                issues.append(PlanIssue("too_many_categories", severity="warning", chart=ch.id, count=c.get("distinct")))
    if plan.table_columns:
        for tc in plan.table_columns:
            if tc not in cols:
                issues.append(PlanIssue("unknown_field", field=tc))
    return issues


def _has_amount_like(aggs: list[Agg], cols: dict[str, dict]) -> bool:
    return any(a.field and a.field in cols and "amount" in cols[a.field].get("roles", []) for a in aggs)


def _check_agg(a: Agg, metric: str, rate_rule: str | None, cols: dict[str, dict], issues: list[PlanIssue]) -> None:
    if a.fn == "count_rows":
        if a.field is not None:
            issues.append(PlanIssue("count_rows_no_field", severity="warning", metric=metric))
    else:
        if a.field is None:
            issues.append(PlanIssue("missing_field", metric=metric, fn=a.fn))
            return
        col = cols.get(a.field)
        if col is None:
            issues.append(PlanIssue("unknown_field", metric=metric, field=a.field))
            return
        t = col["type"]
        if a.fn in ("sum", "avg", "median") and t not in NUMERIC:
            issues.append(PlanIssue("incompatible_aggregation", metric=metric, field=a.field, fn=a.fn, type=t))
        if a.fn in ("min", "max") and t not in NUMERIC | {"date"}:
            issues.append(PlanIssue("incompatible_aggregation", metric=metric, field=a.field, fn=a.fn, type=t))
        if a.fn == "sum" and t == "percent":
            issues.append(PlanIssue("sum_of_rates", metric=metric, field=a.field))
        if a.fn == "avg" and t == "percent" and rate_rule != "row_average":
            issues.append(PlanIssue("rate_average_needs_rule", metric=metric, field=a.field))
    if a.per is not None:
        pc = cols.get(a.per)
        if pc is None:
            issues.append(PlanIssue("unknown_field", metric=metric, field=a.per))
        elif pc["type"] not in ("text", "id", "number"):
            issues.append(PlanIssue("invalid_grain_key", metric=metric, field=a.per))
        if a.fn in ("count_rows", "count_distinct"):
            issues.append(PlanIssue("grain_not_applicable", severity="warning", metric=metric))


def _check_div(expr: Expr, metric: str, issues: list[PlanIssue]) -> None:
    if isinstance(expr, BinOp):
        if expr.op == "div" and isinstance(expr.right, Const) and Decimal(expr.right.value) == 0:
            issues.append(PlanIssue("division_by_constant_zero", metric=metric))
        _check_div(expr.left, metric, issues)
        _check_div(expr.right, metric, issues)


def errors_only(issues: list[PlanIssue]) -> list[PlanIssue]:
    return [i for i in issues if i["severity"] == "error"]


def fields_used(plan: AnalysisPlan) -> set[str]:
    out: set[str] = set()
    for m in plan.metrics:
        for a in iter_aggs(m.expression):
            if a.field:
                out.add(a.field)
            if a.per:
                out.add(a.per)
            out.update(f.field for f in a.filters)
        out.update(f.field for f in m.filters)
    out.update(f.field for f in plan.global_filters)
    if plan.period_field:
        out.add(plan.period_field)
    out.update(plan.filter_dimensions)
    out.update(c.dimension for c in plan.charts if c.dimension != "__period__")
    return out
