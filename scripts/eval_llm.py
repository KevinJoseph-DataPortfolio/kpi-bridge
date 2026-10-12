"""Évaluation du VRAI modèle local (Ollama) dans les six langues, sur le profil réel de l'exemple synthétique.

Ce n'est pas un test CI (le modèle peut varier) : c'est une mesure reproductible, consignée dans docs/TEST_REPORT.md.
Usage : python scripts/eval_llm.py [--out docs/llm_eval.json]
Prérequis : Ollama démarré avec le modèle configuré (KPI_OLLAMA_MODEL, défaut qwen3:4b).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("KPI_DATA_DIR", tempfile.mkdtemp(prefix="kpi_eval_"))

from kpibridge import jobs  # noqa: E402
from kpibridge.config import ensure_dirs, load_settings  # noqa: E402
from kpibridge.db import Database, migrate  # noqa: E402
from kpibridge.errors import UserError  # noqa: E402
from kpibridge.plan.grammar import iter_aggs  # noqa: E402
from kpibridge.plan.interpret import interpret  # noqa: E402
from kpibridge.plan.llm import OllamaProvider  # noqa: E402
from kpibridge.services import Services  # noqa: E402

REQUESTS = {
    "fr": "Suivre les ventes mensuelles, le panier moyen et les cinq meilleurs produits, ainsi que la marge.",
    "en": "Track monthly sales, the average basket and the top five products, plus the margin.",
    "ru": "Отслеживать продажи по месяцам, средний чек и пять лучших товаров, а также маржу.",
    "zh-Hans": "跟踪每月销售额、平均订单金额和前五名产品，以及毛利。",
    "de": "Monatliche Umsätze, den durchschnittlichen Warenkorb und die fünf besten Produkte sowie die Marge verfolgen.",
    "es": "Seguir las ventas mensuales, el ticket medio y los cinco mejores productos, además del margen.",
}


def checks(plan, cols: dict[str, str]) -> dict[str, bool]:
    aggs = [(m, a) for m in plan.metrics for a in iter_aggs(m.expression)]
    amount, order, product, cost = cols["Montant"], cols["N° commande"], cols["Produit"], cols["Coût"]
    has_sum_amount = any(a.fn == "sum" and a.field == amount for _m, a in aggs)
    basket = any(
        m.expression.kind == "op" and m.expression.op == "div"  # type: ignore[union-attr]
        and any(a.fn == "count_distinct" and a.field == order for a in iter_aggs(m.expression))
        for m in plan.metrics
    )
    no_row_count_basket = not any(
        m.expression.kind == "op" and any(a.fn == "count_rows" for a in iter_aggs(m.expression)) for m in plan.metrics  # type: ignore[union-attr]
    )
    top = any(c.dimension == product and c.top_n == 5 for c in plan.charts)
    monthly = plan.period_field == cols["Date"] and any(c.dimension == "__period__" for c in plan.charts)
    margin = any(
        m.expression.kind == "op" and {a.field for a in iter_aggs(m.expression)} >= {amount, cost} and m.expression.op in ("sub", "mul")  # type: ignore[union-attr]
        for m in plan.metrics
    )
    return {"sum_amount": has_sum_amount, "basket_distinct_orders": basket, "no_row_count_ratio": no_row_count_basket,
            "top5_products_chart": top, "monthly_chart": monthly, "margin_difference": margin}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "llm_eval.json"))
    args = ap.parse_args()
    s = load_settings()
    ensure_dirs(s)
    migrate(s)
    db = Database(s)
    svc = Services(s, db)
    p = svc.create_project("eval")
    f = svc.add_example(p["id"], "ventes_demo.csv")
    jid = svc.enqueue_ingest(p["id"], f["id"], "fr")
    jobs.claim_next(db)
    wd = s.tmp_dir / jid
    wd.mkdir(parents=True, exist_ok=True)
    tid = svc.run_ingest(jid, {"file_id": f["id"], "ui_lang": "fr"}, wd)["tables"][0]
    profile = svc.current_version(tid)["profile"]
    cols = {c["name"]: c["id"] for c in profile["columns"]}
    provider = OllamaProvider(s)
    status = provider.status()
    results = {"model": s.ollama_model, "ollama": status, "runs": []}
    for lang, text in REQUESTS.items():
        t0 = time.time()
        try:
            r = interpret(provider, text, lang, profile)
            entry = {"lang": lang, "valid": r["plan"] is not None, "attempts": len(r["attempts"]), "seconds": round(time.time() - t0, 1),
                     "labels": [m.label for m in r["plan"].metrics] if r["plan"] else [], "unavailable": r["unavailable"],
                     "checks": checks(r["plan"], cols) if r["plan"] else {}}
        except UserError as exc:
            entry = {"lang": lang, "valid": False, "error": exc.code, "seconds": round(time.time() - t0, 1), "detail": exc.params}
        results["runs"].append(entry)
        print(json.dumps(entry, ensure_ascii=False))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results["runs"] if r["valid"])
    print(f"plans valides : {ok}/{len(REQUESTS)}")


if __name__ == "__main__":
    main()
