"""Règles de calcul centrales (docs/KPI_RULES.md). Valeurs attendues calculées à la main."""

from __future__ import annotations

from decimal import Decimal

import pytest

from kpibridge.errors import UserError
from kpibridge.joins import analyse_join, create_join

from .conftest import agg, card, metric, value

ORDERS = (
    "order,date,product,amount,currency,status\n"
    "O1,2026-01-10,P1,100.00,EUR,paid\n"
    "O1,2026-01-10,P2,50.00,EUR,paid\n"
    "O1,2026-01-10,P3,25.00,EUR,paid\n"
    "O2,2026-01-20,P1,30.00,EUR,paid\n"
    "O3,2026-03-05,P2,45.00,EUR,cancelled\n"
)


def test_average_basket_uses_distinct_orders_not_rows(env):
    tid = env.ingest_bytes("o.csv", ORDERS.encode())[0]
    o, a = env.col(tid, "order"), env.col(tid, "amount")
    spec = {"metrics": [metric("basket", "currency", agg("sum", a), agg("count_distinct", o), kind="ratio")]}
    dash = env.dashboard(tid, spec, answers={"cancelled_rows": "include"})
    # 250 / 3 commandes (et non 250 / 5 lignes = 50)
    assert value(dash, "basket") == [("EUR", "83.33333333333333333333333333")] or value(dash, "basket")[0][1].startswith("83.333333")
    # avec exclusion des annulations : 205 / 2 = 102.5
    dash = env.dashboard(tid, spec, answers={"cancelled_rows": "exclude"})
    assert Decimal(value(dash, "basket")[0][1]) == Decimal("102.5")


def test_weighted_rate_not_average_of_rates(env):
    data = "store,visits,conversions,rate\nA,1000,10,1\nB,10,5,50\n"
    tid = env.ingest_bytes("r.csv", data.encode())[0]
    v, c, r = env.col(tid, "visits"), env.col(tid, "conversions"), env.col(tid, "rate")
    dash = env.dashboard(tid, {"metrics": [metric("rate", "percent", agg("sum", c), agg("sum", v), kind="percent_of")]})
    # (10 + 5) / (1000 + 10) × 100 = 1.4851…  (la moyenne des taux donnerait 25.5)
    assert value(dash, "rate")[0][1].startswith("1.485148514")
    # moyenne brute d'une colonne de taux : refusée sans règle explicite
    plan = env.plans.builder(env.pid, tid, {"metrics": [metric("avg_rate", "percent", agg("avg", r))]}, "fr")
    assert plan["status"] == "ready"  # 'rate' est numérique, pas pourcentage typé
    data2 = "store,rate\nA,1%\nB,50%\n"
    tid2 = env.ingest_bytes("r2.csv", data2.encode())[0]
    plan = env.plans.builder(env.pid, tid2, {"metrics": [metric("avg_rate", "percent", agg("avg", env.col(tid2, "rate")))]}, "fr")
    assert plan["status"] == "invalid"
    assert any(i["code"] == "rate_average_needs_rule" for i in plan["issues"])
    plan = env.plans.builder(env.pid, tid2, {"metrics": [metric("s", "percent", agg("sum", env.col(tid2, "rate")))]}, "fr")
    assert any(i["code"] == "sum_of_rates" for i in plan["issues"])


def test_missing_costs_zero_denominator_missing_month_mixed_currencies(env):
    data = (
        "date,amount,cost,qty,currency\n"
        "2026-01-15,100,,0,EUR\n"
        "2026-03-15,50,,0,USD\n"
        "2026-03-20,25,,0,EUR\n"
    )
    tid = env.ingest_bytes("m.csv", data.encode())[0]
    d, a, cost, q, cur = (env.col(tid, n) for n in ("date", "amount", "cost", "qty", "currency"))
    # pas de suggestion de marge si les coûts sont vides ? la colonne existe mais n'a aucune valeur
    spec = {
        "metrics": [
            metric("sales", "currency", agg("sum", a), compare="previous_period"),
            metric("cost", "currency", agg("sum", cost)),
            metric("unit", "number", agg("sum", a), agg("sum", q), kind="ratio"),
        ],
        "charts": [{"id": "by_month", "type": "line", "metric": "sales", "dimension": "__period__"}],
        "period_field": d,
    }
    dash = env.dashboard(tid, spec)
    # devises mélangées : résultats séparés, aucune conversion
    assert sorted(value(dash, "sales")) == [("EUR", "125"), ("USD", "50")]
    # coûts absents : indisponible, jamais 0
    assert card(dash, "cost")["status"] == "unavailable"
    assert card(dash, "cost")["reason"] == "no_values"
    # dénominateur nul : indisponible avec motif
    vals = {v["currency"]: v for v in card(dash, "unit")["values"]}
    assert vals["EUR"]["value"] is None and vals["EUR"]["reason"] == "division_by_zero"
    # mois absent (février) : trou explicite, pas de zéro
    eur = next(s for s in dash["charts"][0]["series"] if s["currency"] == "EUR")
    feb = next(p for p in eur["points"] if p["x"] == "2026-02-01")
    assert feb["y"] is None
    # variation : mars vs février — février sans données -> pas de variation inventée
    var = card(dash, "sales")["variation"]
    assert var["status"] == "unavailable" and var["reason"] == "no_comparable_period"


def test_no_comparable_period_when_previous_outside_data(env):
    data = "date,amount\n2026-05-02,10\n2026-05-20,20\n"
    tid = env.ingest_bytes("p.csv", data.encode())[0]
    spec = {"metrics": [metric("s", "number", agg("sum", env.col(tid, "amount")), compare="previous_period")], "period_field": env.col(tid, "date")}
    dash = env.dashboard(tid, spec)
    assert card(dash, "s")["variation"]["reason"] == "no_comparable_period"


def test_variation_with_equal_previous_window(env):
    data = "date,amount\n2026-01-10,100\n2026-02-10,150\n"
    tid = env.ingest_bytes("v.csv", data.encode())[0]
    spec = {"metrics": [metric("s", "number", agg("sum", env.col(tid, "amount")), compare="previous_period")], "period_field": env.col(tid, "date")}
    dash = env.dashboard(tid, spec)
    v = card(dash, "s")["variation"]
    assert v["status"] == "ok" and v["rule"] == "last_month_vs_previous"
    assert Decimal(v["values"][0]["rate"]) == Decimal("50")  # (150 − 100) / 100 × 100


def test_order_level_amount_not_summed_per_line(env):
    data = "order,product,order_total\nO1,P1,100\nO1,P2,100\nO2,P1,40\n"
    tid = env.ingest_bytes("g.csv", data.encode())[0]
    o, t = env.col(tid, "order"), env.col(tid, "order_total")
    plan = env.builder_plan(tid, {"metrics": [metric("ca", "number", agg("sum", t))]})
    q = next(q for q in plan["questions"] if q["kind"] == "order_level_amount")
    plan = env.plans.answer(plan["id"], {q["key"]: "once_per_order"})
    env.plans.validate(plan["id"])
    dash = env.plans.dashboard(plan["id"], None)
    assert value(dash, "ca") == [(None, "140")]  # et non 240
    # répartir ce montant par produit compterait deux fois la commande O1 : refusé
    plan2 = env.builder_plan(tid, {"metrics": [metric("ca", "number", agg("sum", t, per=o))],
                                   "charts": [{"id": "p", "type": "bar", "metric": "ca", "dimension": env.col(tid, "product")}]}, answers={})
    env.plans.validate(plan2["id"])
    dash2 = env.plans.dashboard(plan2["id"], None)
    assert dash2["charts"][0]["status"] == "unavailable"
    assert dash2["charts"][0]["reason"] == "grain_dimension_conflict"


def test_global_filter_applies_to_cards_charts_and_table(env):
    tid = env.ingest_bytes("o.csv", ORDERS.encode())[0]
    a, p = env.col(tid, "amount"), env.col(tid, "product")
    spec = {"metrics": [metric("s", "number", agg("sum", a))], "charts": [{"id": "c", "type": "bar", "metric": "s", "dimension": p}]}
    plan = env.builder_plan(tid, spec, answers={"cancelled_rows": "include"})
    env.plans.validate(plan["id"])
    dims = env.plans.get(plan["id"])["plan"]["filter_dimensions"]
    assert p in dims
    dash = env.plans.dashboard(plan["id"], {"dims": {p: ["P1"]}})
    assert value(dash, "s") == [("EUR", "130.00")]
    assert [pt["x"] for pt in dash["charts"][0]["series"][0]["points"]] == ["P1"]
    assert dash["detail"]["total"] == 2
    eng, _, _ = env.plans.engine(plan["id"])
    from kpibridge.engine.compute import UserFilters

    _cols, _labels, rows = eng.all_rows(UserFilters.from_dict({"dims": {p: ["P1"]}}))
    assert len(rows) == 2  # l'export CSV suit le même périmètre


def test_full_scope_never_sampled(env):
    rows = "".join(f"{i},1.01\n" for i in range(50_000))
    tid = env.ingest_bytes("big.csv", ("id,v\n" + rows).encode())[0]
    dash = env.dashboard(tid, {"metrics": [metric("s", "number", agg("sum", env.col(tid, "v")))]})
    assert value(dash, "s") == [(None, "50500.00")]


def test_join_blocked_when_duplicating_amounts(env):
    sales = env.ingest_bytes("s.csv", b"product,amount\nP1,10\nP2,20\nP1,5\n")[0]
    ref_dup = env.ingest_bytes("r.csv", b"product,family\nP1,A\nP1,B\nP2,C\n")[0]
    rep = analyse_join(env.svc, sales, env.col(sales, "product"), ref_dup, env.col(ref_dup, "product"))
    assert rep["allowed"] is False and rep["reason"] == "duplicate_keys_would_multiply_rows"
    with pytest.raises(UserError):
        create_join(env.svc, env.pid, sales, env.col(sales, "product"), ref_dup, env.col(ref_dup, "product"), [env.col(ref_dup, "family")])
    ref = env.ingest_bytes("r2.csv", b"product,family\nP1,A\nP2,C\n")[0]
    res = create_join(env.svc, env.pid, sales, env.col(sales, "product"), ref, env.col(ref, "product"), [env.col(ref, "family")])
    tid = res["table_id"]
    assert res["sums"][env.col(sales, "amount")]["before"] == res["sums"][env.col(sales, "amount")]["after"]
    dash = env.dashboard(tid, {"metrics": [metric("s", "number", agg("sum", env.col(tid, "amount")))],
                               "charts": [{"id": "f", "type": "bar", "metric": "s", "dimension": env.col(tid, "family")}]})
    assert value(dash, "s") == [(None, "35")]
    assert {p["x"]: p["y"] for p in dash["charts"][0]["series"][0]["points"]} == {"A": "15", "C": "20"}


def test_unknown_field_and_incompatible_ops_refused(env):
    tid = env.ingest_bytes("o.csv", ORDERS.encode())[0]
    plan = env.plans.builder(env.pid, tid, {"metrics": [metric("x", "number", agg("sum", "c99"))]}, "fr")
    assert plan["status"] == "invalid"
    with pytest.raises(UserError):
        env.plans.validate(plan["id"])
    p = env.plans.from_plan_json(env.pid, tid, {"metrics": [{"id": "x", "label": "x", "unit": "number", "expression": {"kind": "agg", "fn": "sum", "field": "c99"}}]}, "test")
    got = env.plans.get(p)
    assert got["status"] == "invalid" and any(i["code"] == "unknown_field" for i in got["issues"])
    p = env.plans.from_plan_json(env.pid, tid, {"metrics": [{"id": "x", "label": "x", "unit": "number", "expression": {"kind": "agg", "fn": "sum", "field": env.col(tid, "product")}}]}, "test")
    assert any(i["code"] == "incompatible_aggregation" for i in env.plans.get(p)["issues"])
    with pytest.raises(UserError):
        env.plans.from_plan_json(env.pid, tid, {"metrics": [{"id": "x", "label": "x", "unit": "number", "expression": {"kind": "sql", "q": "DROP TABLE data"}}]}, "test")
