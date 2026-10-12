"""Dashboard recipes: column mapping, conventions, KPI definitions and layout, reusable on a later file.
On application, the schema, types, conventions and units are revalidated; any missing column or column of a
different type requires an explicit remapping. No old figure is reused: everything is recomputed."""

from __future__ import annotations

import json
import re
from typing import Any

from .db import dumps, loads, new_id, now
from .errors import NotFound, UserError
from .plan.grammar import AnalysisPlan
from .plan.validate import fields_used
from .plans import Plans
from .services import Services

FIELD_RE = re.compile(r'"(c\d{1,3})(_cur)?"')


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


class Recipes:
    def __init__(self, svc: Services, plans: Plans) -> None:
        self.svc = svc
        self.plans = plans
        self.db = svc.db

    def save(self, plan_id: str, name: str) -> dict[str, Any]:
        prow = self.plans.get(plan_id)
        if prow["status"] != "validated":
            raise UserError("plan_not_validated", status=prow["status"])
        cv = self.svc.current_version(prow["table_id"])
        plan = AnalysisPlan.model_validate(prow["plan"])
        used = fields_used(plan)
        cols = {c["id"]: c for c in cv["profile"]["columns"]}
        specs = {c.id: c for c in cv["config"].columns}
        fields = []
        for fid in sorted(used):
            base = fid.replace("_cur", "")
            if base not in cols:
                continue
            spec = specs[base]
            fields.append({"id": fid, "name": cols[base]["name"], "type": spec.type, "convention": spec.convention,
                           "date_order": spec.date_order, "currency": spec.currency or cols[base].get("currency")})
        payload = {
            "format": "kpi-bridge-recipe",
            "version": 1,
            "name": name.strip()[:120] or "Recette",
            "fields": fields,
            "plan": prow["plan"],
            "answers": prow["answers"],
            "layout": {"cards": [m["id"] for m in prow["plan"]["metrics"]], "charts": [c["id"] for c in prow["plan"]["charts"]]},
            "created_at": now(),
        }
        rid = new_id("rcp")
        self.db.run("INSERT INTO recipes (id, name, payload, created_at) VALUES (?, ?, ?, ?)", (rid, payload["name"], dumps(payload), now()))
        return {"id": rid, **payload}

    def list(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"]} for r in self.db.all("SELECT id, name, created_at FROM recipes ORDER BY created_at DESC")]

    def get(self, rid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM recipes WHERE id=?", (rid,))
        if row is None:
            raise NotFound("recipe")
        return {"id": row["id"], **loads(row["payload"], {})}

    def delete(self, rid: str) -> None:
        self.db.run("DELETE FROM recipes WHERE id=?", (rid,))

    def import_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("format") != "kpi-bridge-recipe" or payload.get("version") != 1:
            raise UserError("recipe_invalid")
        AnalysisPlan.model_validate(payload.get("plan"))
        clean: dict[str, Any] = {k: payload[k] for k in ("format", "version", "name", "fields", "plan", "answers", "layout") if k in payload}
        clean["name"] = str(clean.get("name", "Recette"))[:120]
        rid = new_id("rcp")
        self.db.run("INSERT INTO recipes (id, name, payload, created_at) VALUES (?, ?, ?, ?)", (rid, clean["name"], dumps(clean), now()))
        return {"id": rid, **clean}

    def check(self, rid: str, table_id: str) -> dict[str, Any]:
        """Compares the recipe with the new table: matches, missing or incompatible columns."""
        recipe = self.get(rid)
        cv = self.svc.current_version(table_id)
        new_cols = cv["profile"]["columns"]
        by_name = {_norm(c["name"]): c for c in new_cols}
        mapping: dict[str, str] = {}
        problems = []
        for f in recipe["fields"]:
            if f["id"].endswith("_cur"):
                continue
            cand = by_name.get(_norm(f["name"]))
            compatible = [c for c in new_cols if _compatible(f["type"], c["type"])]
            if cand is None:
                problems.append({"field": f["id"], "name": f["name"], "type": f["type"], "problem": "missing",
                                 "candidates": [{"id": c["id"], "name": c["name"], "type": c["type"]} for c in compatible][:20]})
            elif not _compatible(f["type"], cand["type"]):
                problems.append({"field": f["id"], "name": f["name"], "type": f["type"], "problem": "type_changed", "found_type": cand["type"],
                                 "candidates": [{"id": c["id"], "name": c["name"], "type": c["type"]} for c in compatible][:20]})
            else:
                mapping[f["id"]] = cand["id"]
                if f.get("currency") and cand.get("currency") and f["currency"] != cand["currency"]:
                    problems.append({"field": f["id"], "name": f["name"], "problem": "currency_changed", "from": f["currency"], "to": cand["currency"],
                                     "candidates": [{"id": cand["id"], "name": cand["name"], "type": cand["type"]}]})
                    mapping.pop(f["id"], None)
        return {"recipe": {"id": recipe["id"], "name": recipe["name"]}, "mapping": mapping, "problems": problems, "needs_remap": bool(problems)}

    def apply(self, rid: str, pid: str, table_id: str, explicit_mapping: dict[str, str] | None) -> dict[str, Any]:
        recipe = self.get(rid)
        report = self.check(rid, table_id)
        mapping = dict(report["mapping"])
        explicit = explicit_mapping or {}
        cv = self.svc.current_version(table_id)
        new_ids = {c["id"] for c in cv["profile"]["columns"]}
        for p in report["problems"]:
            target = explicit.get(p["field"])
            if not target:
                return {"status": "needs_remap", **report}
            if target not in new_ids:
                raise UserError("invalid_mapping", field=p["field"])
            mapping[p["field"]] = target
        # Applies the recipe's conventions / types to the mapped columns (new data version)
        cfg = cv["config"].model_copy(deep=True)
        changed = False
        for f in recipe["fields"]:
            if f["id"].endswith("_cur") or f["id"] not in mapping:
                continue
            for c in cfg.columns:
                if c.id == mapping[f["id"]]:
                    if f.get("convention") and c.convention != f["convention"]:
                        c.convention = f["convention"]
                        changed = True
                    if f.get("date_order") and c.date_order != f["date_order"]:
                        c.date_order = f["date_order"]
                        changed = True
                    if f["type"] == "money" and c.type == "number" and f.get("currency"):
                        c.type, c.currency = "money", f["currency"]
                        changed = True
                    cfg.confirmed[f"date_order:{c.id}"] = True if f.get("date_order") else cfg.confirmed.get(f"date_order:{c.id}")
                    cfg.confirmed[f"number_convention:{c.id}"] = True if f.get("convention") else cfg.confirmed.get(f"number_convention:{c.id}")
        cfg.confirmed = {k: v for k, v in cfg.confirmed.items() if v is not None}
        if changed or cfg.confirmed != cv["config"].confirmed:
            self.svc.update_config(table_id, cfg, None, "recipe_conventions", {"recipe": rid})
        # Translates the plan's field identifiers
        text = json.dumps(recipe["plan"])

        def repl(m: re.Match[str]) -> str:
            base, suffix = m.group(1), m.group(2) or ""
            new = mapping.get(base + suffix) or mapping.get(base)
            if new is None:
                raise UserError("invalid_mapping", field=base)
            return f'"{new}{suffix}"'

        new_plan = json.loads(FIELD_RE.sub(repl, text))
        # value filters (e.g. cancelled statuses) are kept as they are but revalidated
        plan_id = self.plans.from_plan_json(pid, table_id, new_plan, origin="recipe")
        return {"status": "plan_created", "plan_id": plan_id, "mapping": mapping}


def _compatible(old: str, new: str) -> bool:
    if old == new:
        return True
    numeric = {"number", "money", "percent"}
    if old in ("number", "money") and new in ("number", "money"):
        return True
    if old in ("text", "id") and new in ("text", "id"):
        return True
    return old in numeric and new in numeric and old != "percent" and new != "percent"
