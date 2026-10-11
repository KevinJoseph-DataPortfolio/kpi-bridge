"""API HTTP versionnée (/api/v1) et service du frontend compilé sous la même origine."""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.parse
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, jobs
from .config import Settings, ensure_dirs, get_settings
from .db import Database, migrate
from .engine.compute import UserFilters
from .errors import NotFound, UserError
from .exports import render_csv, render_pdf, render_png
from .ingest.detect import SUPPORTED
from .joins import analyse_join, create_join
from .normalize import TableConfig
from .plan.llm import get_provider
from .plans import SUPPORTED_LANGS, Plans
from .recipes import Recipes
from .security import COOKIE, SecurityMiddleware, consume_bootstrap_token, create_session, revoke_session, session_valid, set_session_cookie
from .services import Services
from .storage import check_id
from .worker import worker_alive

log = logging.getLogger("kpibridge.api")


class Ctx:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.svc = Services(settings, self.db)
        self.plans = Plans(self.svc)
        self.recipes = Recipes(self.svc, self.plans)


# ---------------------------------------------------------------------- modèles de requête
class ProjectIn(BaseModel):
    name: str = Field(default="Projet", max_length=120)
    ui_lang: str = "fr"


class RequestIn(BaseModel):
    table_id: str
    text: str = Field(default="", max_length=4000)
    suggestion_ids: list[str] = Field(default_factory=list, max_length=20)
    ui_lang: str = "fr"


class AnswersIn(BaseModel):
    answers: dict[str, str]


class FiltersIn(BaseModel):
    filters: dict[str, Any] | None = None
    offset: int = 0


class ExportIn(BaseModel):
    format: str
    filters: dict[str, Any] | None = None
    lang: str = "fr"


class HeaderIn(BaseModel):
    header_row: int
    base_version: int | None = None


class ConfigIn(BaseModel):
    config: TableConfig
    base_version: int | None = None
    op: str = Field(default="edit", max_length=40)


class ReingestIn(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)
    ui_lang: str = "fr"


class JoinIn(BaseModel):
    left_table: str
    left_key: str
    right_table: str
    right_key: str
    right_columns: list[str] = Field(default_factory=list, max_length=30)


class RecipeIn(BaseModel):
    plan_id: str
    name: str = Field(default="Recette", max_length=120)


class ApplyRecipeIn(BaseModel):
    table_id: str
    mapping: dict[str, str] | None = None


def _ids(*values: str) -> None:
    for v in values:
        try:
            check_id(v)
        except ValueError as exc:
            raise NotFound("resource") from exc


def _project_of_table(ctx: Ctx, pid: str, tid: str) -> None:
    _ids(pid, tid)
    if ctx.svc.table_row(tid)["project_id"] != pid:
        raise NotFound("table")


def _ascii_name(name: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:60].strip("_") or "kpi-bridge"
    return f"{base}.{ext}"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    ensure_dirs(settings)
    migrate(settings)
    ctx = Ctx(settings)
    app = FastAPI(title="KPI Bridge", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, settings=settings, db=ctx.db)
    app.state.ctx = ctx

    @app.exception_handler(UserError)
    async def _user_error(_req: Request, exc: UserError) -> JSONResponse:
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": {"code": "invalid_request", "params": {"count": len(exc.errors())}}}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": {"code": "not_found" if exc.status_code == 404 else "http_error", "params": {}}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _internal(_req: Request, exc: Exception) -> JSONResponse:
        log.error("erreur interne : %s", type(exc).__name__)
        return JSONResponse({"error": {"code": "internal_error", "params": {}}}, status_code=500)

    api = "/api/v1"

    # ------------------------------------------------------------------ session & méta
    @app.get(f"{api}/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "worker": worker_alive(settings.run_dir)}

    @app.get(f"{api}/session")
    def session_status(request: Request) -> dict[str, Any]:
        return {"authenticated": session_valid(ctx.db, request.cookies.get(COOKIE))}

    @app.post(f"{api}/session/bootstrap")
    def bootstrap(body: dict[str, str] = Body(...)) -> Response:
        token = str(body.get("token", ""))
        if not consume_bootstrap_token(settings, token):
            raise UserError("bootstrap_invalid", status_code=401)
        session = create_session(ctx.db, settings)
        resp = JSONResponse({"authenticated": True})
        set_session_cookie(resp, session, settings)
        return resp

    @app.post(f"{api}/session/logout")
    def logout(request: Request) -> Response:
        revoke_session(ctx.db, request.cookies.get(COOKIE))
        resp = JSONResponse({"authenticated": False})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get(f"{api}/meta")
    def meta() -> dict[str, Any]:
        return {
            "version": __version__,
            "languages": list(SUPPORTED_LANGS),
            "formats": sorted(SUPPORTED),
            "limits": settings.limits.as_public(),
            "ai": get_provider(settings).status(),
            "ocr": {"available": bool(settings.tesseract_cmd)},
            "worker": worker_alive(settings.run_dir),
            "examples": ctx.svc.list_examples(),
        }

    # ------------------------------------------------------------------ projets
    @app.get(f"{api}/projects")
    def projects() -> list[dict[str, Any]]:
        return ctx.svc.list_projects()

    @app.post(f"{api}/projects")
    def create_project(body: ProjectIn) -> dict[str, Any]:
        return ctx.svc.create_project(body.name, body.ui_lang if body.ui_lang in SUPPORTED_LANGS else "fr")

    @app.get(f"{api}/projects/{{pid}}")
    def project(pid: str) -> dict[str, Any]:
        _ids(pid)
        proj = ctx.svc.get_project(pid)
        files = [
            {"id": r["id"], "name": r["original_name"], "size": r["size"], "ext": r["ext"], "status": r["status"],
             "error": {"code": r["error_code"], "params": json.loads(r["error_params"] or "{}")} if r["error_code"] else None,
             "options": json.loads(r["options"] or "{}"), "format": r["detected_format"]}
            for r in ctx.db.all("SELECT * FROM source_files WHERE project_id=? ORDER BY created_at", (pid,))
        ]
        active = [jobs.public(dict(r)) for r in ctx.db.all("SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running') ORDER BY created_at", (pid,))]
        return {**proj, "files": files, "tables": ctx.svc.tables_overview(pid), "active_jobs": active,
                "plans": [{"id": p["id"], "status": p["status"], "origin": p["origin"], "table_id": p["table_id"], "updated_at": p["updated_at"],
                           "request": p["request"]} for p in ctx.plans.list_for_project(pid)][:20]}

    @app.patch(f"{api}/projects/{{pid}}")
    def rename(pid: str, body: ProjectIn) -> dict[str, Any]:
        _ids(pid)
        ctx.svc.rename_project(pid, body.name)
        return ctx.svc.get_project(pid)

    @app.delete(f"{api}/projects/{{pid}}")
    def delete_project(pid: str) -> dict[str, Any]:
        _ids(pid)
        ctx.svc.delete_project(pid)
        return {"deleted": True}

    @app.put(f"{api}/projects/{{pid}}/files")
    async def upload(pid: str, request: Request) -> dict[str, Any]:
        _ids(pid)
        raw_name = request.headers.get("x-file-name", "")
        filename = urllib.parse.unquote(raw_name)[:200]
        ui_lang = request.headers.get("x-ui-lang", "fr")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.limits.max_file_bytes:
            raise UserError("file_too_large", status_code=413, limit_mib=settings.limits.max_file_bytes // (1024 * 1024))
        # Lecture en flux : arrêt dès que la limite est dépassée (pas de stockage complet préalable).
        buffer: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > settings.limits.max_file_bytes:
                raise UserError("file_too_large", status_code=413, limit_mib=settings.limits.max_file_bytes // (1024 * 1024))
            buffer.append(chunk)
        info = ctx.svc.save_upload(pid, filename, iter(buffer))
        job_id = ctx.svc.enqueue_ingest(pid, info["id"], ui_lang if ui_lang in SUPPORTED_LANGS else "fr")
        return {"file": info, "job_id": job_id}

    @app.post(f"{api}/projects/{{pid}}/examples/{{name}}")
    def example(pid: str, name: str, ui_lang: str = "fr") -> dict[str, Any]:
        _ids(pid)
        info = ctx.svc.add_example(pid, name)
        job_id = ctx.svc.enqueue_ingest(pid, info["id"], ui_lang if ui_lang in SUPPORTED_LANGS else "fr")
        return {"file": info, "job_id": job_id}

    @app.post(f"{api}/projects/{{pid}}/files/{{fid}}/reingest")
    def reingest(pid: str, fid: str, body: ReingestIn) -> dict[str, Any]:
        _ids(pid, fid)
        return {"job_id": ctx.svc.reingest(pid, fid, body.options, body.ui_lang)}

    @app.delete(f"{api}/projects/{{pid}}/files/{{fid}}")
    def delete_file(pid: str, fid: str) -> dict[str, Any]:
        _ids(pid, fid)
        ctx.svc.delete_file(pid, fid)
        return {"deleted": True}

    # ------------------------------------------------------------------ tâches
    @app.get(f"{api}/jobs/{{job_id}}")
    def job(job_id: str) -> dict[str, Any]:
        _ids(job_id)
        j = jobs.get(ctx.db, job_id)
        if j is None:
            raise NotFound("job")
        return j

    @app.post(f"{api}/jobs/{{job_id}}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        _ids(job_id)
        return {"cancel_requested": jobs.request_cancel(ctx.db, job_id)}

    # ------------------------------------------------------------------ tables
    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/preview")
    def preview(pid: str, tid: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        return ctx.svc.preview(tid, offset, limit)

    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/raw")
    def raw(pid: str, tid: str, offset: int = 0, limit: int = 60) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        return ctx.svc.raw_view(tid, offset, limit)

    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/config")
    def get_config(pid: str, tid: str) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        cv = ctx.svc.current_version(tid)
        return {"version": cv["version"], "config": cv["config"].model_dump(), "profile": cv["profile"], "versions": ctx.svc.versions(tid)}

    @app.put(f"{api}/projects/{{pid}}/tables/{{tid}}/config")
    def put_config(pid: str, tid: str, body: ConfigIn) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        v = ctx.svc.update_config(tid, body.config, body.base_version, re.sub(r"[^a-z_:]", "", body.op)[:40] or "edit", {})
        return {"version": v}

    @app.post(f"{api}/projects/{{pid}}/tables/{{tid}}/header")
    def header(pid: str, tid: str, body: HeaderIn) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        return {"version": ctx.svc.set_header_row(tid, body.header_row, body.base_version)}

    @app.post(f"{api}/projects/{{pid}}/tables/{{tid}}/revert/{{version}}")
    def revert(pid: str, tid: str, version: int) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        return {"version": ctx.svc.revert(tid, version)}

    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/cells/{{r}}/{{c}}")
    def cell_source(pid: str, tid: str, r: int, c: int) -> dict[str, Any]:
        _project_of_table(ctx, pid, tid)
        img, info = ctx.svc.cell_source(tid, r, c)
        return {**info, "has_image": img is not None}

    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/cells/{{r}}/{{c}}/image")
    def cell_image(pid: str, tid: str, r: int, c: int) -> Response:
        from PIL import Image

        _project_of_table(ctx, pid, tid)
        img_path, info = ctx.svc.cell_source(tid, r, c)
        if img_path is None or not info.get("meta"):
            raise NotFound("image")
        x0, y0, x1, y1 = info["meta"]["bbox"]
        with Image.open(img_path) as im:
            pad = 40
            crop = im.crop((max(0, x0 - pad * 4), max(0, y0 - pad), min(im.width, x1 + pad * 4), min(im.height, y1 + pad)))
            out = io.BytesIO()
            crop.save(out, format="PNG")
        return Response(out.getvalue(), media_type="image/png")

    @app.get(f"{api}/projects/{{pid}}/tables/{{tid}}/suggestions")
    def suggest(pid: str, tid: str, lang: str = "fr") -> list[dict[str, Any]]:
        _project_of_table(ctx, pid, tid)
        return ctx.plans.suggestions(tid, lang if lang in SUPPORTED_LANGS else "fr")

    # ------------------------------------------------------------------ jointures
    @app.post(f"{api}/projects/{{pid}}/joins/check")
    def join_check(pid: str, body: JoinIn) -> dict[str, Any]:
        _project_of_table(ctx, pid, body.left_table)
        _project_of_table(ctx, pid, body.right_table)
        return analyse_join(ctx.svc, body.left_table, body.left_key, body.right_table, body.right_key)

    @app.post(f"{api}/projects/{{pid}}/joins")
    def join_create(pid: str, body: JoinIn) -> dict[str, Any]:
        _project_of_table(ctx, pid, body.left_table)
        _project_of_table(ctx, pid, body.right_table)
        return create_join(ctx.svc, pid, body.left_table, body.left_key, body.right_table, body.right_key, body.right_columns)

    # ------------------------------------------------------------------ demandes & plans
    @app.post(f"{api}/projects/{{pid}}/requests")
    def create_request(pid: str, body: RequestIn) -> dict[str, Any]:
        _project_of_table(ctx, pid, body.table_id)
        return ctx.plans.create_request(pid, body.table_id, body.text, body.suggestion_ids, body.ui_lang)

    @app.post(f"{api}/projects/{{pid}}/builder")
    def builder(pid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        tid = str(body.get("table_id", ""))
        _project_of_table(ctx, pid, tid)
        replace = body.get("replace_plan")
        if replace:
            _ids(str(replace))
        return ctx.plans.builder(pid, tid, body.get("spec") or {}, str(body.get("ui_lang", "fr")), replace_plan=replace)

    def _plan_in_project(pid: str, plan_id: str) -> dict[str, Any]:
        _ids(pid, plan_id)
        p = ctx.plans.get(plan_id)
        if p["project_id"] != pid:
            raise NotFound("plan")
        return p

    @app.get(f"{api}/projects/{{pid}}/plans/{{plan_id}}")
    def get_plan(pid: str, plan_id: str) -> dict[str, Any]:
        return _plan_in_project(pid, plan_id)

    @app.put(f"{api}/projects/{{pid}}/plans/{{plan_id}}")
    def put_plan(pid: str, plan_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        return ctx.plans.update_plan(plan_id, body.get("plan") or {})

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/answers")
    def answers(pid: str, plan_id: str, body: AnswersIn) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        return ctx.plans.answer(plan_id, body.answers)

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/validate")
    def validate(pid: str, plan_id: str) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        return ctx.plans.validate(plan_id)

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/dashboard")
    def dashboard(pid: str, plan_id: str, body: FiltersIn) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        ctx.svc.update_project_state(pid, {"last_filters": body.filters or {}})
        return ctx.plans.dashboard(plan_id, body.filters)

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/detail")
    def detail(pid: str, plan_id: str, body: FiltersIn) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        eng, _p, _cv = ctx.plans.engine(plan_id)
        user = UserFilters.from_dict(body.filters)
        ctx.plans._check_dims(user, eng)
        return eng.detail(user, offset=max(0, body.offset))

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/evidence/{{metric_id}}")
    def evidence(pid: str, plan_id: str, metric_id: str, body: FiltersIn) -> dict[str, Any]:
        _plan_in_project(pid, plan_id)
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", metric_id):
            raise NotFound("metric")
        return ctx.plans.evidence(plan_id, metric_id, body.filters, max(0, body.offset))

    @app.post(f"{api}/projects/{{pid}}/plans/{{plan_id}}/export")
    def export(pid: str, plan_id: str, body: ExportIn) -> Response:
        _plan_in_project(pid, plan_id)
        lang = body.lang if body.lang in SUPPORTED_LANGS else "fr"
        proj = ctx.svc.get_project(pid)
        if body.format in ("pdf", "png"):
            payload = ctx.plans.dashboard(plan_id, body.filters)
            pdf = render_pdf(payload, lang, proj["name"])
            if body.format == "pdf":
                return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{_ascii_name(proj["name"], "pdf")}"'})
            return Response(render_png(pdf), media_type="image/png", headers={"Content-Disposition": f'attachment; filename="{_ascii_name(proj["name"], "png")}"'})
        if body.format == "csv":
            eng, _p, _cv = ctx.plans.engine(plan_id)
            user = UserFilters.from_dict(body.filters)
            ctx.plans._check_dims(user, eng)
            _cols, labels, rows = eng.all_rows(user)
            return Response(render_csv(labels, rows, lang), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{_ascii_name(proj["name"], "csv")}"'})
        raise UserError("unsupported_export", format=body.format[:10])

    # ------------------------------------------------------------------ recettes
    @app.get(f"{api}/recipes")
    def recipes() -> list[dict[str, Any]]:
        return ctx.recipes.list()

    @app.post(f"{api}/projects/{{pid}}/recipes")
    def save_recipe(pid: str, body: RecipeIn) -> dict[str, Any]:
        _plan_in_project(pid, body.plan_id)
        return ctx.recipes.save(body.plan_id, body.name)

    @app.get(f"{api}/recipes/{{rid}}")
    def get_recipe(rid: str) -> Response:
        _ids(rid)
        data = ctx.recipes.get(rid)
        return Response(json.dumps(data, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{_ascii_name(data["name"], "kpirecipe.json")}"'})

    @app.post(f"{api}/recipes/import")
    def import_recipe(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return ctx.recipes.import_payload(body)

    @app.delete(f"{api}/recipes/{{rid}}")
    def delete_recipe(rid: str) -> dict[str, Any]:
        _ids(rid)
        ctx.recipes.delete(rid)
        return {"deleted": True}

    @app.post(f"{api}/projects/{{pid}}/recipes/{{rid}}/check")
    def check_recipe(pid: str, rid: str, body: ApplyRecipeIn) -> dict[str, Any]:
        _ids(rid)
        _project_of_table(ctx, pid, body.table_id)
        return ctx.recipes.check(rid, body.table_id)

    @app.post(f"{api}/projects/{{pid}}/recipes/{{rid}}/apply")
    def apply_recipe(pid: str, rid: str, body: ApplyRecipeIn) -> dict[str, Any]:
        _ids(rid)
        _project_of_table(ctx, pid, body.table_id)
        return ctx.recipes.apply(rid, pid, body.table_id, body.mapping)

    # ------------------------------------------------------------------ frontend compilé
    static_dir = settings.static_dir
    if static_dir and static_dir.is_dir():
        index = static_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith("api/"):
                raise NotFound("route")
            candidate = (static_dir / path).resolve() if path else index
            if path and static_dir.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-store"})

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False, log_level="info",
                proxy_headers=False, server_header=False)


if __name__ == "__main__":
    main()


