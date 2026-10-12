"""Smoke test of a running instance: session → example → free-text request (real AI or substitute) → clarifications →
validation → dashboard → filter → exports. Usage: python scripts/smoke_api.py --port 8765 [--lang fr]

The session link is created locally (same KPI_DATA_DIR as the instance) and is never displayed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("KPI_PORT", "8765")))
    ap.add_argument("--lang", default="fr")
    ap.add_argument("--text", default="Suivre les ventes mensuelles, le panier moyen et les cinq meilleurs produits.")
    a = ap.parse_args()
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), KPI_PORT=str(a.port))
    url = subprocess.run([sys.executable, "-m", "kpibridge.session", "new"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    token = url.split("#token=", 1)[1]
    base = f"http://127.0.0.1:{a.port}/api/v1"
    h = {"X-KPI-CSRF": "1"}
    c = httpx.Client(timeout=300, trust_env=False)
    c.post(f"{base}/session/bootstrap", json={"token": token}, headers=h).raise_for_status()
    pid = c.post(f"{base}/projects", json={"name": "smoke"}, headers=h).json()["id"]
    job = c.post(f"{base}/projects/{pid}/examples/ventes_demo.csv?ui_lang={a.lang}", headers=h).json()["job_id"]
    while (j := c.get(f"{base}/jobs/{job}").json())["status"] in ("queued", "running"):
        time.sleep(0.3)
    assert j["status"] == "done", j
    tid = c.get(f"{base}/projects/{pid}").json()["tables"][0]["id"]
    t0 = time.time()
    plan_id = c.post(f"{base}/projects/{pid}/requests", json={"table_id": tid, "text": a.text, "ui_lang": a.lang}, headers=h).json()["plan_id"]
    while (p := c.get(f"{base}/projects/{pid}/plans/{plan_id}").json())["status"] == "interpreting":
        time.sleep(0.5)
    ai_s = time.time() - t0
    print(json.dumps({"plan_status": p["status"], "origin": p["origin"], "ai_seconds": round(ai_s, 1)}))
    for _ in range(3):
        if p["status"] != "needs_clarification":
            break
        p = c.post(f"{base}/projects/{pid}/plans/{plan_id}/answers", json={"answers": {q["key"]: q["options"][0] for q in p["questions"]}}, headers=h).json()
    assert p["status"] == "ready", p["status"]
    c.post(f"{base}/projects/{pid}/plans/{plan_id}/validate", headers=h).raise_for_status()
    d = c.post(f"{base}/projects/{pid}/plans/{plan_id}/dashboard", json={"filters": None}, headers=h).json()
    print(json.dumps({"cards": [(x["label"], [v["value"] for v in x["values"]]) for x in d["cards"]]}, ensure_ascii=False))
    dim = d["filters"]["dims"][0]
    f = {"dims": {dim["field"]: [dim["values"][0]["value"]]}}
    d2 = c.post(f"{base}/projects/{pid}/plans/{plan_id}/dashboard", json={"filters": f}, headers=h).json()
    assert d2["rows_in_scope"] < d["rows_in_scope"]
    for fmt in ("pdf", "png", "csv"):
        r = c.post(f"{base}/projects/{pid}/plans/{plan_id}/export", json={"format": fmt, "filters": f, "lang": a.lang}, headers=h)
        r.raise_for_status()
        print(fmt, len(r.content), "octets")
    c.delete(f"{base}/projects/{pid}", headers=h).raise_for_status()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
