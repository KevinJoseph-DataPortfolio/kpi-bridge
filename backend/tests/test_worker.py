"""Real worker (subprocess): cancellation, crash, bounded retry, timeout, temporary files cleaned."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kpibridge import jobs
from kpibridge.db import Database

ROOT = Path(__file__).resolve().parents[1]


def start_worker(data_dir: Path, extra_env: dict | None = None) -> subprocess.Popen:
    env = {**os.environ, "KPI_DATA_DIR": str(data_dir), "KPI_ENABLE_TEST_JOBS": "1", "PYTHONPATH": str(ROOT), **(extra_env or {})}
    return subprocess.Popen([sys.executable, "-m", "kpibridge.worker"], cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(db: Database, job_id: str, statuses: tuple[str, ...], timeout: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jobs.get(db, job_id)
        if j and j["status"] in statuses:
            return j
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting {statuses}: {jobs.get(db, job_id)}")


@pytest.fixture()
def worker_env(settings):
    db = Database(settings)
    procs: list[subprocess.Popen] = []
    yield settings, db, procs
    for p in procs:
        p.kill()
        p.wait(10)


def test_cancel_running_job(worker_env):
    settings, db, procs = worker_env
    procs.append(start_worker(settings.data_dir))
    jid = jobs.enqueue(db, "sleep_test", None, {"seconds": 30}, timeout_s=120)
    wait_for(db, jid, ("running",))
    time.sleep(1)
    assert jobs.request_cancel(db, jid)
    j = wait_for(db, jid, ("cancelled",), timeout=20)
    assert j["status"] == "cancelled"
    assert not (settings.tmp_dir / jid).exists()  # temporary files removed


def test_crash_is_retried_then_failed_and_timeout_enforced(worker_env):
    settings, db, procs = worker_env
    procs.append(start_worker(settings.data_dir))
    jid = jobs.enqueue(db, "sleep_test", None, {"crash": True}, timeout_s=60, max_attempts=2)
    j = wait_for(db, jid, ("failed",), timeout=60)
    assert j["error"]["code"] == "worker_crashed" and j["attempts"] == 2  # bounded retry: 2 attempts
    jt = jobs.enqueue(db, "sleep_test", None, {"seconds": 30}, timeout_s=2)
    j = wait_for(db, jt, ("failed",), timeout=30)
    assert j["error"]["code"] == "job_timeout"


def test_supervisor_crash_recovery_on_restart(worker_env):
    settings, db, procs = worker_env
    p = start_worker(settings.data_dir)
    procs.append(p)
    jid = jobs.enqueue(db, "sleep_test", None, {"seconds": 3}, timeout_s=60, max_attempts=2)
    wait_for(db, jid, ("running",))
    p.kill()  # hard stop of the supervisor during the job
    p.wait(10)
    (settings.tmp_dir / "orphan-file.tmp").write_text("x")
    assert jobs.get(db, jid)["status"] == "running"
    procs.append(start_worker(settings.data_dir))
    j = wait_for(db, jid, ("done",), timeout=60)  # requeued once, consistent result
    assert j["result"] == {"slept": 3}
    assert not (settings.tmp_dir / "orphan-file.tmp").exists()  # sweep on restart
