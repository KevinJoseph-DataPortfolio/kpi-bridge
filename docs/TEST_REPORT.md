# Test report: 2026-10-11

Execution environment: Windows 11 Home, AMD Ryzen, 15.3 GB RAM, NVIDIA RTX 3050 6 GB (laptop), Python 3.14.7,
Node 24.21.0, Tesseract 5.5.3 (conda-forge), Ollama 0.40.3, model `qwen3:4b`. No administrator rights, no WSL,
no Docker.

Every value below was **executed and measured**. What could not be run is listed at the end.

## 1. Summary

| Suite | Command | Result |
|---|---|---|
| Backend tests (unit, ingestion, KPI rules, API, security, real worker, exports, i18n) | `cd backend && pytest` | **55 passed / 55** (≈ 30 s) |
| Python lint | `ruff check backend scripts` | no error |
| Python types | `mypy kpibridge` | no error (40 files) |
| Frontend lint / types | `eslint src e2e` · `tsc -b` | no error |
| Browser, AI substitute (same as CI) | `npx playwright test journey` | **10 passed / 10** (6 journeys in the 6 languages + 2 mobile/session tests × 2 profiles), 1.1 min |
| Browser, **real local model** | `KPI_LLM_PROVIDER=ollama npx playwright test -g parcours --project=desktop` | **6 passed / 6** in 2.6 min; all 6 plans have origin `ai:ollama` (no fallback) |
| Real-model evaluation (outside CI) | `python scripts/eval_llm.py` | **6 valid plans / 6**, 1 attempt each, 13.9 to 18.3 s; all semantic checks passed (`docs/llm_eval.json`) |
| Python audit | `pip-audit -r requirements.lock --require-hashes --strict` | no known vulnerability |
| npm audit | `npm audit --audit-level=high` | 0 vulnerabilities |
| Licences | `scripts/check_licenses.py` | no incompatible licence |
| Clean install | fresh clone + `launch.cmd --no-browser` (port 8790, fresh data) | succeeded in **40.9 s** (tools already present on the machine) |
| Second launch | `launch.cmd --no-browser` | reused, **1.0 s** |
| Update | `kpi update` on the clone | succeeded, 11.4 s |
| Operations | `kpi stop / status / doctor / backup / uninstall` | succeeded; `uninstall` keeps the data unless `--purge-data` is passed |

## 2. Acceptance criteria

| Case | Test(s) | Result |
|---|---|---|
| Clean sales CSV: exact KPIs, filters and exports agree | `test_clean_csv_exact_kpis`, `test_global_filter_applies_to_cards_charts_and_table`, browser journey | ✅ (15.30 exact; filter applied to cards, charts, table and CSV) |
| French CSV with `;`, `,` decimals, leading-zero identifiers | `test_french_csv_semicolon_decimals_leading_zero_ids` | ✅ (`00042` kept, `1 234,50` → 1234.50, Total row excluded) |
| Dates like `03/04/2026` with no reliable convention | `test_ambiguous_dates_require_clarification`, `test_date_order_ambiguous_until_resolved` | ✅ question asked before any time-based KPI; MDY → 3 March |
| Excel: titles, merged cells, several sheets, subtotals | `test_excel_titles_merged_multisheet_subtotals` | ✅ 2 tables, total 190.5 (subtotal and grand total not added), merge applied |
| Old XLS, and XLSX with an uncached formula | `test_old_xls_is_usable`, `test_xlsx_formula_without_cache_is_unavailable` | ✅ XLS = 30.75; uncached formulas flagged, KPI "unavailable" |
| Multi-page text PDF | `test_text_pdf_multipage_repeated_headers_and_provenance` | ✅ 120 rows, repeated headers excluded, Σ = 7320.00, provenance `page=1;bbox=…` |
| Readable scan, then degraded scan | `test_scanned_pdf_ocr_readable`, `test_degraded_scan_never_fabricates`, `test_tabular_image` | ✅ real OCR = 3540; degraded scan: explicit refusal or flagged cells, no invented figure |
| DOCX table, text without data, JSON, tabular image | `test_docx_table_text_without_data_json_supported`, `test_tabular_image` | ✅ DOCX = 42; empty text → `no_data_found_text`; nested JSON = 3.75; unsupported JSON → targeted message |
| Multi-line orders: average basket | `test_average_basket_uses_distinct_orders_not_rows` | ✅ 250 / 3 orders (not / 5 rows); 102.5 with cancellations excluded |
| Rate with different denominators | `test_weighted_rate_not_average_of_rates` | ✅ 15 / 1010 = 1.485 % (not 25.5 %); sum/average of rates rejected |
| Missing costs, zero denominator, missing month, mixed currencies | `test_missing_costs_zero_denominator_missing_month_mixed_currencies` | ✅ unavailable (`no_values`, `division_by_zero`), gap in February, EUR 125 / USD 50 kept separate |
| Join that duplicates an amount | `test_join_blocked_when_duplicating_amounts` | ✅ blocked; safe join keeps Σ = 35 |
| `<script>` cells, CSV formula, exfiltration instruction | `test_full_flow_with_malicious_cells_and_safe_exports`, `test_model_cannot_inject_code_or_numbers` | ✅ escaped plain text, formulas prefixed with `'`, `-5.5` intact, total 24.5 (not 999999) |
| Fake MIME, traversal, zip bomb, oversized file | `test_fake_mime_and_refused_types`, `test_zip_traversal_bomb_and_vba_rejected_before_extraction`, `test_oversized_file_stopped_during_streaming`, `test_upload_limit_via_http`, `test_row_limit_enforced_during_parse` | ✅ rejected before extraction / bounded stop |
| API access without a session, disallowed origin | `test_session_required_and_single_use_bootstrap`, `test_host_origin_csrf_and_headers`, `test_cookie_flags`, browser test "page without session" | ✅ 401 / 403; single-use token; HttpOnly SameSite=Strict cookie |
| Cancellation, worker crash, restart | `test_cancel_running_job`, `test_crash_is_retried_then_failed_and_timeout_enforced`, `test_supervisor_crash_recovery_on_restart` | ✅ (real subprocess worker) cancellation, 2 attempts then failure, timeout, recovery after a hard stop, temp files swept |
| Project deletion | `test_delete_project_removes_everything` | ✅ files and SQLite rows removed, 404 afterwards |
| Refresh with a changed schema | `test_recipe_refresh_requires_remap_and_recomputes` | ✅ remapping requested, result recomputed (7, not the old 15) |
| Correction and no stale figure | `test_correction_invalidates_results_no_stale_figure` | ✅ new version, cache invalidated, 409 version conflict, rollback |
| Journey in all six languages | browser journey ×6, `test_key_parity_and_placeholders`, `test_frontend_static_keys_exist`, `test_error_codes_raised_in_backend_are_translated`, `test_pdf_export_glyphs_pagination_and_filters[×6]` | ✅ 503 keys × 6 languages; request handled by the **real model** in the 6 languages; paginated PDF exports with Cyrillic and Chinese, no missing glyph |
| Fresh install, second launch, offline | § 1 and § 5 | ✅ install and reuse; offline **simulated** (see § 6) |

## 3. Browser tests (Playwright, Chromium)
Journey checked in every language: session link removed from the URL → language choice → synthetic example →
"Understand" screen → free-text request → interpretation → answers to clarifying questions → plan validation →
dashboard (non-empty value, no `NaN`) → category filter (scope changed) → "Check this figure" → non-empty CSV,
PDF and PNG exports → no raw translation key displayed. Additional tests: page without a session (401), mobile
dashboard (Pixel 7) with no horizontal scrolling.
- AI substitute (CI configuration): **10/10** (6 journeys, mobile and no-session tests on 2 profiles).
- Real `qwen3:4b` model: **6/6**, plan origin `ai:ollama`, no fallback.

Defects found by these tests and fixed: button rows not wrapping on mobile; a display race for the questions in one
test; identical chart titles.

## 4. Real local model evaluation (`docs/llm_eval.json`)
Request "monthly sales, average basket, top five products and margin" on the real profile of the example:

| Language | Valid plan | Attempts | Duration | Checks (sum of amounts, basket = distinct orders, no per-row ratio, top 5 products, monthly curve, margin = difference) |
|---|---|---|---|---|
| fr | yes | 1 | 18.3 s | 6/6 |
| en | yes | 1 | 16.2 s | 6/6 |
| ru | yes | 1 | 18.1 s | 6/6 |
| zh-Hans | yes | 1 | 14.4 s | 6/6 |
| de | yes | 1 | 13.9 s | 6/6 |
| es | yes | 1 | 14.2 s | 6/6 |

Honest history: the first version scored 0/6 (the schema let the model omit `metrics`), then 4/6 (it wrongly filled
the grain field). Fixes: a generation schema with required properties and no grain field, a guided example, repair
hints, a safe structural normalisation of the period, and a real global deadline (one call had lasted 1319 s despite
the read timeout). The model can still be wrong on other requests: the plan is always displayed and must be
validated.

## 5. Performance measurements (`docs/benchmark.json`, `scripts/benchmark.py --ai`)

| Measure | Value | Target |
|---|---|---|
| API start-up (process → health OK) | 3.8 – 4.4 s | n/a |
| Import + normalisation of a 50,000-row CSV (1.9 MB) | 4.9 – 5.4 s | n/a |
| First dashboard computation (3 KPIs incl. period change, 2 charts, table) | 0.23 – 0.27 s | n/a |
| Applying a filter (16 distinct combinations, no cache) | median 0.19 – 0.21 s, max 0.21 s | < 1 s: **met** |
| AI interpretation (`qwen3:4b`, 100 % GPU) | 11.8 – 18.9 s | n/a |
| Peak memory of the import + compute process | 172 MiB | no saturation: **observed** |
| GPU memory used by the model | 3.2 GB (of 6 GB) | n/a |
| Free RAM during the measurements (other applications open) | 3.7 GB of 15.3 GB | n/a |
| First launch from a fresh clone (tools already present) | 40.9 s | n/a |
| Next launch | 1.0 s | n/a |

## 6. Not verified, with the cause
| Item | Cause | What was done instead |
|---|---|---|
| **Docker Compose** distribution (CPU and GPU profiles) | no Docker, WSL or administrator rights on the machine | Dockerfile, compose file and GPU overlay written; image build and Trivy scan run in CI |
| Native **Linux / macOS** launch (`launch.sh`, `setup.sh`) | no Linux/macOS machine available | logic shared with Windows (`kpictl.py`, tested); backend tests run on Ubuntu in CI |
| **GitHub Actions CI** | not run at the time of this report | every step executed locally (tests, lint, types, audits, build, e2e); results of the hosted runs are in the repository's Actions tab |
| **Strict offline** mode (network adapter disabled) | disabling the network needs administrator rights | relaunched with a dead proxy (`HTTP(S)_PROXY=127.0.0.1:9`): application, OCR and local AI work (complete Russian journey passed); Ollama itself was not network-isolated |
| **Full from-scratch install** of the tools (Node, Tesseract, Ollama, model) by the script | these tools were already downloaded on the machine during development | the same URLs and commands were run by hand during development; the script reuses them |
| **gitleaks** secret scan | tool not installed locally | run in CI; the project requires no secret (`.env.example` holds none) |
