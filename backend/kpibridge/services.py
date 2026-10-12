"""Services métier partagés entre l'API et le worker : projets, fichiers, tables, versions, plans."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from . import jobs
from .config import Settings
from .db import Database, dumps, loads, new_id, now
from .engine import store
from .errors import LimitExceeded, NotFound, UserError
from .ingest import IngestContext, ingest_file, load_raw_rows, save_raw
from .ingest.detect import check_extension
from .normalize import TableConfig, apply_config, infer_config
from .storage import delete_project_files, ensure_project_dirs, make_readonly, project_path, restrict

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


class Services:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    # ------------------------------------------------------------------ projets
    def create_project(self, name: str, ui_lang: str = "fr") -> dict[str, Any]:
        pid = new_id("prj")
        t = now()
        name = (name or "").strip()[:120] or "Projet"
        self.db.run("INSERT INTO projects (id, name, ui_lang, state, created_at, updated_at) VALUES (?, ?, ?, '{}', ?, ?)", (pid, name, ui_lang, t, t))
        ensure_project_dirs(self.settings, pid)
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM projects WHERE id=?", (pid,))
        if row is None:
            raise NotFound("project")
        return {"id": row["id"], "name": row["name"], "ui_lang": row["ui_lang"], "state": loads(row["state"], {}),
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT p.*, (SELECT COUNT(*) FROM source_files s WHERE s.project_id=p.id) AS n_files FROM projects p ORDER BY updated_at DESC")
        return [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"], "n_files": r["n_files"], "state": loads(r["state"], {})} for r in rows]

    def update_project_state(self, pid: str, patch: dict[str, Any]) -> dict[str, Any]:
        proj = self.get_project(pid)
        state = {**proj["state"], **patch}
        self.db.run("UPDATE projects SET state=?, updated_at=? WHERE id=?", (dumps(state), now(), pid))
        return state

    def rename_project(self, pid: str, name: str) -> None:
        self.get_project(pid)
        self.db.run("UPDATE projects SET name=?, updated_at=? WHERE id=?", (name.strip()[:120] or "Projet", now(), pid))

    def touch(self, pid: str) -> None:
        self.db.run("UPDATE projects SET updated_at=? WHERE id=?", (now(), pid))

    def delete_project(self, pid: str) -> None:
        self.get_project(pid)
        for j in self.db.all("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)):
            jobs.request_cancel(self.db, j["id"])
        base = str(project_path(self.settings, pid))
        store.forget(base)
        self.db.run("DELETE FROM projects WHERE id=?", (pid,))
        delete_project_files(self.settings, pid)

    # ------------------------------------------------------------------ fichiers
    def save_upload(self, pid: str, filename: str, chunks: Any, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Enregistre un fichier en flux avec arrêt dès que la limite d'octets est dépassée."""
        self.get_project(pid)
        ext = check_extension(filename)
        fid = new_id("src")
        stored = f"{fid}.{ext}"
        dest = project_path(self.settings, pid, "sources", stored)
        limit = self.settings.limits.max_file_bytes
        size = 0
        h = hashlib.sha256()
        try:
            with open(dest, "wb") as out:
                for chunk in chunks:
                    size += len(chunk)
                    if size > limit:
                        raise LimitExceeded("file_too_large", limit_mib=limit // (1024 * 1024))
                    h.update(chunk)
                    out.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        if size == 0:
            dest.unlink(missing_ok=True)
            raise UserError("empty_file")
        make_readonly(dest)
        safe_name = "".join(ch for ch in filename if ch.isprintable() and ch not in '<>:"/\\|?*')[:150] or f"fichier.{ext}"
        t = now()
        self.db.run(
            "INSERT INTO source_files (id, project_id, original_name, stored_name, ext, size, sha256, status, options, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (fid, pid, safe_name, stored, ext, size, h.hexdigest(), dumps(options or {}), t),
        )
        self.touch(pid)
        return {"id": fid, "name": safe_name, "size": size, "ext": ext}

    def enqueue_ingest(self, pid: str, fid: str, ui_lang: str) -> str:
        return jobs.enqueue(self.db, "ingest", pid, {"file_id": fid, "ui_lang": ui_lang}, self.settings.limits.job_timeout_s)

    def add_example(self, pid: str, example: str) -> dict[str, Any]:
        files = {p.name: p for p in EXAMPLES_DIR.iterdir() if p.is_file() and not p.name.startswith(".")}
        if example not in files:
            raise NotFound("example")
        path = files[example]

        def chunks() -> Any:
            with open(path, "rb") as fh:
                while True:
                    b = fh.read(1 << 20)
                    if not b:
                        break
                    yield b

        return self.save_upload(pid, example, chunks())

    def list_examples(self) -> list[dict[str, Any]]:
        if not EXAMPLES_DIR.exists():
            return []
        return [{"name": p.name, "size": p.stat().st_size} for p in sorted(EXAMPLES_DIR.iterdir()) if p.is_file() and not p.name.startswith(".")]

    def reingest(self, pid: str, fid: str, options: dict[str, Any], ui_lang: str) -> str:
        row = self.db.one("SELECT * FROM source_files WHERE id=? AND project_id=?", (fid, pid))
        if row is None:
            raise NotFound("file")
        allowed = {k: v for k, v in options.items() if k in ("delimiter", "encoding", "sheets", "pages", "ocr_lang", "json_key")}
        if "delimiter" in allowed and allowed["delimiter"] not in (",", ";", "\t", "|", None):
            raise UserError("invalid_option", option="delimiter")
        self.db.run("UPDATE source_files SET options=?, status='queued', error_code=NULL, error_params=NULL WHERE id=?", (dumps(allowed), fid))
        return self.enqueue_ingest(pid, fid, ui_lang)

    def delete_file(self, pid: str, fid: str) -> None:
        row = self.db.one("SELECT * FROM source_files WHERE id=? AND project_id=?", (fid, pid))
        if row is None:
            raise NotFound("file")
        for t in self.db.all("SELECT id FROM extracted_tables WHERE source_file_id=?", (fid,)):
            self._remove_table_files(pid, t["id"])
        self.db.run("DELETE FROM extracted_tables WHERE source_file_id=?", (fid,))
        self.db.run("DELETE FROM source_files WHERE id=?", (fid,))
        p = project_path(self.settings, pid, "sources", row["stored_name"])
        if p.exists():
            p.chmod(0o600)
            p.unlink()

    def _remove_table_files(self, pid: str, tid: str) -> None:
        base = project_path(self.settings, pid)
        store.forget(str(base / "ds" / tid))
        for sub in ("raw", "ds"):
            for f in (base / sub).glob(f"{tid}*"):
                f.unlink(missing_ok=True)

    # ------------------------------------------------------------------ ingestion (worker)
    def run_ingest(self, job_id: str, payload: dict[str, Any], work_dir: Path) -> dict[str, Any]:
        fid = payload["file_id"]
        row = self.db.one("SELECT * FROM source_files WHERE id=?", (fid,))
        if row is None:
            raise NotFound("file")
        pid = row["project_id"]
        src = project_path(self.settings, pid, "sources", row["stored_name"])
        options = loads(row["options"], {})

        def prog(stage: str, value: float) -> None:
            jobs.progress(self.db, job_id, stage, value)

        self.db.run("UPDATE source_files SET status='processing' WHERE id=?", (fid,))
        ctx = IngestContext(limits=self.settings.limits, options=options, work_dir=work_dir, progress=prog,
                            tesseract_cmd=self.settings.tesseract_cmd, ui_lang=payload.get("ui_lang", "fr"))
        prog("import", 0.02)
        try:
            tables = ingest_file(src, row["ext"], row["original_name"], ctx)
        except UserError as exc:
            self.db.run("UPDATE source_files SET status='failed', error_code=?, error_params=? WHERE id=?", (exc.code, dumps(exc.params), fid))
            raise
        prog("verify", 0.9)
        # remplace les tables existantes de ce fichier (réimport avec options)
        for old in self.db.all("SELECT id FROM extracted_tables WHERE source_file_id=?", (fid,)):
            self._remove_table_files(pid, old["id"])
        self.db.run("DELETE FROM extracted_tables WHERE source_file_id=?", (fid,))
        created = []
        for t in tables:
            tid = new_id("tbl")
            raw_path = project_path(self.settings, pid, "raw", f"{tid}.parquet")
            # images OCR rasterisées : copiées dans le projet (aperçu des cellules sources)
            images = {}
            for page, img in t.images.items():
                src_img = Path(work_dir) / img
                if src_img.exists():
                    dest_name = f"{tid}_p{page}.png"
                    shutil.copyfile(src_img, project_path(self.settings, pid, "raw", dest_name))
                    images[page] = dest_name
            t.images = images
            save_raw(t, raw_path)
            restrict(raw_path)
            meta = t.meta_dict()
            cfg, questions = infer_config(t.rows, meta)
            meta["questions"] = questions
            meta["source_file_id"] = fid
            self.db.run(
                "INSERT INTO extracted_tables (id, project_id, source_file_id, name, kind, n_rows, n_cols, raw_path, meta, current_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (tid, pid, fid, t.name[:200], t.kind, len(t.rows), t.n_cols, raw_path.name, dumps(meta), now()),
            )
            self._write_version(pid, tid, t.rows, t.row_src, meta, cfg, op="initial", params={}, note="auto")
            created.append(tid)
        self.db.run("UPDATE source_files SET status='ready', detected_format=? WHERE id=?", (tables[0].notes[-1]["params"].get("value") if tables else None, fid))
        self.touch(pid)
        return {"tables": created}

    # ------------------------------------------------------------------ tables & versions
    def table_row(self, tid: str) -> Any:
        row = self.db.one("SELECT * FROM extracted_tables WHERE id=?", (tid,))
        if row is None:
            raise NotFound("table")
        return row

    def raw_path(self, row: Any) -> Path:
        return project_path(self.settings, row["project_id"], "raw", row["raw_path"])

    def _write_version(self, pid: str, tid: str, rows: list, srcs: list, meta: dict, cfg: TableConfig, op: str, params: dict, note: str) -> int:
        result = apply_config(rows, srcs, meta, cfg)
        cur = self.db.one("SELECT current_version FROM extracted_tables WHERE id=?", (tid,))
        prev = cur["current_version"] if cur else 0
        maxv = self.db.one("SELECT COALESCE(MAX(version), 0) AS v FROM dataset_versions WHERE table_id=?", (tid,))
        version = (maxv["v"] if maxv else 0) + 1
        ds_path = project_path(self.settings, pid, "ds", f"{tid}_v{version}.parquet")
        pq.write_table(result.table, ds_path, compression="zstd")
        restrict(ds_path)
        t = now()
        with self.db.tx() as con:
            con.execute(
                "INSERT INTO dataset_versions (id, project_id, table_id, version, config, parquet_path, profile, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("dsv"), pid, tid, version, cfg.model_dump_json(), ds_path.name, dumps(result.profile), note, t),
            )
            con.execute(
                "INSERT INTO transformations (id, project_id, table_id, from_version, to_version, op, params, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("trf"), pid, tid, prev or None, version, op, dumps(params), t),
            )
            con.execute("UPDATE extracted_tables SET current_version=? WHERE id=?", (version, tid))
            # invalide les caches dépendants
            con.execute("DELETE FROM result_cache WHERE plan_id IN (SELECT id FROM analysis_plans WHERE table_id=?)", (tid,))
        return version

    def current_version(self, tid: str) -> dict[str, Any]:
        row = self.table_row(tid)
        v = self.db.one("SELECT * FROM dataset_versions WHERE table_id=? AND version=?", (tid, row["current_version"]))
        if v is None:
            raise NotFound("dataset_version")
        return {
            "table_id": tid,
            "project_id": row["project_id"],
            "version": v["version"],
            "config": TableConfig.model_validate_json(v["config"]),
            "profile": loads(v["profile"], {}),
            "parquet": project_path(self.settings, row["project_id"], "ds", v["parquet_path"]),
        }

    def update_config(self, tid: str, cfg: TableConfig, base_version: int | None, op: str, params: dict) -> int:
        row = self.table_row(tid)
        if base_version is not None and base_version != row["current_version"]:
            raise UserError("version_conflict", status_code=409, current=row["current_version"])
        rows, srcs = load_raw_rows(self.raw_path(row))
        width = max((len(r) for r in rows), default=0)
        if cfg.header_row < 0 or cfg.header_row >= max(1, len(rows)):
            raise UserError("invalid_header_row")
        for c in cfg.columns:
            if c.raw_index >= width or c.id != f"c{c.raw_index}":
                raise UserError("invalid_column_spec")
        for k in cfg.corrections:
            r_i, c_i = (int(x) for x in k.split(","))
            if r_i >= len(rows) or c_i >= width:
                raise UserError("invalid_correction")
        meta = loads(row["meta"], {})
        return self._write_version(row["project_id"], tid, rows, srcs, meta, cfg, op=op, params=params, note=op)

    def set_header_row(self, tid: str, header_row: int, base_version: int | None) -> int:
        row = self.table_row(tid)
        rows, srcs = load_raw_rows(self.raw_path(row))
        meta = loads(row["meta"], {})
        meta2 = {**meta, "header_row": header_row, "header_confident": True}
        excluded = {int(k): v for k, v in (meta.get("excluded") or {}).items() if v not in ("title",) and int(k) > header_row}
        for i in range(header_row):
            excluded[i] = "title"
        meta2["excluded"] = {str(k): v for k, v in excluded.items()}
        cfg, _q = infer_config(rows, meta2)
        cfg.confirmed["header_row"] = True
        return self.update_config(tid, cfg, base_version, "set_header_row", {"header_row": header_row})

    def revert(self, tid: str, version: int) -> int:
        row = self.table_row(tid)
        v = self.db.one("SELECT config FROM dataset_versions WHERE table_id=? AND version=?", (tid, version))
        if v is None:
            raise NotFound("dataset_version")
        cfg = TableConfig.model_validate_json(v["config"])
        return self.update_config(tid, cfg, None, "revert", {"to": version, "from": row["current_version"]})

    def versions(self, tid: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT version, note, created_at FROM dataset_versions WHERE table_id=? ORDER BY version", (tid,))
        trs = self.db.all("SELECT to_version, op, params FROM transformations WHERE table_id=? ORDER BY created_at", (tid,))
        ops = {t["to_version"]: {"op": t["op"], "params": loads(t["params"], {})} for t in trs}
        return [{"version": r["version"], "created_at": r["created_at"], **ops.get(r["version"], {})} for r in rows]

    def tables_overview(self, pid: str) -> list[dict[str, Any]]:
        out = []
        for row in self.db.all("SELECT * FROM extracted_tables WHERE project_id=? ORDER BY created_at", (pid,)):
            cv = self.current_version(row["id"])
            meta = loads(row["meta"], {})
            prof = cv["profile"]
            questions = self.pending_table_questions(meta, cv["config"], prof)
            out.append({
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "source_file_id": row["source_file_id"],
                "version": cv["version"],
                "n_rows": prof["n_rows"],
                "n_cols": len(prof["columns"]),
                "columns": prof["columns"],
                "coverage": prof.get("coverage", {}),
                "duplicates": prof.get("duplicates", {}),
                "excluded": prof.get("excluded", {}),
                "grain": prof.get("grain", {}),
                "notes": meta.get("notes", []),
                "questions": questions,
                "header_row": cv["config"].header_row,
                "has_images": bool(meta.get("images")),
            })
        return out

    @staticmethod
    def pending_table_questions(meta: dict[str, Any], cfg: TableConfig, profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Questions de structure encore ouvertes (en-tête incertain, conventions ambiguës non confirmées)."""
        out = []
        for q in meta.get("questions", []):
            key = q["kind"] + (":" + q["params"]["column"] if "column" in q.get("params", {}) else "")
            if cfg.confirmed.get(key):
                continue
            out.append({**q, "key": key})
        return out

    def preview(self, tid: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        cv = self.current_version(tid)
        tbl = pq.read_table(cv["parquet"])
        n = tbl.num_rows
        sl = tbl.slice(max(0, offset), max(1, min(limit, 500)))
        cols = [c["id"] for c in cv["profile"]["columns"]] + list((cv["profile"].get("derived_currency") or {}).values())
        data = sl.to_pylist()
        rows = []
        for r in data:
            rows.append({"row": r["_row"], "src": r["_src"], "values": [_jsonable(r.get(c)) for c in cols]})
        return {"version": cv["version"], "total": n, "offset": offset, "columns": [{"id": c, "label": cv["profile"]["labels"].get(c, c)} for c in cols], "rows": rows}

    def raw_view(self, tid: str, offset: int = 0, limit: int = 60) -> dict[str, Any]:
        from .ingest.base import display

        row = self.table_row(tid)
        rows, srcs = load_raw_rows(self.raw_path(row), offset, max(1, min(limit, 300)))
        cv = self.current_version(tid)
        meta = loads(row["meta"], {})
        cell_meta = meta.get("cell_meta", {})
        out_rows = []
        for i, (r, s) in enumerate(zip(rows, srcs, strict=False)):
            idx = offset + i
            flags = {}
            for j in range(len(r)):
                m = cell_meta.get(f"{idx},{j}")
                if m and m.get("low"):
                    flags[str(j)] = "ocr_low"
                if r[j] == "\x1fF":
                    flags[str(j)] = "formula_no_cache"
            corr = {k.split(",")[1]: v.value for k, v in cv["config"].corrections.items() if int(k.split(",")[0]) == idx}
            out_rows.append({"index": idx, "src": s, "cells": [display(c) for c in r], "excluded": cv["config"].excluded.get(idx), "flags": flags, "corrections": corr})
        return {"total": row["n_rows"], "offset": offset, "header_row": cv["config"].header_row, "rows": out_rows, "col_refs": meta.get("col_refs", [])}

    def cell_source(self, tid: str, r: int, c: int) -> tuple[Path | None, dict[str, Any]]:
        row = self.table_row(tid)
        meta = loads(row["meta"], {})
        m = (meta.get("cell_meta") or {}).get(f"{r},{c}")
        rows, srcs = load_raw_rows(self.raw_path(row), r, 1)
        info = {"src": srcs[0] if srcs else None, "meta": m}
        if m and "page" in m and str(m["page"]) in (meta.get("images") or {}):
            return project_path(self.settings, row["project_id"], "raw", meta["images"][str(m["page"])]), info
        return None, info


def _jsonable(v: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
