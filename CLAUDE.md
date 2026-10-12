# KPI Bridge: project memory for Claude Code

Source of the requirements: the "KPI Bridge: full mission" brief (condensed copy here).
This file keeps requirements, decisions and constraints across sessions. Progress is tracked in `docs/STATUS.md`.

## Product
Document → review → "What do you want to know?" → clarifications (1 to 3 questions max per step) → interactive
dashboard (KPI cards, charts, filters, table). No narrative summary. Every figure is verifiable ("Check this figure").
Local single-user tool. Six UI languages: fr (default), en, ru, zh-Hans, de, es.

## Non-negotiable rules
- The AI (Ollama, `qwen3:4b` by default) proposes a JSON plan; **the deterministic engine computes**. No free-form SQL/Python.
- Closed computation grammar (`backend/kpibridge/plan/grammar.py`), validated against the real fields.
- Exact decimals (DuckDB DECIMAL / `decimal.Decimal`), rounding only at display time.
- Missing value ≠ 0; division by zero → unavailable + reason; mixed currencies → separate results.
- Average basket = sum / distinct order count. Global rate = Σnumerator / Σdenominator.
- No automatic duplicate removal; detected subtotals are excluded visibly and reversibly.
- Joins only many-to-one with a verified unique key and a sums check before/after.
- Excel formulas: cached value only, otherwise flagged "unavailable".
- Every transformation is recorded and reversible; a correction creates a new `DatasetVersion`.
- Security: 127.0.0.1 only, session via a single-use token created by the launcher, HttpOnly SameSite=Strict cookie,
  Host/Origin check + CSRF header, strict CSP, DuckDB without external access or extensions.

## Technical decisions (and deviations from the brief)
- Backend Python 3.12+ (tested on 3.14 under Windows), FastAPI, Pydantic v2, DuckDB, pyarrow (immutable Parquet per version).
- No pandas: parsers produce text cells; typing is done by `normalize.py`.
- PDF: pdfplumber (text), pypdfium2 (raster rendering), Tesseract through pytesseract (OCR). PyMuPDF excluded (AGPL).
- PDF export: ReportLab + embedded Noto fonts (OFL). PNG export: page 1 of the PDF rendered with pdfium (same code, same fonts).
- Separate worker (`python -m kpibridge.worker`) with a SQLite job queue (no Redis).
- Frontend React + TypeScript + Vite, ECharts, i18next. Locales shared in `shared/locales/*.json` (frontend + exports).
- Reference dev environment: Windows 11 without admin rights, without WSL ⇒ Docker cannot be tested there.
  Tools installed in user mode under `%USERPROFILE%\.kpi-bridge\tools` (portable Node, Ollama, Tesseract via micromamba/conda-forge, gh).
  Note: the Claude app (MSIX) virtualises `%LOCALAPPDATA%`; do not install there anything the user must see.

## Useful commands
- Backend tests: `.venv\Scripts\python -m pytest -q backend/tests`
- Lint/types: `.venv\Scripts\ruff check backend`; `.venv\Scripts\mypy backend/kpibridge`
- Frontend: `cd frontend; npm ci; npm run build; npm run test:e2e`
- Native launch: `launch.cmd` (Windows) or `./launch.sh`
