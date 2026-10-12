"""Worker de traitement : superviseur + un sous-processus par tâche.

- une tâche lourde à la fois (par défaut) ;
- annulation et délai maximal appliqués en terminant le sous-processus ;
- limite mémoire du sous-processus (POSIX, RLIMIT_AS) ; sous Windows, le délai et l'isolation par processus s'appliquent ;
- reprise bornée après crash (tentatives max), temporaires supprimés en fin de tâche et au démarrage.

Lancement : ``python -m kpibridge.worker``
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from . import jobs
from .config import ensure_dirs, get_settings
from .db import Database, loads, migrate, now
from .errors import JobCancelled, UserError
from .storage import sweep_tmp

log = logging.getLogger("kpibridge.worker")


def _limit_memory(mib: int) -> None:
    try:
        import resource  # POSIX uniquement

        limit = mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))  # type: ignore[attr-defined]
    except (ImportError, ValueError, OSError):
        pass


def run_job_child(job_id: str) -> None:
    """Exécuté dans un sous-processus isolé."""
    settings = get_settings()
    _limit_memory(settings.limits.job_memory_mib)
    db = Database(settings)
    row = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        return
    payload: dict[str, Any] = loads(row["payload"], {})
    work_dir = settings.tmp_dir / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        from .handlers import HANDLERS

        handler = HANDLERS[row["kind"]]
        result = handler(settings, db, job_id, payload, work_dir)
        jobs.finish(db, job_id, result or {})
    except JobCancelled:
        jobs.mark_cancelled(db, job_id)
    except UserError as exc:
        jobs.fail(db, job_id, exc.code, exc.params)
    except MemoryError:
        jobs.fail(db, job_id, "out_of_memory")
    except Exception as exc:  # noqa: BLE001 — aucune trace système côté utilisateur
        log.error("tâche %s en échec : %s", job_id, type(exc).__name__)
        jobs.fail(db, job_id, "internal_error")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class Supervisor:
    def __init__(self) -> None:
        self.settings = get_settings()
        ensure_dirs(self.settings)
        migrate(self.settings)
        self.db = Database(self.settings)
        self.stop = False
        self.ctx = mp.get_context("spawn")

    def run_forever(self) -> None:
        rec = jobs.recover_after_crash(self.db)
        swept = sweep_tmp(self.settings)
        log.info("worker démarré (reprise: %s, temporaires balayés: %d)", rec, swept)
        (self.settings.run_dir / "worker.heartbeat").write_text(str(now()))
        while not self.stop:
            job = jobs.claim_next(self.db)
            if job is None:
                (self.settings.run_dir / "worker.heartbeat").write_text(str(now()))
                time.sleep(self.settings.worker_poll_s)
                continue
            self.supervise(job)

    def supervise(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        proc = self.ctx.Process(target=run_job_child, args=(job_id,), daemon=True)
        proc.start()
        started = time.monotonic()
        timeout = job["timeout_s"]
        while proc.is_alive():
            proc.join(0.3)
            row = self.db.one("SELECT status, cancel_requested FROM jobs WHERE id=?", (job_id,))
            self.db.run("UPDATE jobs SET heartbeat_at=? WHERE id=? AND status='running'", (now(), job_id))
            (self.settings.run_dir / "worker.heartbeat").write_text(str(now()))
            if row is None or row["cancel_requested"]:
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                jobs.mark_cancelled(self.db, job_id)
                break
            if time.monotonic() - started > timeout:
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                jobs.fail(self.db, job_id, "job_timeout", {"seconds": timeout})
                break
        proc.join(1)
        row = self.db.one("SELECT status, attempts, max_attempts FROM jobs WHERE id=?", (job_id,))
        if row is not None and row["status"] == "running":
            # le sous-processus est mort sans conclure (crash, mémoire) : reprise bornée
            if row["attempts"] < row["max_attempts"]:
                self.db.run("UPDATE jobs SET status='queued', stage='requeued_after_crash', updated_at=? WHERE id=?", (now(), job_id))
            else:
                jobs.fail(self.db, job_id, "worker_crashed", {"exitcode": proc.exitcode})
        shutil.rmtree(self.settings.tmp_dir / job_id, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sup = Supervisor()

    def _stop(*_a: Any) -> None:
        sup.stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    sup.run_forever()


if __name__ == "__main__":
    main()


def worker_alive(run_dir: Path, max_age: float = 15.0) -> bool:
    hb = run_dir / "worker.heartbeat"
    try:
        return now() - float(hb.read_text()) < max_age
    except (OSError, ValueError):
        return False
