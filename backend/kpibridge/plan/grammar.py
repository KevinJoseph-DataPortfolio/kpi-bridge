"""Closed computation grammar.

The language model (or the guided builder, or a recipe) produces a JSON `AnalysisPlan`. The plan is validated
strictly (Pydantic schema, `extra="forbid"`), then semantically against the real fields (`validate.py`).
It contains NO code: only field references, operators and aggregations from a closed list.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

FIELD_PATTERN = r"^c\d{1,3}(_cur)?$"
ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"
PERIOD = "__period__"

AggFn = Literal["sum", "count_rows", "count", "count_distinct", "avg", "median", "min", "max"]
FilterOp = Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "is_null", "not_null", "between"]
Unit = Literal["currency", "count", "number", "percent", "ratio"]
ChartType = Literal["line", "bar", "hbar"]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Filter(Strict):
    field: str = Field(pattern=FIELD_PATTERN)
    op: FilterOp
    values: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("values")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        return [str(x)[:200] for x in v]


class Agg(Strict):
    kind: Literal["agg"] = "agg"
    fn: AggFn
    field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    per: str | None = Field(default=None, pattern=FIELD_PATTERN, description="clé de grain : une valeur par clé avant agrégation")
    filters: list[Filter] = Field(default_factory=list, max_length=8)


class Const(Strict):
    kind: Literal["const"] = "const"
    value: str = Field(pattern=r"^-?\d{1,18}(\.\d{1,12})?$")


class BinOp(Strict):
    kind: Literal["op"] = "op"
    op: Literal["add", "sub", "mul", "div"]
    left: Expr
    right: Expr


Expr = Annotated[Union[Agg, Const, BinOp], Field(discriminator="kind")]  # noqa: UP007
BinOp.model_rebuild()


class Metric(Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    unit: Unit
    expression: Expr
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    compare: Literal["none", "previous_period"] = "none"
    rate_rule: Literal["weighted", "row_average"] | None = None
    decimals: int | None = Field(default=None, ge=0, le=6)


class Chart(Strict):
    id: str = Field(pattern=ID_PATTERN)
    type: ChartType
    metric: str = Field(pattern=ID_PATTERN)
    dimension: str = Field(pattern=r"^(c\d{1,3}(_cur)?|__period__)$")
    top_n: int | None = Field(default=None, ge=1, le=20)
    sort: Literal["desc", "asc", "natural"] = "desc"
    title: str = Field(default="", max_length=80)


class AnalysisPlan(Strict):
    version: Literal[1] = 1
    metrics: list[Metric] = Field(min_length=1, max_length=8)
    charts: list[Chart] = Field(default_factory=list, max_length=4)
    period_field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    time_grain: TimeGrain = "month"
    filter_dimensions: list[str] = Field(default_factory=list, max_length=4)
    global_filters: list[Filter] = Field(default_factory=list, max_length=8)
    currency_field: str | None = Field(default=None, pattern=FIELD_PATTERN)
    table_columns: list[str] | None = Field(default=None, max_length=60)

    @field_validator("filter_dimensions")
    @classmethod
    def _dims(cls, v: list[str]) -> list[str]:
        import re

        for d in v:
            if not re.match(FIELD_PATTERN, d):
                raise ValueError("dimension invalide")
        return list(dict.fromkeys(v))


def iter_aggs(expr: Expr) -> list[Agg]:
    if isinstance(expr, Agg):
        return [expr]
    if isinstance(expr, BinOp):
        return iter_aggs(expr.left) + iter_aggs(expr.right)
    return []


def expr_depth(expr: Expr) -> int:
    if isinstance(expr, BinOp):
        return 1 + max(expr_depth(expr.left), expr_depth(expr.right))
    return 1


def plan_json_schema() -> dict:
    return AnalysisPlan.model_json_schema()
