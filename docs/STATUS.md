# Project status (2026-10-12)

Legend: [x] done and verified · [~] done, partial verification · [!] not done or not verifiable (cause given)

## Environment
- [x] Development machine: Windows 11, Python 3.14, RTX 3050 6 GB, 15 GB RAM, no admin rights, no WSL
- [x] User-mode tools in `~/.kpi-bridge/tools`: Node 24.21.0, gh 2.102.0, Ollama 0.40.3, Tesseract 5.5.3, qwen3:4b

## Product
- [x] Ingestion of CSV/TSV/TXT/XLSX/XLS/text PDF/scanned PDF/PNG/JPG/DOCX/JSON, limits enforced during processing
- [x] Structure: titles, headers, multiple tables, merged cells, totals, repeated headers, per-row provenance
- [x] Versioned, reversible normalisation, quality profile, cell corrections (OCR with source region)
- [x] Closed grammar, validation, locked-down DuckDB engine, tested KPI rules
- [x] Local AI (Ollama) with bounded repair, guided-builder fallback, deterministic suggestions, clarifications
- [x] Dashboard, filters, evidence, PDF/PNG/CSV exports, recipes + remapping, many-to-one join
- [x] 6 languages (503 keys), Cyrillic and Chinese exports

## Security
- [x] Single-use session, HttpOnly/SameSite cookie, Host/Origin/CSRF, CSP, CSV neutralisation, ZIP/MIME checks, deletion
- [~] Worker isolation: subprocess + timeouts (native); hardened Docker written but not executed

## Delivery
- [x] Tests: 55 backend, 10 Playwright (AI substitute), 6 Playwright with the real model, evaluation 6/6, benchmark
- [x] `launch` / `setup` / `kpi` scripts (stop, status, doctor, update, backup, uninstall), tested on Windows
- [x] Documentation, licences, CI workflow, repository published on GitHub
- [!] Docker Compose: not executed on the development machine (no Docker/WSL/admin); the image build runs in CI
- [!] Linux/macOS: native launch not tested by hand; backend tests run on Ubuntu in CI
