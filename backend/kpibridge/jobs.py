"""File de tâches persistante (SQLite). Pas de Redis : une tâche lourde à la fois, reprise bornée après crash."""

from __future__ import annotations

from typing import Any

from .db import Database, dumps, loads, new_id, now
from .errors import JobCancelled

ACTIVE = ("queued", "running")


def enqueue(db: Database, kind: str, project_id: str | None, payload: dict[str, Any], timeout_s: int, max_attempts: int = 2) -> str:
    job_id = new_id("job")
    t = now()
    db.run(
        "INSERT INTO jobs (id, project_id, kind, status, stage, progress, payload, attempts, max_attempts, timeout_s, created_at, updated_at)"
        " VALUES (?, ?, ?, 'queued', 'queued', 0, ?, 0, ?, ?, ?, ?)",
        (job_id, project_id, kind, dumps(payload), max_attempts, timeout_s, t, t),
    )
    return job_id


def claim_next(db: Database) -> dict[str, Any] | None:
    with db.tx() as con:
        row = con.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            return None
        t = now()
        con.execute(
            "UPDATE jobs SET status='running', stage='starting', attempts=attempts+1, started_at=?, heartbeat_at=?, updated_at=? WHERE id=?",
            (t, t, t, row["id"]),
        )
        out = dict(row)
        out["attempts"] = row["attempts"] + 1
        return out


def get(db: Database, job_id: str) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        return None
    return public(dict(row))


def public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "kind": row["kind"],
        "status": row["status"],
        "stage": row["stage"],
        "progress": round(float(row["progress"] or 0), 3),
        "result": loads(row["result"], None),
        "error": {"code": row["error_code"], "params": loads(row["error_params"], {})} if row["error_code"] else None,
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "cancel_requested": bool(row["cancel_requested"]),
    }


def progress(db: Database, job_id: str, stage: str, value: float) -> None:
    """Mise à jour de progression depuis le traitement ; lève JobCancelled si l'annulation est demandée."""
    with db.conn() as con:
        row = con.execute("SELECT status, cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["cancel_requested"] or row["status"] != "running":
            raise JobCancelled()
        con.execute(
            "UPDATE jobs SET stage=?, progress=?, heartbeat_at=?, updated_at=? WHERE id=? AND status='running'",
            (stage, max(0.0, min(1.0, value)), now(), now(), job_id),
        )


def finish(db: Database, job_id: str, result: dict[str, Any]) -> bool:
    t = now()
    n = db.run(
        "UPDATE jobs SET status='done', stage='done', progress=1, result=?, finished_at=?, updated_at=? WHERE id=? AND status='running' AND cancel_requested=0",
        (dumps(result), t, t, job_id),
    )
    return n > 0


def fail(db: Database, job_id: str, code: str, params: dict[str, Any] | None = None) -> None:
    t = now()
    db.run(
        "UPDATE jobs SET status='failed', stage='failed', error_code=?, error_params=?, finished_at=?, updated_at=? WHERE id=? AND status IN ('running','queued')",
        (code, dumps(params or {}), t, t, job_id),
    )


def request_cancel(db: Database, job_id: str) -> bool:
    t = now()
    with db.tx() as con:
        row = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["status"] not in ACTIVE:
            return False
        if row["status"] == "queued":
            con.execute("UPDATE jobs SET status='cancelled', stage='cancelled', cancel_requested=1, finished_at=?, updated_at=? WHERE id=?", (t, t, job_id))
        else:
            con.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?", (t, job_id))
    return True


def mark_cancelled(db: Database, job_id: str) -> None:
    t = now()
    db.run("UPDATE jobs SET status='cancelled', stage='cancelled', finished_at=?, updated_at=? WHERE id=? AND status='running'", (t, t, job_id))


def recover_after_crash(db: Database) -> dict[str, int]:
    """Au démarrage du worker : les tâches 'running' orphelines sont relancées (si tentatives restantes) ou échouées."""
    requeued = failed = cancelled = 0
    t = now()
    with db.tx() as con:
        for row in con.execute("SELECT id, attempts, max_attempts, cancel_requested FROM jobs WHERE status='running'").fetchall():
            if row["cancel_requested"]:
                con.execute("UPDATE jobs SET status='cancelled', stage='cancelled', finished_at=?, updated_at=? WHERE id=?", (t, t, row["id"]))
                cancelled += 1
            elif row["attempts"] < row["max_attempts"]:
                con.execute("UPDATE jobs SET status='queued', stage='requeued_after_crash', progress=0, updated_at=? WHERE id=?", (t, row["id"]))
                requeued += 1
            else:
                con.execute(
                    "UPDATE jobs SET status='failed', stage='failed', error_code='worker_crashed', finished_at=?, updated_at=? WHERE id=?",
                    (t, t, row["id"]),
                )
                failed += 1
    return {"requeued": requeued, "failed": failed, "cancelled": cancelled}
