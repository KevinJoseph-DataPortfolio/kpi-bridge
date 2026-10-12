"""Suggestions fondées uniquement sur les champs réellement disponibles (déterministes, sans IA).

Chaque suggestion porte un fragment de plan. Choisir des suggestions sans texte libre construit le plan sans modèle :
ce n'est pas une interprétation de langage naturel, et c'est présenté comme tel.
"""

from __future__ import annotations

from typing import Any

from .grammar import AnalysisPlan, Unit
from .llm_schema import AggSpec, ChartSpec, LLMOutput, MetricSpec, compile_output


def _first(profile: dict[str, Any], role: str, types: tuple[str, ...] | None = None) -> dict[str, Any] | None:
    for c in profile["columns"]:
        if role in c.get("roles", []) and (types is None or c["type"] in types):
            return c
    return None


def _dims(profile: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for role in ("category", "product", "region", "channel", "customer", "dimension", "status"):
        for c in profile["columns"]:
            if role in c.get("roles", []) and c["type"] in ("text", "id", "bool") and 1 < c.get("distinct", 0) <= 200 and c not in out:
                out.append(c)
    return out


def suggestions(profile: dict[str, Any]) -> list[dict[str, Any]]:
    amount = _first(profile, "amount", ("number", "money"))
    cost = _first(profile, "cost", ("number", "money"))
    qty = _first(profile, "quantity", ("number",))
    date = _first(profile, "date", ("date",))
    order = _first(profile, "order_id", ("text", "id", "number"))
    product = _first(profile, "product", ("text", "id"))
    customer = _first(profile, "customer", ("text", "id"))
    num = _first(profile, "numerator", ("number",))
    den = _first(profile, "denominator", ("number",))
    dims = _dims(profile)
    out: list[dict[str, Any]] = []

    def add(sid: str, params: dict[str, Any], metrics: list[MetricSpec], charts: list[ChartSpec] | None = None) -> None:
        out.append({"id": sid, "params": params, "metrics": [m.model_dump() for m in metrics], "charts": [c.model_dump() for c in (charts or [])]})

    unit_amount: Unit = "currency" if amount and amount["type"] == "money" or (amount and "amount" in amount.get("roles", [])) else "number"
    if amount:
        total = MetricSpec(id="total_amount", label=amount["name"][:80], unit=unit_amount, kind="aggregate", a=AggSpec(fn="sum", field=amount["id"]),
                           compare="previous_period" if date else "none")
        add("total_amount", {"field": amount["name"]}, [total])
        if date:
            add("amount_by_period", {"field": amount["name"], "date": date["name"]}, [total],
                [ChartSpec(id="amount_by_period", type="line", metric="total_amount", dimension="__period__")])
        if order:
            add("avg_basket", {"field": amount["name"], "order": order["name"]}, [
                MetricSpec(id="avg_basket", label="avg_basket", unit=unit_amount, kind="ratio",
                           a=AggSpec(fn="sum", field=amount["id"]), b=AggSpec(fn="count_distinct", field=order["id"]))])
        if product:
            add("top_products", {"field": amount["name"], "dim": product["name"]}, [total],
                [ChartSpec(id="top_products", type="hbar", metric="total_amount", dimension=product["id"], top_n=5)])
        for d in dims[:2]:
            if product and d["id"] == product["id"]:
                continue
            add(f"by_{d['id']}", {"field": amount["name"], "dim": d["name"]}, [total],
                [ChartSpec(id=f"by_{d['id']}", type="bar", metric="total_amount", dimension=d["id"], top_n=10)])
        if cost:
            add("margin", {"field": amount["name"], "cost": cost["name"]}, [
                MetricSpec(id="margin", label="margin", unit=unit_amount, kind="difference", a=AggSpec(fn="sum", field=amount["id"]), b=AggSpec(fn="sum", field=cost["id"])),
                MetricSpec(id="margin_rate", label="margin_rate", unit="percent", kind="difference_ratio", a=AggSpec(fn="sum", field=amount["id"]), b=AggSpec(fn="sum", field=cost["id"])),
            ])
    if order:
        add("order_count", {"order": order["name"]}, [MetricSpec(id="order_count", label="order_count", unit="count", kind="aggregate", a=AggSpec(fn="count_distinct", field=order["id"]))])
    if customer:
        add("customer_count", {"field": customer["name"]}, [MetricSpec(id="customer_count", label="customer_count", unit="count", kind="aggregate", a=AggSpec(fn="count_distinct", field=customer["id"]))])
    if qty:
        add("total_quantity", {"field": qty["name"]}, [MetricSpec(id="total_quantity", label=qty["name"][:80], unit="number", kind="aggregate", a=AggSpec(fn="sum", field=qty["id"]))])
    if num and den:
        add("weighted_rate", {"num": num["name"], "den": den["name"]}, [
            MetricSpec(id="weighted_rate", label="weighted_rate", unit="percent", kind="percent_of", a=AggSpec(fn="sum", field=num["id"]), b=AggSpec(fn="sum", field=den["id"]))])
    if not amount:
        numeric = [c for c in profile["columns"] if c["type"] in ("number", "money") and not ({"numerator", "denominator", "quantity"} & set(c.get("roles", [])))]
        for c in numeric[:2]:
            mid = f"sum_{c['id']}"
            add(mid, {"field": c["name"]}, [MetricSpec(id=mid, label=c["name"][:80], unit="currency" if c["type"] == "money" else "number", kind="aggregate", a=AggSpec(fn="sum", field=c["id"]))])
        label_col = next((c for c in profile["columns"] if c["type"] == "text" and 1 < c.get("distinct", 0) <= 200), None)
        if numeric and label_col:
            mid = f"sum_{numeric[0]['id']}"
            add(f"by_{label_col['id']}", {"field": numeric[0]["name"], "dim": label_col["name"]},
                [MetricSpec(id=mid, label=numeric[0]["name"][:80], unit="number", kind="aggregate", a=AggSpec(fn="sum", field=numeric[0]["id"]))],
                [ChartSpec(id=f"by_{label_col['id']}", type="hbar", metric=mid, dimension=label_col["id"], top_n=10)])
    add("row_count", {}, [MetricSpec(id="row_count", label="row_count", unit="count", kind="aggregate", a=AggSpec(fn="count_rows"))])
    return out


def plan_from_suggestions(profile: dict[str, Any], ids: list[str], labels: dict[str, str]) -> AnalysisPlan:
    available = {s["id"]: s for s in suggestions(profile)}
    metrics: dict[str, MetricSpec] = {}
    charts: dict[str, ChartSpec] = {}
    for sid in ids:
        s = available.get(sid)
        if s is None:
            continue
        for m in s["metrics"]:
            spec = MetricSpec.model_validate(m)
            if spec.label in labels:
                spec.label = labels[spec.label][:80]
            metrics.setdefault(spec.id, spec)
        for c in s["charts"]:
            charts.setdefault(c["id"], ChartSpec.model_validate(c))
    if not metrics:
        raise ValueError("aucune suggestion valide")
    date = _first(profile, "date", ("date",))
    uses_period = any(c.dimension == "__period__" for c in charts.values()) or any(m.compare == "previous_period" for m in metrics.values())
    out = LLMOutput(
        understood=True,
        metrics=list(metrics.values())[:8],
        charts=list(charts.values())[:4],
        period_field=date["id"] if date and (uses_period or date) else None,
        filter_dimensions=[d["id"] for d in _dims(profile)[:3]],
    )
    return compile_output(out)


def default_filter_dimensions(profile: dict[str, Any]) -> list[str]:
    return [d["id"] for d in _dims(profile)[:3]]
