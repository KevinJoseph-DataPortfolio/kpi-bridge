"""Measurements on the current machine: start-up, import of a 50,000-row CSV, computation, filters, memory, AI.

Usage: python scripts/benchmark.py [--ai]   (JSON results in docs/benchmark.json)
Values depend on the hardware; they are measured, never assumed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("KPI_DATA_DIR", tempfile.mkdtemp(prefix="kpi_bench_"))


def peak_rss_mib() -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 2**20
    import resource

    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1024 if sys.platform != "darwin" else r / 2**20


def make_csv(path: Path, n: int = 50_000) -> None:
    rnd = random.Random(7)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["order", "date", "product", "region", "qty", "amount"])
        for i in range(n):
            w.writerow([f"O{i // 3:06d}", f"2025-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}", f"P{rnd.randint(1, 40)}",
                        rnd.choice(["Nord", "Sud", "Est", "Ouest"]), rnd.randint(1, 9), f"{rnd.randint(100, 99999) / 100:.2f}"])


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def measure_startup() -> float:
    port = free_port()
    env = dict(os.environ, KPI_PORT=str(port), KPI_DATA_DIR=tempfile.mkdtemp(prefix="kpi_bench_start_"), PYTHONPATH=str(ROOT / "backend"))
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, "-m", "kpibridge.app"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while time.perf_counter() - t0 < 60:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1):
                    return time.perf_counter() - t0
            except OSError:
                time.sleep(0.05)
        return float("nan")
    finally:
        p.terminate()
        p.wait(10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true")
    args = ap.parse_args()
    from kpibridge import jobs
    from kpibridge.config import ensure_dirs, load_settings
    from kpibridge.db import Database, migrate
    from kpibridge.plans import Plans
    from kpibridge.services import Services

    out: dict = {"machine": {"system": platform.platform(), "python": platform.python_version(), "cpu": platform.processor()}}
    out["startup_api_s"] = round(measure_startup(), 2)
    s = load_settings()
    ensure_dirs(s)
    migrate(s)
    db = Database(s)
    svc = Services(s, db)
    plans = Plans(svc)
    src = Path(tempfile.mkdtemp()) / "bench.csv"
    make_csv(src)
    out["csv_bytes"] = src.stat().st_size
    p = svc.create_project("bench")
    t0 = time.perf_counter()
    f = svc.save_upload(p["id"], "bench.csv", iter([src.read_bytes()]))
    jid = svc.enqueue_ingest(p["id"], f["id"], "fr")
    jobs.claim_next(db)
    wd = s.tmp_dir / jid
    wd.mkdir(parents=True, exist_ok=True)
    tid = svc.run_ingest(jid, {"file_id": f["id"], "ui_lang": "fr"}, wd)["tables"][0]
    out["import_50k_rows_s"] = round(time.perf_counter() - t0, 2)
    cols = {c["name"]: c["id"] for c in svc.current_version(tid)["profile"]["columns"]}
    spec = {"metrics": [
        {"id": "sales", "label": "Ventes", "unit": "number", "kind": "aggregate", "a": {"fn": "sum", "field": cols["amount"]}, "compare": "previous_period"},
        {"id": "basket", "label": "Panier", "unit": "number", "kind": "ratio", "a": {"fn": "sum", "field": cols["amount"]}, "b": {"fn": "count_distinct", "field": cols["order"]}},
        {"id": "orders", "label": "Commandes", "unit": "count", "kind": "aggregate", "a": {"fn": "count_distinct", "field": cols["order"]}}],
        "charts": [{"id": "m", "type": "line", "metric": "sales", "dimension": "__period__"},
                   {"id": "p", "type": "hbar", "metric": "sales", "dimension": cols["product"], "top_n": 5}],
        "period_field": cols["date"], "filter_dimensions": [cols["region"], cols["product"]]}
    plan = plans.builder(p["id"], tid, spec, "fr")
    if plan["status"] == "needs_clarification":
        plan = plans.answer(plan["id"], {q["key"]: q["options"][0] for q in plan["questions"]})
    plans.validate(plan["id"])
    t0 = time.perf_counter()
    plans.dashboard(plan["id"], None)
    out["first_dashboard_s"] = round(time.perf_counter() - t0, 3)
    lat = []
    for region in ("Nord", "Sud", "Est", "Ouest"):
        for month in (1, 4, 7, 10):
            filters = {"dims": {cols["region"]: [region]}, "period_from": f"2025-{month:02d}-01", "period_to": f"2025-{month + 2:02d}-28"}
            t0 = time.perf_counter()
            plans.dashboard(plan["id"], filters)
            lat.append(time.perf_counter() - t0)
    out["filter_latency_s"] = {"median": round(statistics.median(lat), 3), "max": round(max(lat), 3), "n": len(lat), "cache": "miss (filtres distincts)"}
    out["peak_rss_mib_benchmark_process"] = round(peak_rss_mib(), 1)
    if args.ai:
        from kpibridge.plan.interpret import interpret
        from kpibridge.plan.llm import OllamaProvider

        t0 = time.perf_counter()
        r = interpret(OllamaProvider(s), "Ventes mensuelles, panier moyen et top 5 produits", "fr", svc.current_version(tid)["profile"])
        out["ai_interpretation_s"] = round(time.perf_counter() - t0, 1)
        out["ai_valid"] = r["plan"] is not None
    print(json.dumps(out, indent=2, ensure_ascii=False))
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "benchmark.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
