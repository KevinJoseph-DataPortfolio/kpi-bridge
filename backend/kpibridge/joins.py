"""Jointure prudente entre deux tableaux d'un projet.

Seule une jointure « plusieurs vers un » (lookup) est autorisée : la clé du tableau de droite doit être unique.
Contrôles avant création : unicité de la clé, compatibilité des types, nombre de lignes et sommes des colonnes
numériques du tableau de gauche identiques avant et après. Toute duplication bloque la jointure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .db import dumps, new_id, now
from .errors import UserError
from .ingest import save_raw
from .ingest.base import RawTable, native_bool, native_date, native_number
from .normalize import infer_config
from .services import Services
from .storage import project_path, restrict


def _encode(v: Any) -> str | None:
    import datetime as dt

    if v is None:
        return None
    if isinstance(v, bool):
        return native_bool(v)
    if isinstance(v, (Decimal, int, float)):
        return native_number(v) or None
    if isinstance(v, (dt.date, dt.datetime)):
        return native_date(v.isoformat())
    return str(v)


def _key_kind(t: str) -> str:
    return "num" if t in ("number", "money") else ("text" if t in ("text", "id") else t)


def analyse_join(svc: Services, left_id: str, left_key: str, right_id: str, right_key: str) -> dict[str, Any]:
    lv, rv = svc.current_version(left_id), svc.current_version(right_id)
    if lv["project_id"] != rv["project_id"]:
        raise UserError("join_cross_project")
    lcols = {c["id"]: c for c in lv["profile"]["columns"]}
    rcols = {c["id"]: c for c in rv["profile"]["columns"]}
    if left_key not in lcols or right_key not in rcols:
        raise UserError("unknown_field")
    if _key_kind(lcols[left_key]["type"]) != _key_kind(rcols[right_key]["type"]):
        return {"allowed": False, "reason": "key_types_differ", "left_type": lcols[left_key]["type"], "right_type": rcols[right_key]["type"]}
    left = pq.read_table(lv["parquet"])
    right = pq.read_table(rv["parquet"])
    rk = right.column(right_key)
    non_null = pc.drop_null(rk)
    distinct = len(pc.unique(non_null))
    dup = len(non_null) - distinct
    lk = left.column(left_key)
    matched = pc.sum(pc.is_in(lk, value_set=pc.unique(non_null))).as_py() or 0
    report: dict[str, Any] = {
        "left_rows": left.num_rows,
        "right_rows": right.num_rows,
        "right_key_unique": dup == 0,
        "right_key_duplicates": dup,
        "right_key_nulls": right.num_rows - len(non_null),
        "left_matched": matched,
        "left_unmatched": left.num_rows - matched,
        "cardinality": "many_to_one" if dup == 0 else "many_to_many",
    }
    if dup:
        report.update(allowed=False, reason="duplicate_keys_would_multiply_rows")
        return report
    report["allowed"] = True
    return report


def create_join(svc: Services, pid: str, left_id: str, left_key: str, right_id: str, right_key: str, right_columns: list[str]) -> dict[str, Any]:
    report = analyse_join(svc, left_id, left_key, right_id, right_key)
    if not report.get("allowed"):
        raise UserError("join_blocked", **{k: v for k, v in report.items() if isinstance(v, (int, str, bool))})
    lv, rv = svc.current_version(left_id), svc.current_version(right_id)
    left = pq.read_table(lv["parquet"])
    right = pq.read_table(rv["parquet"])
    rcols = {c["id"]: c for c in rv["profile"]["columns"]}
    bring = [c for c in right_columns if c in rcols and c != right_key][:30]
    rsel = right.select([right_key] + bring + ["_src"]).rename_columns(["__rk"] + [f"r_{c}" for c in bring] + ["__rsrc"])
    if _key_kind(rcols[right_key]["type"]) == "num":
        rsel = rsel.set_column(0, "__rk", pc.cast(rsel.column(0), left.schema.field(left_key).type))
    left_idx = left.append_column("__lpos", pa.array(range(left.num_rows), type=pa.int64()))
    joined = left_idx.join(rsel, keys=left_key, right_keys="__rk", join_type="left outer").sort_by("__lpos")
    # contrôles avant/après
    if joined.num_rows != left.num_rows:
        raise UserError("join_blocked", reason="row_count_changed", before=left.num_rows, after=joined.num_rows)
    sums = {}
    for c in lv["profile"]["columns"]:
        if c["type"] in ("number", "money"):
            before = pc.sum(left.column(c["id"])).as_py()
            after = pc.sum(joined.column(c["id"])).as_py()
            sums[c["id"]] = {"before": str(before), "after": str(after)}
            if before != after:
                raise UserError("join_blocked", reason="sum_changed", field=c["name"])
    lcols = [c for c in lv["profile"]["columns"]]
    header = [c["name"] for c in lcols] + [f"{rcols[c]['name']}" for c in bring]
    rows: list[list[str | None]] = [header]
    srcs = ["header"]
    data = joined.to_pydict()
    for i in range(joined.num_rows):
        row = [_encode(data[c["id"]][i]) for c in lcols] + [_encode(data[f"r_{c}"][i]) for c in bring]
        rows.append(row)
        rs = data["__rsrc"][i]
        srcs.append(f"{data['_src'][i]} | join: {rs}" if rs else f"{data['_src'][i]} | join: —")
    lname = svc.table_row(left_id)["name"]
    rname = svc.table_row(right_id)["name"]
    raw = RawTable(name=f"{lname} ⋈ {rname}"[:200], kind="join", rows=rows, row_src=srcs, header_row=0, col_refs=[str(i + 1) for i in range(len(header))])
    raw.note("join", left=lname, right=rname, left_key=left_key, right_key=right_key, report={k: v for k, v in report.items() if not isinstance(v, dict)}, sums=sums)
    tid = new_id("tbl")
    raw_path = project_path(svc.settings, pid, "raw", f"{tid}.parquet")
    save_raw(raw, raw_path)
    restrict(raw_path)
    meta = raw.meta_dict()
    cfg, questions = infer_config(raw.rows, meta)
    # les types d'origine sont conservés (valeurs natives encodées) ; on reporte devise et type texte/identifiant
    src_specs = [next(s for s in lv["config"].columns if s.id == c["id"]) for c in lcols] + [
        next(s for s in rv["config"].columns if s.id == c) for c in bring
    ]
    src_cur = [c.get("currency") for c in lcols] + [rcols[c].get("currency") for c in bring]
    for spec, src, cur in zip(cfg.columns, src_specs, src_cur, strict=False):
        if src.type in ("money", "text", "id", "percent"):
            spec.type = src.type
        if src.type == "money":
            spec.currency = src.currency or cur
    meta["questions"] = []
    _ = questions
    svc.db.run(
        "INSERT INTO extracted_tables (id, project_id, source_file_id, name, kind, n_rows, n_cols, raw_path, meta, current_version, created_at) VALUES (?, ?, NULL, ?, 'join', ?, ?, ?, ?, 0, ?)",
        (tid, pid, raw.name, len(rows), len(header), raw_path.name, dumps(meta), now()),
    )
    svc._write_version(pid, tid, raw.rows, raw.row_src, meta, cfg, op="join", params={"left": left_id, "right": right_id, "left_key": left_key, "right_key": right_key}, note="join")
    return {"table_id": tid, "report": report, "sums": sums}
