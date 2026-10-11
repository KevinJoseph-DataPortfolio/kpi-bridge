"""Gestionnaires de tâches exécutés dans le sous-processus du worker."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .plans import Plans
from .services import Services


def _ingest(settings: Settings, db: Database, job_id: str, payload: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    return Services(settings, db).run_ingest(job_id, payload, work_dir)


def _interpret(settings: Settings, db: Database, job_id: str, payload: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    return Plans(Services(settings, db)).run_interpret(job_id, payload)


def _sleep_test(settings: Settings, db: Database, job_id: str, payload: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Tâche de test (annulation / crash / délai) : n'est enregistrée que si KPI_ENABLE_TEST_JOBS=1."""
    import os
    import time

    from . import jobs

    if payload.get("crash"):
        os._exit(3)
    for i in range(int(payload.get("seconds", 5) * 10)):
        jobs.progress(db, job_id, "sleep", i / (payload.get("seconds", 5) * 10))
        time.sleep(0.1)
    return {"slept": payload.get("seconds", 5)}


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {"ingest": _ingest, "interpret": _interpret}

import os as _os  # noqa: E402

if _os.environ.get("KPI_ENABLE_TEST_JOBS") == "1":
    HANDLERS["sleep_test"] = _sleep_test
