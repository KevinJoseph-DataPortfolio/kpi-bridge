"""Accès SQLite (métadonnées, tâches) avec migrations versionnées."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Settings


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def now() -> float:
    return time.time()


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def migrate(settings: Settings) -> None:
    con = connect(settings.db_path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at REAL NOT NULL)")
        applied = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
        files = sorted(
            (p for p in resources.files("kpibridge.migrations").iterdir() if p.name.endswith(".sql")),
            key=lambda p: p.name,
        )
        for f in files:
            if f.name in applied:
                continue
            con.execute("BEGIN")
            try:
                for stmt in f.read_text(encoding="utf-8").split(";"):
                    if stmt.strip():
                        con.execute(stmt)
                con.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.name, now()))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
    finally:
        con.close()


class Database:
    """Petit wrapper thread-safe (une connexion par appel)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        con = connect(self.settings.db_path)
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        con = connect(self.settings.db_path)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.conn() as con:
            return con.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.conn() as con:
            return list(con.execute(sql, params).fetchall())

    def run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.conn() as con:
            cur = con.execute(sql, params)
            return cur.rowcount


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(raw: str | None, default: Any = None) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)
