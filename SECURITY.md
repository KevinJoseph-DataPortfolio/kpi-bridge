# Security: threat model and protections

Scope: a **local, single-user** application listening on `127.0.0.1`. This document is not a certification. It
describes the threats considered, the protections implemented and **tested**, and their limits.
To report a vulnerability, open a private security advisory ("Security" tab → "Report a vulnerability") on the
repository.

## Assets
Imported files and derived data, results and exports, the session secret, the integrity of the displayed figures.

## Threats and protections

| Threat | Protection | Verification |
|---|---|---|
| Third-party web page calling the local API (CSRF) | `SameSite=Strict` cookie + mandatory `X-KPI-CSRF` header on every state-changing request + `Origin` / `Sec-Fetch-Site` check; no CORS headers | `test_host_origin_csrf_and_headers` |
| DNS rebinding | Strict allow-list for the `Host` header (`127.0.0.1:<port>`, `localhost:<port>`) | same |
| Another local process / user without the secret | Session required on the whole API except health/bootstrap; single-use secret created by the launcher, valid 5 min, only its SHA-256 digest is written to disk; it travels in the URL fragment (never sent over HTTP nor logged), is exchanged for an HttpOnly cookie and removed from history (`history.replaceState`) | `test_session_required_and_single_use_bootstrap`, `test_cookie_flags`, browser test "page without session" |
| Malicious file: fake MIME, renamed executable | Extension allow-list **and** real content check (signatures, expected OOXML container, null bytes) | `test_fake_mime_and_refused_types` |
| Decompression bomb, dangerous paths, macros | ZIP inspected **before** extraction: entry count, total decompressed size, per-entry ratio, absolute/`..` paths, encryption, `vbaProject.bin`; `.xlsm/.docm/...` files refused | `test_zip_traversal_bomb_and_vba_rejected_before_extraction` |
| Oversized file / denial of service | Byte limit enforced **while** streaming the upload; row, column, cell, page, OCR page and pixel limits enforced during parsing; maximum duration per job; one heavy job at a time | `test_oversized_file_stopped_during_streaming`, `test_row_limit_enforced_during_parse`, `test_upload_limit_via_http`, `test_crash_is_retried_then_failed_and_timeout_enforced` |
| XXE / XML entities | `defusedxml` for XML read directly; openpyxl uses defusedxml when installed; python-docx disables entity resolution | code review |
| Code execution through the document | No macro, recalculated formula, script, external link, PDF action or active rendering: Excel cached values only, PDF rasterised by PDFium without forms | `test_xlsx_formula_without_cache_is_unavailable` |
| SQL / code injection through the plan or the model | Closed grammar validated by Pydantic (`extra=forbid`) then semantically; column identifiers allow-listed as `c<n>`; values passed as parameters; in-memory DuckDB with `enable_external_access=false`, extensions forbidden and `lock_configuration=true` | `test_model_cannot_inject_code_or_numbers`, `test_unknown_field_and_incompatible_ops_refused`, manual DuckDB test (file read, INSTALL/LOAD, COPY, ATTACH refused) |
| Prompt injection (file or request) | The model has no tool, network or file access; it receives a bounded profile (3 samples ≤ 30 characters); output constrained by a JSON schema then validated; it never produces a figure; its labels are escaped text | `test_full_flow_with_malicious_cells_and_safe_exports`, `test_prompt_contains_bounded_data_only` |
| XSS (cells, labels, tooltips) | React escapes everything; `dangerouslySetInnerHTML` banned by ESLint; ECharts tooltips built from escaped text; CSP `script-src 'self'` | ESLint, API test (`<script>` returned as plain text) |
| Formula injection in CSV exports | **Text** cells starting with `= + - @ TAB CR LF \| %` are prefixed with `'`; real numbers (typed decimals) are not modified | `test_full_flow_with_malicious_cells_and_safe_exports` |
| Leakage through logs | No HTTP access log; logs limited to error types, with no document content, token or cookie; stack traces never returned to the user | code review |
| Network exfiltration | No telemetry; the only host contacted is the configured local Ollama (`trust_env=False`, no redirects); in Docker the worker sits on an internal network without Internet | code review, `docker-compose.yml` |
| Access to files outside the storage | Generated storage names, validated identifiers (`prj_<hex>`), paths resolved server-side and confined to the data directory; data kept outside the static folder | `test_ids_and_paths_are_server_side` |
| Stale result after a correction | Data version in the cache key; every correction creates a version, invalidates caches and forces plan revalidation | `test_correction_invalidates_results_no_stale_figure` |
| Incomplete deletion | Removal of sources, Parquet files, OCR images, temporary exports, SQLite rows (cascade) and closing of DuckDB connections | `test_delete_project_removes_everything` |

## HTTP headers
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP/CORP `same-origin`,
`Cache-Control: no-store` on the API. `style-src 'unsafe-inline'` is required by ECharts computed styles.

## Processing isolation
- **Native (tested on Windows)**: each job runs in a separate subprocess, killed on cancellation or timeout;
  `RLIMIT_AS` memory limit on Linux/macOS only. On Windows there is no per-process memory limit and no read-only
  filesystem: isolation is weaker than with Docker.
- **Docker (written, not executed on the development machine)**: read-only containers, `cap_drop: ALL`,
  `no-new-privileges`, unprivileged user, memory/CPU/PID limits for the worker, internal network without Internet
  for the worker, no mount of the home directory or the Docker socket, only the application port published on
  `127.0.0.1`.

## Storage
Data is stored in clear text in `~/.kpi-bridge/data` (permissions 700/600 on POSIX; user-profile ACLs on
Windows). **The application does not encrypt at rest**: use disk encryption (BitLocker, FileVault, LUKS).
Deletion is not a forensic wipe and does not touch external backups.

## Dependencies and supply chain
Versions pinned with hashes (`requirements.lock`, `package-lock.json`), Docker images pinned to a precise version,
launcher downloads from official sources (SHA-256 digest verified for Node.js). Third-party GitHub Actions are
pinned to a commit SHA where they run security tooling. CI: `pip-audit --strict`, `npm audit --audit-level=high`,
gitleaks, Trivy (fixable high and critical vulnerabilities), licence check. On 2026-10-11: **0 known
vulnerabilities** (`pip-audit`, `npm audit`).

## Out of scope (V1)
Exposure to the Internet or a local network, multi-user use, account authentication, TLS, server-side encryption,
isolation between clients. Changing the port or the listening address does not make the application safe for the
Internet. `KPI_EXTRA_HOSTS` and `KPI_SECURE_COOKIES` exist only for a future deployment behind an HTTPS proxy.
