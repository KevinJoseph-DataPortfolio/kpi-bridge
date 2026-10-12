"""Output format requested from the model (non-recursive, simpler than the grammar) and its compilation to `AnalysisPlan`.

The guided builder uses exactly the same format: both paths go through the same compilation and the same
validation, then through the same deterministic engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .grammar import (
    FIELD_PATTERN,
    ID_PATTERN,
    Agg,
    AnalysisPlan,
    BinOp,
    Chart,
    ChartType,
    Filter,
    Metric,
    TimeGrain,
    Unit,
)


class S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AggSpec(S):
    fn: Literal["sum", "count_rows", "count", "count_distinct", "avg", "median", "min", "max"]
    field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    per: str | None = Field(default=None, pattern=FIELD_PATTERN)


class FilterSpec(S):
    field: str = Field(pattern=FIELD_PATTERN)
    op: Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "is_null", "not_null", "between"]
    values: list[str] = Field(default_factory=list, max_length=50)


class MetricSpec(S):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    unit: Unit
    kind: Literal["aggregate", "ratio", "difference", "difference_ratio", "percent_of"]
    a: AggSpec
    b: AggSpec | None = None
    filters: list[FilterSpec] = Field(default_factory=list, max_length=6)
    compare: Literal["none", "previous_period"] = "none"
    rate_rule: Literal["weighted", "row_average"] | None = None


class ChartSpec(S):
    id: str = Field(pattern=ID_PATTERN)
    type: ChartType
    metric: str = Field(pattern=ID_PATTERN)
    dimension: str = Field(pattern=r"^(c\d{1,3}(_cur)?|__period__)$")
    top_n: int | None = Field(default=None, ge=1, le=20)


class Unavailable(S):
    what: str = Field(max_length=120)
    missing: list[str] = Field(default_factory=list, max_length=6)


class LLMOutput(S):
    understood: bool
    metrics: list[MetricSpec] = Field(default_factory=list, max_length=8)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=4)
    period_field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    time_grain: TimeGrain = "month"
    filter_dimensions: list[str] = Field(default_factory=list, max_length=4)
    unavailable: list[Unavailable] = Field(default_factory=list, max_length=6)


def _agg(spec: AggSpec, filters: list[Filter] | None = None) -> Agg:
    return Agg(fn=spec.fn, field=spec.field if spec.fn != "count_rows" else None, per=spec.per, filters=filters or [])


def compile_metric(m: MetricSpec) -> Metric:
    a = _agg(m.a)
    expr: Agg | BinOp
    if m.kind == "aggregate":
        expr = a
    else:
        if m.b is None:
            raise ValueError("b requis")
        b = _agg(m.b)
        if m.kind == "ratio":
            expr = BinOp(op="div", left=a, right=b)
        elif m.kind == "difference":
            expr = BinOp(op="sub", left=a, right=b)
        elif m.kind == "percent_of":
            expr = BinOp(op="mul", left=BinOp(op="div", left=a, right=b), right={"kind": "const", "value": "100"})  # type: ignore[arg-type]
        else:  # difference_ratio : (a - b) / a × 100
            expr = BinOp(
                op="mul",
                left=BinOp(op="div", left=BinOp(op="sub", left=a, right=b), right=_agg(m.a)),
                right={"kind": "const", "value": "100"},  # type: ignore[arg-type]
            )
    return Metric(
        id=m.id,
        label=m.label,
        unit=m.unit,
        expression=expr,
        filters=[Filter(**f.model_dump()) for f in m.filters],
        compare=m.compare,
        rate_rule=m.rate_rule,
    )


def compile_output(out: LLMOutput) -> AnalysisPlan:
    metrics = [compile_metric(m) for m in out.metrics]
    charts = [Chart(id=c.id, type=c.type, metric=c.metric, dimension=c.dimension, top_n=c.top_n) for c in out.charts]
    return AnalysisPlan(
        metrics=metrics,
        charts=charts,
        period_field=out.period_field,
        time_grain=out.time_grain,
        filter_dimensions=out.filter_dimensions,
    )
