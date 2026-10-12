"""Request and plan lifecycle: creation, interpretation, clarification, validation, computation, evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from . import jobs
from .db import dumps, loads, new_id, now
from .engine import store
from .engine.compute import Engine, UserFilters
from .errors import NotFound, UserError
from .i18n import translate
from .plan.clarify import apply_plan_answer, questions_for
from .plan.grammar import AnalysisPlan
from .plan.interpret import interpret
from .plan.llm import get_provider
from .plan.llm_schema import LLMOutput, compile_output
from .plan.suggest import default_filter_dimensions, plan_from_suggestions, suggestions
from .plan.validate import errors_only, parse_plan, validate_plan
from .services import Services, canonical_json

SUPPORTED_LANGS = ("fr", "en", "ru", "zh-Hans", "de", "es")


def suggestion_key(sid: str) -> str:
    if sid.startswith("by_"):
        return "by_dimension"
    if sid.startswith("sum_"):
        return "sum_field"
    return sid


class Plans:
    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.db = svc.db
        self.settings = svc.settings

    # ------------------------------------------------------------------ lecture
    def get(self, plan_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM analysis_plans WHERE id=?", (plan_id,))
        if row is None:
            raise NotFound("plan")
        req = self.db.one("SELECT * FROM analysis_requests WHERE id=?", (row["request_id"],)) if row["request_id"] else None
        status = row["status"]
        job = None
        if status == "interpreting":
            jrow = self.db.one(
                "SELECT * FROM jobs WHERE kind='interpret' AND payload LIKE ? ORDER BY created_at DESC LIMIT 1",
                (f'%"plan_id":"{plan_id}"%',),
            )
            if jrow is not None:
                job = jobs.public(dict(jrow))
                if job["status"] in ("cancelled", "failed"):
                    # job cancelled or stopped (timeout, crash): no plan was produced
                    code = "ai_cancelled" if job["status"] == "cancelled" else (job["error"] or {}).get("code", "ai_error")
                    self._set_status(plan_id, "ai_failed", issues=[{"code": code, "params": (job["error"] or {}).get("params", {}), "severity": "error"}])
                    return self.get(plan_id)
        return {
            "job": job,
            "id": row["id"],
            "project_id": row["project_id"],
            "table_id": row["table_id"],
            "dataset_version": row["dataset_version"],
            "plan": loads(row["plan"], None),
            "status": status,
            "origin": row["origin"],
            "model": row["model"],
            "questions": loads(row["questions"], []),
            "answers": loads(row["answers"], {}),
            "issues": loads(row["issues"], []),
            "request": {"text": req["text"], "ui_lang": req["ui_lang"], "mode": req["mode"]} if req else None,
            "updated_at": row["updated_at"],
        }

    def list_for_project(self, pid: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT id FROM analysis_plans WHERE project_id=? ORDER BY updated_at DESC", (pid,))
        return [self.get(r["id"]) for r in rows]

    # ------------------------------------------------------------------ creation
    def suggestions(self, table_id: str, lang: str) -> list[dict[str, Any]]:
        cv = self.svc.current_version(table_id)
        out = []
        for s in suggestions(cv["profile"]):
            out.append({"id": s["id"], "params": s["params"], "label": translate(lang, f"suggestions.{suggestion_key(s['id'])}", s["params"])})
        return out

    def create_request(self, pid: str, table_id: str, text: str, suggestion_ids: list[str], ui_lang: str) -> dict[str, Any]:
        self.svc.get_project(pid)
        trow = self.svc.table_row(table_id)
        if trow["project_id"] != pid:
            raise NotFound("table")
        if ui_lang not in SUPPORTED_LANGS:
            ui_lang = "fr"
        text = (text or "").strip()
        if len(text) > self.settings.limits.max_request_chars:
            raise UserError("request_too_long", limit=self.settings.limits.max_request_chars)
        if not text and not suggestion_ids:
            raise UserError("request_empty")
        mode = "ai" if text else "suggestions"
        rid = new_id("req")
        self.db.run("INSERT INTO analysis_requests (id, project_id, table_id, text, ui_lang, suggestion_ids, mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, pid, table_id, text, ui_lang, dumps(suggestion_ids), mode, now()))
        cv = self.svc.current_version(table_id)
        plan_id = new_id("pln")
        t = now()
        self.db.run(
            "INSERT INTO analysis_plans (id, project_id, request_id, table_id, dataset_version, plan, status, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
            (plan_id, pid, rid, table_id, cv["version"], "interpreting" if mode == "ai" else "draft", mode, t, t),
        )
        self.svc.update_project_state(pid, {"last_plan_id": plan_id, "last_table_id": table_id})
        if mode == "ai":
            # The text may combine suggestions + a free request: the suggestions are passed as extra detail.
            job_id = jobs.enqueue(self.db, "interpret", pid, {"plan_id": plan_id, "suggestions": suggestion_ids}, timeout_s=self.settings.ollama_timeout_s * 3 + 60, max_attempts=1)
            return {"plan_id": plan_id, "job_id": job_id, "mode": mode}
        labels = self._suggestion_labels(ui_lang)
        try:
            plan = plan_from_suggestions(cv["profile"], suggestion_ids, labels)
        except ValueError as exc:
            raise UserError("suggestions_invalid") from exc
        self._store_plan(plan_id, plan, origin="suggestions", model=None)
        return {"plan_id": plan_id, "job_id": None, "mode": mode}

    def _suggestion_labels(self, lang: str) -> dict[str, str]:
        keys = ["avg_basket", "order_count", "customer_count", "margin", "margin_rate", "weighted_rate", "row_count"]
        return {k: translate(lang, f"metric_labels.{k}", {}) for k in keys}

    def run_interpret(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        plan_id = payload["plan_id"]
        prow = self.get(plan_id)
        req = prow["request"]
        cv = self.svc.current_version(prow["table_id"])
        jobs.progress(self.db, job_id, "ai", 0.1)
        provider = get_provider(self.settings)
        text = req["text"]
        if payload.get("suggestions"):
            sugg = {s["id"]: s for s in suggestions(cv["profile"])}
            extra = [translate(req["ui_lang"], f"suggestions.{suggestion_key(s)}", sugg[s]["params"]) for s in payload["suggestions"] if s in sugg]
            if extra:
                text = text + "\n" + "\n".join(extra)
        try:
            result = interpret(provider, text, req["ui_lang"], cv["profile"])
        except UserError as exc:
            self._set_status(plan_id, "ai_failed", issues=[{"code": exc.code, "params": exc.params, "severity": "error"}], model=self.settings.ollama_model)
            return {"plan_id": plan_id, "status": "ai_failed", "error": exc.to_dict()}
        jobs.progress(self.db, job_id, "validate", 0.9)
        if not result["understood"]:
            self._set_status(plan_id, "not_understood", issues=[{"code": "ai_not_understood", "params": {"unavailable": result["unavailable"]}, "severity": "error"}], model=result["model"])
            return {"plan_id": plan_id, "status": "not_understood"}
        plan: AnalysisPlan = result["plan"]
        if not plan.filter_dimensions:
            plan.filter_dimensions = default_filter_dimensions(cv["profile"])
        extra_issues = [{"code": "kpi_unavailable", "severity": "warning", "params": u} for u in result["unavailable"]]
        self._store_plan(plan_id, plan, origin=f"ai:{result['provider']}", model=result["model"], extra_issues=extra_issues, attempts=result["attempts"])
        return {"plan_id": plan_id, "status": self.get(plan_id)["status"], "attempts": result["attempts"]}

    def builder(self, pid: str, table_id: str, spec: dict[str, Any], ui_lang: str, replace_plan: str | None = None) -> dict[str, Any]:
        """Guided builder: same format as the model output, same validations, same engine."""
        try:
            out = LLMOutput.model_validate({**spec, "understood": True})
            plan = compile_output(out)
        except (ValidationError, ValueError) as exc:
            raise UserError("builder_invalid", detail=str(exc)[:300]) from exc
        cv = self.svc.current_version(table_id)
        if not plan.filter_dimensions:
            plan.filter_dimensions = default_filter_dimensions(cv["profile"])
        if replace_plan:
            prow = self.get(replace_plan)
            plan_id = replace_plan
            self.db.run("UPDATE analysis_plans SET origin='builder', dataset_version=?, answers='{}' WHERE id=?", (cv["version"], plan_id))
            _ = prow
        else:
            rid = new_id("req")
            self.db.run("INSERT INTO analysis_requests (id, project_id, table_id, text, ui_lang, suggestion_ids, mode, created_at) VALUES (?, ?, ?, '', ?, '[]', 'builder', ?)",
                        (rid, pid, table_id, ui_lang, now()))
            plan_id = new_id("pln")
            t = now()
            self.db.run("INSERT INTO analysis_plans (id, project_id, request_id, table_id, dataset_version, plan, status, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, 'draft', 'builder', ?, ?)",
                        (plan_id, pid, rid, table_id, cv["version"], t, t))
        self._store_plan(plan_id, plan, origin="builder", model=None)
        self.svc.update_project_state(pid, {"last_plan_id": plan_id, "last_table_id": table_id})
        return self.get(plan_id)

    def from_plan_json(self, pid: str, table_id: str, plan_data: dict[str, Any], origin: str) -> str:
        plan, issues = parse_plan(plan_data)
        if plan is None:
            raise UserError("plan_invalid", issues=issues)
        cv = self.svc.current_version(table_id)
        rid = new_id("req")
        self.db.run("INSERT INTO analysis_requests (id, project_id, table_id, text, ui_lang, suggestion_ids, mode, created_at) VALUES (?, ?, ?, '', 'fr', '[]', ?, ?)",
                    (rid, pid, table_id, origin, now()))
        plan_id = new_id("pln")
        t = now()
        self.db.run("INSERT INTO analysis_plans (id, project_id, request_id, table_id, dataset_version, plan, status, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, 'draft', ?, ?, ?)",
                    (plan_id, pid, rid, table_id, cv["version"], origin, t, t))
        self._store_plan(plan_id, plan, origin=origin, model=None)
        return plan_id

    # ------------------------------------------------------------------ validation / clarification
    def _set_status(self, plan_id: str, status: str, issues: list | None = None, model: str | None = None) -> None:
        self.db.run("UPDATE analysis_plans SET status=?, issues=?, model=COALESCE(?, model), updated_at=? WHERE id=?",
                    (status, dumps(issues or []), model, now(), plan_id))

    def _store_plan(self, plan_id: str, plan: AnalysisPlan, origin: str, model: str | None, extra_issues: list | None = None, attempts: list | None = None) -> None:
        prow = self.db.one("SELECT * FROM analysis_plans WHERE id=?", (plan_id,))
        assert prow is not None
        cv = self.svc.current_version(prow["table_id"])
        issues = validate_plan(plan, cv["profile"])
        answers = loads(prow["answers"], {})
        status = "invalid" if errors_only(issues) else "ready"
        questions: list[dict[str, Any]] = []
        if status == "ready":
            tq = self.svc.pending_table_questions(loads(self.svc.table_row(prow["table_id"])["meta"], {}), cv["config"], cv["profile"])
            questions = questions_for(plan, cv["profile"], cv["config"], tq, answers)
            if questions:
                status = "needs_clarification"
        all_issues = list(issues) + list(extra_issues or [])
        if attempts:
            all_issues.append({"code": "ai_attempts", "severity": "info", "params": {"attempts": attempts}})
        self.db.run(
            "UPDATE analysis_plans SET plan=?, status=?, origin=?, model=COALESCE(?, model), questions=?, issues=?, dataset_version=?, updated_at=? WHERE id=?",
            (plan.model_dump_json(), status, origin, model, dumps(questions), dumps(all_issues), cv["version"], now(), plan_id),
        )

    def answer(self, plan_id: str, answers_in: dict[str, str]) -> dict[str, Any]:
        prow = self.get(plan_id)
        if prow["plan"] is None:
            raise UserError("plan_not_ready")
        plan = AnalysisPlan.model_validate(prow["plan"])
        answers = dict(prow["answers"])
        qmap = {q["key"]: q for q in prow["questions"]}
        tid = prow["table_id"]
        for key, value in answers_in.items():
            q = qmap.get(key)
            if q is None or value not in q["options"]:
                raise UserError("invalid_answer", key=key)
            answers[key] = value
            if q["kind"] in ("date_order", "number_convention", "currency", "duplicates"):
                cv = self.svc.current_version(tid)
                cfg = cv["config"].model_copy(deep=True)
                if q["kind"] == "date_order":
                    for c in cfg.columns:
                        if c.id == q["params"]["column"]:
                            c.date_order = value  # type: ignore[assignment]
                elif q["kind"] == "number_convention":
                    for c in cfg.columns:
                        if c.id == q["params"]["column"]:
                            c.convention = value  # type: ignore[assignment]
                elif q["kind"] == "currency" and value != "none":
                    for c in cfg.columns:
                        if c.id == q["params"]["column"]:
                            c.type = "money"
                            c.currency = value
                elif q["kind"] == "duplicates" and value == "exclude":
                    for group in cv["profile"].get("duplicates", {}).get("sample", []):
                        for r in group[1:]:
                            cfg.excluded[int(r)] = "duplicate"
                cfg.confirmed[key] = value
                self.svc.update_config(tid, cfg, None, f"answer:{q['kind']}", {"key": key, "value": value})
            else:
                plan = apply_plan_answer(plan, q, value)
        self.db.run("UPDATE analysis_plans SET answers=? WHERE id=?", (dumps(answers), plan_id))
        self._store_plan(plan_id, plan, origin=prow["origin"], model=None)
        return self.get(plan_id)

    def update_plan(self, plan_id: str, plan_data: dict[str, Any]) -> dict[str, Any]:
        prow = self.get(plan_id)
        plan, issues = parse_plan(plan_data)
        if plan is None:
            raise UserError("plan_invalid", issues=issues)
        self._store_plan(plan_id, plan, origin=prow["origin"], model=None)
        return self.get(plan_id)

    def validate(self, plan_id: str) -> dict[str, Any]:
        prow = self.get(plan_id)
        if prow["status"] != "ready":
            raise UserError("plan_not_ready", status=prow["status"])
        self.db.run("UPDATE analysis_plans SET status='validated', updated_at=? WHERE id=?", (now(), plan_id))
        self.svc.update_project_state(prow["project_id"], {"last_plan_id": plan_id, "validated_plan_id": plan_id})
        return self.get(plan_id)

    def _ensure_current(self, prow: dict[str, Any]) -> tuple[AnalysisPlan, dict[str, Any]]:
        """Revalidates the plan if the table changed version. Never a figure computed on a stale version."""
        if prow["status"] != "validated":
            raise UserError("plan_not_validated", status=prow["status"])
        cv = self.svc.current_version(prow["table_id"])
        plan = AnalysisPlan.model_validate(prow["plan"])
        if cv["version"] != prow["dataset_version"]:
            issues = validate_plan(plan, cv["profile"])
            if errors_only(issues):
                self.db.run("UPDATE analysis_plans SET status='invalid', issues=?, updated_at=? WHERE id=?", (dumps(issues), now(), prow["id"]))
                raise UserError("plan_invalidated_by_new_version", status_code=409)
            self.db.run("UPDATE analysis_plans SET dataset_version=?, updated_at=? WHERE id=?", (cv["version"], now(), prow["id"]))
        return plan, cv

    def engine(self, plan_id: str) -> tuple[Engine, dict[str, Any], dict[str, Any]]:
        prow = self.get(plan_id)
        plan, cv = self._ensure_current(prow)
        con, lock = store.open_dataset(cv["parquet"])
        return Engine(con, lock, plan, cv["profile"]), prow, cv

    def dashboard(self, plan_id: str, filters: dict[str, Any] | None) -> dict[str, Any]:
        eng, prow, cv = self.engine(plan_id)
        user = UserFilters.from_dict(filters)
        self._check_dims(user, eng)
        key = hashlib.sha256(canonical_json({"p": prow["plan"], "v": cv["version"], "f": user.to_dict(), "t": prow["table_id"]}).encode()).hexdigest()
        cached = self.db.one("SELECT payload FROM result_cache WHERE key=? AND dataset_version=?", (key, cv["version"]))
        if cached:
            payload = loads(cached["payload"])
            payload["cache"] = "hit"
            return payload
        payload = eng.dashboard(user)
        payload["detail"] = eng.detail(user)
        payload["plan"] = prow["plan"]
        payload["plan_id"] = plan_id
        payload["dataset_version"] = cv["version"]
        payload["table_id"] = prow["table_id"]
        payload["quality"] = self._quality(cv["profile"], eng)
        payload["issues"] = [i for i in prow["issues"] if i.get("severity") in ("warning",)]
        self.db.run("INSERT OR REPLACE INTO result_cache (key, project_id, plan_id, dataset_version, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (key, prow["project_id"], plan_id, cv["version"], dumps(payload), now()))
        payload["cache"] = "miss"
        return payload

    @staticmethod
    def _check_dims(user: UserFilters, eng: Engine) -> None:
        for d in user.dims:
            if d not in eng.plan.filter_dimensions:
                raise UserError("invalid_filter_dimension", field=d)

    @staticmethod
    def _quality(profile: dict[str, Any], eng: Engine) -> dict[str, Any]:
        used = set()
        from .plan.validate import fields_used

        used = fields_used(eng.plan)
        cols = [c for c in profile["columns"] if c["id"] in used]
        return {
            "excluded_rows": profile.get("excluded", {}),
            "duplicates": profile.get("duplicates", {}).get("groups", 0),
            "columns": [{"id": c["id"], "name": c["name"], "invalid": c.get("invalid", 0), "nulls": c.get("nulls", 0),
                         "formula_missing": c.get("formula_missing", 0), "ocr_low": c.get("ocr_low", 0), "convention": c.get("convention"),
                         "date_order": c.get("date_order"), "currency": c.get("currency"), "currencies": c.get("currencies", [])} for c in cols],
            "corrections": profile.get("corrections", 0),
            "notes": profile.get("notes", []),
            "coverage": profile.get("coverage", {}),
        }

    def evidence(self, plan_id: str, metric_id: str, filters: dict[str, Any] | None, offset: int = 0) -> dict[str, Any]:
        eng, prow, cv = self.engine(plan_id)
        user = UserFilters.from_dict(filters)
        self._check_dims(user, eng)
        try:
            ev = eng.evidence(metric_id, user, offset=offset)
        except KeyError as exc:
            raise NotFound("metric") from exc
        ev["dataset_version"] = cv["version"]
        ev["exclusions"] = {
            "rows_excluded_by_reason": cv["profile"].get("excluded", {}),
            "nulls_ignored": eng.card(next(m for m in eng.plan.metrics if m.id == metric_id), user).get("nulls_ignored", {}),
            "invalid_values": {c["id"]: c.get("invalid", 0) for c in cv["profile"]["columns"] if c["id"] in {f["id"] for f in ev["fields"]} and c.get("invalid")},
            "formula_missing": {c["id"]: c.get("formula_missing", 0) for c in cv["profile"]["columns"] if c.get("formula_missing")},
        }
        ev["rules"] = ["missing_values_not_zero", "division_by_zero_unavailable", "decimal_exact", "currencies_separate"]
        if ev.get("currency_key"):
            ev["rules"].append("split_by_currency")
        return ev
