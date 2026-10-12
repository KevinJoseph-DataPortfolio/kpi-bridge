# Architecture

```
Browser (React + ECharts, same origin)
   │  HTTP 127.0.0.1:8765, HttpOnly cookie, CSRF header
   ▼
FastAPI API  (kpibridge.app)  ──── deterministic in-memory DuckDB computation (engine/)
   │  SQLite: projects, versions, plans, jobs, result cache
   │  persistent job queue
   ▼
Supervisor worker (kpibridge.worker) ── 1 subprocess per job (cancellation, timeout, crash isolation)
   ├─ ingestion: format detection → dedicated parser → RawTable (text cells + provenance)
   ├─ normalisation: TableConfig → typed, versioned Parquet + quality profile
   └─ interpretation: local Ollama (qwen3:4b) → constrained JSON plan → validation
```

## Backend modules (`backend/kpibridge`)

| Module | Role |
|---|---|
| `config.py` | Settings and limits (`KPI_*`), no hard-coded secret |
| `db.py`, `migrations/` | SQLite + versioned migrations (`schema_migrations`) |
| `security.py`, `session.py` | Host/Origin/CSRF checks, CSP, sessions, single-use bootstrap token |
| `storage.py` | Private per-project directories, confined paths, deletion, temp-file sweep |
| `ingest/` | `detect` (allow-list + real content + ZIP checks), `csvtab`, `excel` (XLSX/XLS), `pdf` (text + OCR), `ocr` (Tesseract + grid reconstruction), `docs` (DOCX/TXT/JSON), `text` ("label: number" values with evidence), `tables` (blocks, header, titles, totals) |
| `normalize/` | `parse` (number/date conventions, currencies), `roles` (multilingual column roles), `TableConfig` application and profile |
| `plan/` | `grammar` (closed grammar), `validate`, `llm_schema` (model/builder format), `llm` (Ollama, test substitute), `interpret` (prompt, bounded repair, structural normalisation), `suggest`, `clarify` |
| `engine/` | `store` (locked-down DuckDB), `compute` (parameterised SQL compilation, decimal evaluation, currencies, grain, period-over-period change, evidence) |
| `plans.py` | Lifecycle: request → plan → clarification → validation → dashboard (versioned cache) |
| `services.py` | Projects, files, tables, versions, previews |
| `joins.py` | Controlled many-to-one join |
| `recipes.py` | Reusable recipes and column remapping |
| `exports/` | PDF (ReportLab, embedded Noto fonts), PNG (PDFium rendering), neutralised CSV |
| `jobs.py`, `worker.py`, `handlers.py` | Persistent queue, supervisor, handlers |
| `i18n.py` | Server-side translations from `shared/locales` |

## Canonical model
`Project` (projects) · `SourceFile` (source_files, original kept read-only) · `ExtractedTable` (extracted_tables +
raw Parquet `raw/<id>.parquet`, text cells and a `_src` column) · `ColumnProfile` (JSON profile of the version) ·
`SourceReference` (per-row `_src` column: `rec=;line=`, `sheet=;row=`, `page=;bbox=`, `line=;chars=`) ·
`Transformation` (transformations) · `DatasetVersion` (dataset_versions + typed Parquet `ds/<id>_v<n>.parquet`, full
reversible config) · `AnalysisRequest` · `AnalysisPlan` (validated JSON plan, questions, answers, origin) ·
`MetricDefinition` (grammar metric) · `MetricResult` (computed cards, `result_cache` keyed by plan + version +
filters) · `DashboardSpec` (charts, filter dimensions, recipe layout) · `Job` (jobs).

Every typed row keeps `_row` (raw index) and `_src` (provenance). The evidence for a figure is the reproducible
query (filters + aggregate) that retrieves the contributing rows; no row list is stored in the cards.

## Design decisions and deviations from the original brief
- **pandas is not used**: parsers → text cells → controlled typing (Decimal) → Arrow/Parquet. This avoids implicit
  float conversions.
- **PyMuPDF excluded** (AGPL): pdfplumber for text, pypdfium2 for rendering.
- **PNG export** = page 1 of the PDF rendered by PDFium: one layout engine and the same fonts for both formats.
- **DuckDB reads Arrow tables** loaded by pyarrow, then external access is disabled and the configuration is
  locked: the computation engine cannot reach any file.
- **Launcher in Python** (`scripts/kpictl.py`): no dependency on PowerShell execution policy;
  `launch.cmd` / `launch.sh` only locate Python ≥ 3.12.
- **Tesseract on Windows without admin rights**: the UB-Mannheim installer requires elevation; the launcher uses
  micromamba + conda-forge in user mode.
- **Docker Compose**: written and documented as the reference path, but not executed on the development machine
  (see [TEST_REPORT](TEST_REPORT.md)).

## API (`/api/v1`)
Session: `POST /session/bootstrap`, `GET /session`, `POST /session/logout` · Meta: `GET /meta`, `GET /health` ·
Projects: `GET|POST /projects`, `GET|PATCH|DELETE /projects/{id}` · Files: `PUT /projects/{id}/files` (raw
stream, `X-File-Name` header), `POST /projects/{id}/examples/{name}`, `POST .../files/{fid}/reingest`,
`DELETE .../files/{fid}` · Jobs: `GET /jobs/{id}`, `POST /jobs/{id}/cancel` · Tables: `.../tables/{tid}/preview|raw|config|header|revert/{v}|cells/{r}/{c}[/image]|suggestions` ·
Joins: `POST .../joins/check`, `POST .../joins` · Plans: `POST .../requests`, `POST .../builder`,
`GET|PUT .../plans/{pid}`, `POST .../plans/{pid}/answers|validate|dashboard|detail|evidence/{metric}|export` ·
Recipes: `GET /recipes`, `GET|DELETE /recipes/{id}`, `POST /recipes/import`, `POST .../recipes`,
`POST .../recipes/{rid}/check|apply`.
