"""Compilation du plan validé vers des requêtes DuckDB paramétrées et évaluation décimale exacte.

Principes :
- les identifiants de colonnes proviennent uniquement de la liste blanche validée (`c<n>`), jamais d'un texte libre ;
- toutes les valeurs (filtres) sont passées en paramètres ;
- les agrégats sont calculés en DECIMAL par DuckDB, les combinaisons (ratios, différences) en `decimal.Decimal` ;
- absence de valeur ≠ 0 ; division par zéro ⇒ indisponible avec motif ;
- devises différentes ⇒ résultats séparés ;
- `per` (grain) : une valeur par clé, avec contrôle de cohérence (valeur constante par clé, clé non répartie
  entre plusieurs groupes).
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import threading
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import duckdb

from ..plan.grammar import PERIOD, Agg, AnalysisPlan, BinOp, Const, Expr, Filter, Metric, iter_aggs
from ..plan.validate import coerce_value, columns_by_id

_IDENT = re.compile(r"^(c\d{1,3}(_cur)?|_row|_src)$")
GRAINS = {"day", "week", "month", "quarter", "year"}
MAX_POINTS = 400
RESULT_QUANT = Decimal("1e-10")


def q(ident: str) -> str:
    if not _IDENT.match(ident):
        raise ValueError("identifiant non autorisé")
    return f'"{ident}"'


@dataclass
class UserFilters:
    period_from: dt.date | None = None
    period_to: dt.date | None = None
    dims: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserFilters:
        data = data or {}
        pf = data.get("period_from")
        pt = data.get("period_to")
        dims = {k: [str(x)[:200] for x in v][:200] for k, v in (data.get("dims") or {}).items() if isinstance(v, list)}
        return cls(
            period_from=dt.date.fromisoformat(pf) if pf else None,
            period_to=dt.date.fromisoformat(pt) if pt else None,
            dims=dims,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "dims": {k: v for k, v in sorted(self.dims.items()) if v},
        }


class Unavailable(Exception):
    def __init__(self, reason: str, **params: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.params = params


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    return None


def fmt_dec(d: Decimal | None) -> str | None:
    if d is None:
        return None
    if d.as_tuple().exponent < -10:  # type: ignore[operator]
        d = d.quantize(RESULT_QUANT, rounding=ROUND_HALF_EVEN)
    s = format(d, "f")
    return s


def bucket_start(d: dt.date, grain: str) -> dt.date:
    if grain == "day":
        return d
    if grain == "week":
        return d - dt.timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    if grain == "quarter":
        return d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
    return d.replace(month=1, day=1)


def next_bucket(d: dt.date, grain: str) -> dt.date:
    if grain == "day":
        return d + dt.timedelta(days=1)
    if grain == "week":
        return d + dt.timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[grain]
    m = d.month - 1 + months
    return dt.date(d.year + m // 12, m % 12 + 1, 1)


class Engine:
    def __init__(self, con: duckdb.DuckDBPyConnection, lock: threading.Lock, plan: AnalysisPlan, profile: dict[str, Any]) -> None:
        self.con = con
        self.lock = lock
        self.plan = plan
        self.profile = profile
        self.cols = columns_by_id(profile)
        with lock:
            desc = con.execute("DESCRIBE data").fetchall()
        self.sql_types = {r[0]: r[1] for r in desc}

    # ------------------------------------------------------------------ SQL helpers
    def _rows(self, sql: str, params: list[Any]) -> list[tuple]:
        with self.lock:
            return self.con.execute(sql, params).fetchall()

    def _scale(self, field: str) -> int:
        t = self.sql_types.get(field, "")
        m = re.match(r"DECIMAL\((\d+),(\d+)\)", t)
        return int(m.group(2)) if m else 0

    def _filter_sql(self, f: Filter, params: list[Any]) -> str:
        col = self.cols[f.field]
        ident = q(f.field)
        vals = [coerce_value(v, col["type"]) for v in f.values]
        if f.op == "eq":
            params.append(vals[0])
            return f"{ident} = ?"
        if f.op == "neq":
            params.append(vals[0])
            return f"{ident} IS DISTINCT FROM ?"
        if f.op in ("in", "not_in"):
            marks = ", ".join("?" for _ in vals)
            params.extend(vals)
            if f.op == "in":
                return f"{ident} IN ({marks})"
            return f"({ident} IS NULL OR {ident} NOT IN ({marks}))"
        if f.op in ("gt", "gte", "lt", "lte"):
            params.append(vals[0])
            return f"{ident} {dict(gt='>', gte='>=', lt='<', lte='<=')[f.op]} ?"
        if f.op == "between":
            params.extend(vals[:2])
            return f"{ident} BETWEEN ? AND ?"
        if f.op == "is_null":
            return f"{ident} IS NULL"
        if f.op == "not_null":
            return f"{ident} IS NOT NULL"
        raise ValueError(f.op)

    def scope_filters(self, user: UserFilters, period: tuple[dt.date, dt.date] | None = "default", skip_dim: str | None = None) -> list[Filter]:  # type: ignore[assignment]
        """Filtres de périmètre : filtres globaux du plan + filtres utilisateur (période, catégories)."""
        flt = list(self.plan.global_filters)
        for dim, values in user.dims.items():
            if dim == skip_dim or not values or dim not in self.cols:
                continue
            flt.append(Filter(field=dim, op="in", values=values))
        pf = self.plan.period_field
        if pf:
            if period == "default":
                lo, hi = user.period_from, user.period_to
            elif period is None:
                lo = hi = None
            else:
                lo, hi = period
            if lo:
                flt.append(Filter(field=pf, op="gte", values=[lo.isoformat()]))
            if hi:
                flt.append(Filter(field=pf, op="lte", values=[hi.isoformat()]))
        return flt

    def where(self, filters: list[Filter], params: list[Any]) -> str:
        parts = [self._filter_sql(f, params) for f in filters]
        return " AND ".join(parts) if parts else "TRUE"

    def _key_exprs(self, keys: list[str]) -> list[str]:
        out = []
        for k in keys:
            if k == PERIOD:
                grain = self.plan.time_grain
                if grain not in GRAINS or not self.plan.period_field:
                    raise ValueError("grain")
                out.append(f"CAST(date_trunc('{grain}', {q(self.plan.period_field)}) AS DATE)")
            else:
                out.append(q(k))
        return out

    # ------------------------------------------------------------------ agrégats
    def agg_values(self, a: Agg, keys: list[str], scope: list[Filter]) -> dict[tuple, Any]:
        params: list[Any] = []
        flt = scope + list(a.filters)
        where = self.where(flt, params)
        kexprs = self._key_exprs(keys)
        ksel = ", ".join(f"{e} AS k{i}" for i, e in enumerate(kexprs))
        kgrp = ", ".join(f"k{i}" for i in range(len(kexprs)))
        sel_prefix = f"{ksel}, " if ksel else ""
        grp = f"GROUP BY {kgrp}" if kgrp else ""
        f = q(a.field) if a.field else None
        if a.per and a.field and a.fn not in ("count_rows", "count_distinct"):
            per = q(a.per)
            inner = (
                f"SELECT {sel_prefix}{per} AS gk, ANY_VALUE({f}) AS v, COUNT(DISTINCT {f}) AS nv "
                f"FROM data WHERE {where} AND {per} IS NOT NULL GROUP BY {kgrp + ', ' if kgrp else ''}gk"
            )
            chk = self._rows(f"SELECT COALESCE(MAX(nv), 0) FROM ({inner})", list(params))[0][0]
            if chk and chk > 1:
                raise Unavailable("grain_conflict", field=a.field, key=a.per)
            if kgrp:
                spread = self._rows(f"SELECT COALESCE(MAX(c), 0) FROM (SELECT gk, COUNT(*) c FROM ({inner}) GROUP BY gk)", list(params))[0][0]
                if spread and spread > 1:
                    raise Unavailable("grain_dimension_conflict", key=a.per)
            source = f"({inner})"
            fexpr = "v"
            outer_where = "TRUE"
            ksel2 = ", ".join(f"k{i}" for i in range(len(kexprs)))
            sel_prefix2 = f"{ksel2}, " if ksel2 else ""
        else:
            source = "data"
            fexpr = f or "NULL"
            outer_where = where
            sel_prefix2 = sel_prefix
        if a.fn == "sum":
            agg_sql = f"SUM({fexpr}), COUNT({fexpr})"
        elif a.fn == "count_rows":
            agg_sql = "COUNT(*), COUNT(*)"
        elif a.fn == "count":
            agg_sql = f"COUNT({fexpr}), COUNT(*)"
        elif a.fn == "count_distinct":
            agg_sql = f"COUNT(DISTINCT {fexpr}), COUNT(*)"
        elif a.fn == "avg":
            agg_sql = f"SUM({fexpr}), COUNT({fexpr})"
        elif a.fn == "median":
            scale = min(37, self._scale(a.field or "") + 1)
            agg_sql = f"MEDIAN(CAST({fexpr} AS DECIMAL(38,{scale}))), COUNT({fexpr})"
        elif a.fn in ("min", "max"):
            agg_sql = f"{a.fn.upper()}({fexpr}), COUNT({fexpr})"
        else:  # pragma: no cover
            raise ValueError(a.fn)
        sql = f"SELECT {sel_prefix2}{agg_sql} FROM {source} WHERE {outer_where} {grp}"
        rows = self._rows(sql, params if source == "data" else params)
        out: dict[tuple, Any] = {}
        nk = len(kexprs)
        for r in rows:
            key = tuple(r[:nk])
            val, cnt = r[nk], r[nk + 1]
            if a.fn == "avg":
                if not cnt:
                    out[key] = None
                else:
                    with localcontext() as ctx:
                        ctx.prec = 40
                        out[key] = _dec(val) / Decimal(cnt)  # type: ignore[operator]
            elif a.fn in ("count_rows", "count", "count_distinct"):
                out[key] = Decimal(val or 0)
            elif a.fn in ("min", "max") and isinstance(val, (dt.date, dt.datetime)):
                out[key] = val
            else:
                out[key] = _dec(val) if cnt else None
        return out

    # ------------------------------------------------------------------ évaluation
    def money_fields(self, metric: Metric) -> list[str]:
        """Champs monétaires du KPI : type « montant », ou nombre agrégé dans un KPI d'unité monétaire."""
        out = []
        for a in iter_aggs(metric.expression):
            if not a.field or a.fn not in ("sum", "avg", "median", "min", "max"):
                continue
            col = self.cols.get(a.field, {})
            t = col.get("type")
            monetary_role = bool({"amount", "cost", "unit_price"} & set(col.get("roles", [])))
            if t == "money" or (t == "number" and (metric.unit == "currency" or monetary_role)):
                out.append(a.field)
        return out

    def currency_key(self, metric: Metric) -> str | None:
        money = self.money_fields(metric)
        if not money:
            return None
        derived = (self.profile.get("derived_currency") or {}).get(money[0])
        if derived:
            return derived
        if self.plan.currency_field:
            return self.plan.currency_field
        if self.cols[money[0]].get("currency"):
            return None
        cur_cols = self.profile.get("currency_columns") or []
        if len(cur_cols) == 1:
            return cur_cols[0]
        return None

    def fixed_currency(self, metric: Metric) -> str | None:
        for a in iter_aggs(metric.expression):
            if a.field and self.cols.get(a.field, {}).get("currency"):
                return self.cols[a.field]["currency"]
        return None

    def eval_metric(self, metric: Metric, keys: list[str], scope: list[Filter]) -> dict[tuple, tuple[Decimal | None, str | None]]:
        scope = scope + list(metric.filters)
        aggs = iter_aggs(metric.expression)
        cache: dict[int, dict[tuple, Any]] = {}
        failure: Unavailable | None = None
        for a in aggs:
            try:
                cache[id(a)] = self.agg_values(a, keys, scope)
            except Unavailable as u:
                failure = u
                break
        if failure is not None:
            return {(): (None, failure.reason)}
        all_keys: set[tuple] = set()
        for vals in cache.values():
            all_keys.update(vals.keys())
        if not keys:
            all_keys = {()}
        out: dict[tuple, tuple[Decimal | None, str | None]] = {}
        for key in all_keys:
            out[key] = self._eval(metric.expression, key, cache)
        return out

    def _eval(self, expr: Expr, key: tuple, cache: dict[int, dict[tuple, Any]]) -> tuple[Decimal | None, str | None]:
        if isinstance(expr, Const):
            return Decimal(expr.value), None
        if isinstance(expr, Agg):
            vals = cache[id(expr)]
            if key not in vals:
                if expr.fn in ("count_rows", "count", "count_distinct"):
                    return Decimal(0), None
                return None, "no_values"
            v = vals[key]
            if v is None:
                return None, "no_values"
            if isinstance(v, (dt.date, dt.datetime)):
                return None, "date_value"
            return v, None
        assert isinstance(expr, BinOp)
        lv, lr = self._eval(expr.left, key, cache)
        rv, rr = self._eval(expr.right, key, cache)
        if lv is None:
            return None, lr
        if rv is None:
            return None, rr
        with localcontext() as ctx:
            ctx.prec = 40
            if expr.op == "add":
                return lv + rv, None
            if expr.op == "sub":
                return lv - rv, None
            if expr.op == "mul":
                return lv * rv, None
            if rv == 0:
                return None, "division_by_zero"
            return lv / rv, None

    # ------------------------------------------------------------------ dashboard
    def period_bounds(self, scope: list[Filter]) -> tuple[dt.date, dt.date] | None:
        pf = self.plan.period_field
        if not pf:
            return None
        params: list[Any] = []
        where = self.where(scope, params)
        r = self._rows(f"SELECT MIN({q(pf)}), MAX({q(pf)}) FROM data WHERE {where}", params)[0]
        if r[0] is None:
            return None
        return r[0], r[1]

    def data_bounds(self) -> tuple[dt.date, dt.date] | None:
        pf = self.plan.period_field
        if not pf:
            return None
        r = self._rows(f"SELECT MIN({q(pf)}), MAX({q(pf)}) FROM data", [])[0]
        return (r[0], r[1]) if r[0] is not None else None

    def comparison_windows(self, user: UserFilters) -> tuple[tuple[dt.date, dt.date], tuple[dt.date, dt.date], str] | None:
        if not self.plan.period_field:
            return None
        if user.period_from and user.period_to:
            span = (user.period_to - user.period_from).days + 1
            prev_to = user.period_from - dt.timedelta(days=1)
            prev_from = user.period_from - dt.timedelta(days=span)
            return (user.period_from, user.period_to), (prev_from, prev_to), "previous_equal_window"
        bounds = self.period_bounds(self.scope_filters(user))
        if bounds is None:
            return None
        last = bounds[1]
        cur_from = last.replace(day=1)
        cur_to = last.replace(day=calendar.monthrange(last.year, last.month)[1])
        prev_to = cur_from - dt.timedelta(days=1)
        prev_from = prev_to.replace(day=1)
        return (cur_from, cur_to), (prev_from, prev_to), "last_month_vs_previous"

    def variation(self, metric: Metric, user: UserFilters, ckey: str | None) -> dict[str, Any]:
        windows = self.comparison_windows(user)
        if windows is None:
            return {"status": "unavailable", "reason": "no_period"}
        cur_w, prev_w, rule = windows
        data_bounds = self.data_bounds()
        info: dict[str, Any] = {
            "rule": rule,
            "current": [cur_w[0].isoformat(), cur_w[1].isoformat()],
            "previous": [prev_w[0].isoformat(), prev_w[1].isoformat()],
        }
        if data_bounds is None or prev_w[1] < data_bounds[0]:
            return {**info, "status": "unavailable", "reason": "no_comparable_period"}
        # couverture partielle de la période précédente : signalée, jamais masquée
        info["previous_partial"] = prev_w[0] < data_bounds[0]
        info["data_start"] = data_bounds[0].isoformat()
        keys = [ckey] if ckey else []
        cur = self.eval_metric(metric, keys, self.scope_filters(user, period=cur_w))
        prev_scope = self.scope_filters(user, period=prev_w)
        params: list[Any] = []
        n_prev = self._rows(f"SELECT COUNT(*) FROM data WHERE {self.where(prev_scope, params)}", params)[0][0]
        if not n_prev:
            return {**info, "status": "unavailable", "reason": "no_comparable_period"}
        prev = self.eval_metric(metric, keys, prev_scope)
        values = []
        for key in sorted(set(cur) | set(prev), key=lambda k: tuple(str(x) for x in k)):
            c, cr = cur.get(key, (None, "no_values"))
            p, pr = prev.get(key, (None, "no_values"))
            entry: dict[str, Any] = {"currency": key[0] if key else None, "current": fmt_dec(c), "previous": fmt_dec(p)}
            if c is None or p is None:
                entry.update(status="unavailable", reason=cr or pr)
            elif p == 0:
                entry.update(status="unavailable", reason="division_by_zero", delta=fmt_dec(c - p))
            else:
                with localcontext() as ctx:
                    ctx.prec = 40
                    entry.update(status="ok", delta=fmt_dec(c - p), rate=fmt_dec((c - p) / abs(p) * 100))
            values.append(entry)
        return {**info, "status": "ok", "values": values}

    def card(self, metric: Metric, user: UserFilters) -> dict[str, Any]:
        scope = self.scope_filters(user)
        ckey = self.currency_key(metric)
        keys = [ckey] if ckey else []
        res = self.eval_metric(metric, keys, scope)
        fixed = self.fixed_currency(metric)
        values = []
        for key in sorted(res, key=lambda k: tuple(str(x) for x in k)):
            v, reason = res[key]
            values.append({"currency": (key[0] if key else fixed), "value": fmt_dec(v), "reason": reason})
        params: list[Any] = []
        rows_in_scope = self._rows(f"SELECT COUNT(*) FROM data WHERE {self.where(scope + list(metric.filters), params)}", params)[0][0]
        nulls: dict[str, int] = {}
        for a in iter_aggs(metric.expression):
            if a.field:
                p2: list[Any] = []
                w = self.where(scope + list(metric.filters) + list(a.filters), p2)
                nulls[a.field] = self._rows(f"SELECT COUNT(*) - COUNT({q(a.field)}) FROM data WHERE {w}", p2)[0][0]
        status = "ok" if values and any(x["value"] is not None for x in values) else "unavailable"
        out: dict[str, Any] = {
            "id": metric.id,
            "label": metric.label,
            "unit": metric.unit,
            "decimals": metric.decimals,
            "status": status,
            "values": values,
            "currency_split": ckey is not None and len(values) > 1,
            "rows_in_scope": rows_in_scope,
            "nulls_ignored": {k: v for k, v in nulls.items() if v},
        }
        if status == "unavailable":
            out["reason"] = next((x["reason"] for x in values if x["reason"]), "no_values")
        if metric.compare == "previous_period":
            out["variation"] = self.variation(metric, user, ckey)
        return out

    def chart(self, ch: Any, user: UserFilters) -> dict[str, Any]:
        metric = next(m for m in self.plan.metrics if m.id == ch.metric)
        scope = self.scope_filters(user)
        ckey = self.currency_key(metric)
        keys = [ch.dimension] + ([ckey] if ckey else [])
        res = self.eval_metric(metric, keys, scope)
        out: dict[str, Any] = {"id": ch.id, "type": ch.type, "metric": metric.id, "title": ch.title or metric.label, "unit": metric.unit,
                               "dimension": ch.dimension, "dimension_label": self._label(ch.dimension), "decimals": metric.decimals}
        if () in res and res[()][0] is None and res[()][1]:
            out.update(status="unavailable", reason=res[()][1], series=[])
            return out
        series: dict[str | None, dict[Any, tuple[Decimal | None, str | None]]] = {}
        for key, val in res.items():
            if len(key) != len(keys):
                continue
            cur = key[1] if ckey else self.fixed_currency(metric)
            series.setdefault(cur, {})[key[0]] = val
        result_series = []
        for cur, points in sorted(series.items(), key=lambda kv: str(kv[0])):
            if ch.dimension == PERIOD:
                present = [k for k in points if k is not None]
                if not present:
                    continue
                start, end = min(present), max(present)
                xs: list[dt.date] = []
                d = bucket_start(start, self.plan.time_grain)
                while d <= end and len(xs) < MAX_POINTS:
                    xs.append(d)
                    d = next_bucket(d, self.plan.time_grain)
                pts = [{"x": x.isoformat(), "y": fmt_dec(points.get(x, (None, None))[0]), "reason": points.get(x, (None, "absent"))[1]} for x in xs]
                truncated = 0
            else:
                items = [(k, v) for k, v in points.items()]
                null_label = [i for i in items if i[0] is None]
                items = [i for i in items if i[0] is not None]
                if ch.sort == "desc":
                    items.sort(key=lambda kv: (kv[1][0] is None, -(kv[1][0] or 0)))
                elif ch.sort == "asc":
                    items.sort(key=lambda kv: (kv[1][0] is None, kv[1][0] or 0))
                else:
                    items.sort(key=lambda kv: str(kv[0]))
                total = len(items)
                limit = ch.top_n or 20
                truncated = max(0, total - limit)
                items = items[:limit]
                pts = [{"x": self._x(k), "y": fmt_dec(v[0]), "reason": v[1]} for k, v in items]
                if null_label:
                    out["null_dimension"] = fmt_dec(null_label[0][1][0])
            result_series.append({"currency": cur, "points": pts, "others_hidden": truncated})
        out.update(status="ok" if result_series else "unavailable", series=result_series)
        if not result_series:
            out["reason"] = "no_values"
        return out

    def _x(self, v: Any) -> str:
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def _label(self, field_id: str) -> str:
        if field_id == PERIOD:
            return self._label(self.plan.period_field) if self.plan.period_field else ""
        return self.cols.get(field_id, {}).get("name", field_id)

    def filter_options(self, user: UserFilters) -> dict[str, Any]:
        dims = []
        for d in self.plan.filter_dimensions:
            if d not in self.cols:
                continue
            params: list[Any] = []
            where = self.where(self.scope_filters(user, skip_dim=d), params)
            rows = self._rows(f"SELECT {q(d)}, COUNT(*) FROM data WHERE {where} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 200", params)
            dims.append({"field": d, "label": self._label(d), "values": [{"value": self._x(v) if v is not None else None, "count": c} for v, c in rows]})
        bounds = self.data_bounds()
        return {
            "period": {"field": self.plan.period_field, "label": self._label(PERIOD), "min": bounds[0].isoformat(), "max": bounds[1].isoformat()} if bounds else None,
            "dims": dims,
        }

    def detail(self, user: UserFilters, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        cols = self.plan.table_columns or [c["id"] for c in self.profile["columns"] if c["type"] != "ignore"][:30]
        cols = [c for c in cols if c in self.sql_types]
        params: list[Any] = []
        where = self.where(self.scope_filters(user), params)
        total = self._rows(f"SELECT COUNT(*) FROM data WHERE {where}", list(params))[0][0]
        sel = ", ".join(q(c) for c in cols)
        rows = self._rows(
            f"SELECT _row, _src, {sel} FROM data WHERE {where} ORDER BY _row LIMIT ? OFFSET ?",
            params + [max(1, min(limit, 500)), max(0, offset)],
        )
        return {
            "columns": [{"id": c, "label": self._label(c), "type": self.cols.get(c, {}).get("type")} for c in cols],
            "rows": [{"row": r[0], "src": r[1], "values": [self._cell(v) for v in r[2:]]} for r in rows],
            "total": total,
            "offset": offset,
        }

    @staticmethod
    def _cell(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, Decimal):
            return format(v, "f")
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        if isinstance(v, bool):
            return v
        return str(v)

    def all_rows(self, user: UserFilters) -> tuple[list[str], list[str], list[list[Any]]]:
        cols = [c["id"] for c in self.profile["columns"] if c["type"] != "ignore"]
        cols = [c for c in cols if c in self.sql_types]
        params: list[Any] = []
        where = self.where(self.scope_filters(user), params)
        rows = self._rows(f"SELECT _src, {', '.join(q(c) for c in cols)} FROM data WHERE {where} ORDER BY _row", params)
        return cols, [self._label(c) for c in cols], [list(r) for r in rows]

    def dashboard(self, user: UserFilters) -> dict[str, Any]:
        cards = [self.card(m, user) for m in self.plan.metrics]
        charts = [self.chart(c, user) for c in self.plan.charts]
        params: list[Any] = []
        scope = self.scope_filters(user)
        n_scope = self._rows(f"SELECT COUNT(*) FROM data WHERE {self.where(scope, params)}", params)[0][0]
        return {
            "cards": cards,
            "charts": charts,
            "filters": self.filter_options(user),
            "active_filters": user.to_dict(),
            "global_filters": [f.model_dump() for f in self.plan.global_filters],
            "rows_in_scope": n_scope,
            "coverage": self.period_bounds(scope) and [d.isoformat() for d in self.period_bounds(scope)],  # type: ignore[union-attr]
        }

    # ------------------------------------------------------------------ preuve
    def describe_expr(self, expr: Expr) -> dict[str, Any]:
        if isinstance(expr, Const):
            return {"const": expr.value}
        if isinstance(expr, Agg):
            return {
                "fn": expr.fn,
                "field": expr.field,
                "field_label": self._label(expr.field) if expr.field else None,
                "per": expr.per,
                "per_label": self._label(expr.per) if expr.per else None,
                "filters": [self.describe_filter(f) for f in expr.filters],
            }
        assert isinstance(expr, BinOp)
        return {"op": expr.op, "left": self.describe_expr(expr.left), "right": self.describe_expr(expr.right)}

    def describe_filter(self, f: Filter) -> dict[str, Any]:
        return {"field": f.field, "label": self._label(f.field), "op": f.op, "values": f.values}

    def evidence(self, metric_id: str, user: UserFilters, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        metric = next((m for m in self.plan.metrics if m.id == metric_id), None)
        if metric is None:
            raise KeyError(metric_id)
        scope = self.scope_filters(user) + list(metric.filters)
        fields = sorted({a.field for a in iter_aggs(metric.expression) if a.field} | {a.per for a in iter_aggs(metric.expression) if a.per})
        params: list[Any] = []
        where = self.where(scope, params)
        queries = []
        for a in iter_aggs(metric.expression):
            p2: list[Any] = []
            w = self.where(scope + list(a.filters), p2)
            queries.append({"aggregate": self.describe_expr(a), "where_sql": w, "params": [self._cell(x) for x in p2]})
        total = self._rows(f"SELECT COUNT(*) FROM data WHERE {where}", list(params))[0][0]
        sel = ", ".join(q(f) for f in fields) if fields else "NULL"
        rows = self._rows(f"SELECT _row, _src, {sel} FROM data WHERE {where} ORDER BY _row LIMIT ? OFFSET ?", params + [min(limit, 200), offset])
        return {
            "metric": metric.model_dump(),
            "definition": self.describe_expr(metric.expression),
            "fields": [{"id": f, "label": self._label(f), "type": self.cols.get(f, {}).get("type")} for f in fields],
            "scope_filters": [self.describe_filter(f) for f in scope],
            "queries": queries,
            "currency_key": self.currency_key(metric),
            "contributing_rows": {
                "total": total,
                "offset": offset,
                "columns": [self._label(f) for f in fields],
                "rows": [{"row": r[0], "src": r[1], "values": [self._cell(v) for v in r[2:]]} for r in rows],
            },
        }
