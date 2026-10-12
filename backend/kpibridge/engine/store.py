"""DuckDB computation connections: in-memory, no external access, no extensions, locked configuration."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[duckdb.DuckDBPyConnection, threading.Lock]] = OrderedDict()
MAX_OPEN = 6

SAFE_CONFIG = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_community_extensions": False,
    "allow_unsigned_extensions": False,
    "threads": 2,
    "memory_limit": "1GB",
}


def open_dataset(parquet_path: Path) -> tuple[duckdb.DuckDBPyConnection, threading.Lock]:
    """Loads the Parquet file (read by pyarrow, not by DuckDB) into a locked in-memory database."""
    key = str(parquet_path)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    arrow_tbl = pq.read_table(parquet_path)
    con = duckdb.connect(":memory:", config=dict(SAFE_CONFIG))  # type: ignore[arg-type]
    con.register("src_arrow", arrow_tbl)
    con.execute("CREATE TABLE data AS SELECT * FROM src_arrow")
    con.unregister("src_arrow")
    con.execute("SET lock_configuration = true")
    entry = (con, threading.Lock())
    with _LOCK:
        _CACHE[key] = entry
        while len(_CACHE) > MAX_OPEN:
            _k, (old, _l) = _CACHE.popitem(last=False)
            try:
                old.close()
            except duckdb.Error:
                pass
    return entry


def forget(prefix: str) -> None:
    """Closes the connections of a deleted project."""
    with _LOCK:
        for k in [k for k in _CACHE if k.startswith(prefix)]:
            con, _l = _CACHE.pop(k)
            try:
                con.close()
            except duckdb.Error:
                pass
