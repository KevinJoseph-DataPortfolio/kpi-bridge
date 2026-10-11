"""Fixtures communes. Chaque test dispose d'un répertoire de données isolé."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kpibridge import jobs
from kpibridge.config import Settings, ensure_dirs, load_settings
from kpibridge.db import Database, migrate
from kpibridge.plans import Plans
from kpibridge.services import Services


class Env:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.svc = Services(settings, self.db)
        self.plans = Plans(self.svc)
        self.project = self.svc.create_project("test")

    @property
    def pid(self) -> str:
        return self.project["id"]

    def ingest_bytes(self, name: str, data: bytes, options: dict | None = None) -> list[str]:
        f = self.svc.save_upload(self.pid, name, iter([data]), options)
        return self.run_ingest(f["id"])

    def run_ingest(self, fid: str) -> list[str]:
        jid = self.svc.enqueue_ingest(self.pid, fid, "fr")
        job = jobs.claim_next(self.db)
        assert job and job["id"] == jid
        wd = self.settings.tmp_dir / jid
        wd.mkdir(parents=True, exist_ok=True)
        payload = json.loads(self.db.one("SELECT payload FROM jobs WHERE id=?", (jid,))["payload"])
        res = self.svc.run_ingest(jid, payload, wd)
        jobs.finish(self.db, jid, res)
        return res["tables"]

    def profile(self, tid: str) -> dict[str, Any]:
        return self.svc.current_version(tid)["profile"]

    def col(self, tid: str, name: str) -> str:
        for c in self.profile(tid)["columns"]:
            if c["name"] == name:
                return c["id"]
        raise KeyError(name)

    def builder_plan(self, tid: str, spec: dict[str, Any], answers: dict[str, str] | None = None) -> dict[str, Any]:
        plan = self.plans.builder(self.pid, tid, spec, "fr")
        if plan["status"] == "needs_clarification" and answers is not None:
            plan = self.plans.answer(plan["id"], {q["key"]: answers.get(q["kind"], q["options"][0]) for q in plan["questions"]})
        return plan

    def dashboard(self, tid: str, spec: dict[str, Any], filters: dict | None = None, answers: dict[str, str] | None = None) -> dict[str, Any]:
        plan = self.builder_plan(tid, spec, answers if answers is not None else {})
        assert plan["status"] == "ready", (plan["status"], plan["issues"], plan["questions"])
        self.plans.validate(plan["id"])
        return self.plans.dashboard(plan["id"], filters)


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("KPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KPI_LLM_PROVIDER", "fake")
    s = load_settings()
    ensure_dirs(s)
    migrate(s)
    return s


@pytest.fixture()
def env(settings: Settings) -> Env:
    return Env(settings)


def agg(fn: str, field: str | None = None, per: str | None = None) -> dict[str, Any]:
    return {"fn": fn, "field": field, "per": per}


def metric(mid: str, unit: str, a: dict, b: dict | None = None, kind: str = "aggregate", compare: str = "none", filters: list | None = None) -> dict[str, Any]:
    return {"id": mid, "label": mid, "unit": unit, "kind": kind, "a": a, "b": b, "compare": compare, "filters": filters or []}


def value(dash: dict[str, Any], mid: str) -> list[tuple[str | None, str | None]]:
    card = next(c for c in dash["cards"] if c["id"] == mid)
    return [(v["currency"], v["value"]) for v in card["values"]]


def card(dash: dict[str, Any], mid: str) -> dict[str, Any]:
    return next(c for c in dash["cards"] if c["id"] == mid)
