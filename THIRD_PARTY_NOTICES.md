# Composants tiers et licences

Le code original de KPI Bridge est sous licence MIT (voir `LICENSE`). Il utilise les composants suivants, sans
modification, dont les licences sont compatibles avec cette distribution. Inventaire généré par
`python scripts/check_licenses.py --markdown` (Python) et `npm ls --omit=dev` (frontend) le 2026-10-11 ; la CI
refuse toute licence AGPL/GPL-3/SSPL dans les dépendances d'exécution.

## Polices embarquées (exports PDF/PNG)

| Fichier | Origine | Licence |
|---|---|---|
| `backend/kpibridge/exports/fonts/NotoSans-Regular.ttf` | Noto Sans (google/fonts), instance statique wght=400 générée par `scripts/make_fonts.py` | SIL Open Font License 1.1 (`OFL.txt`) |
| `backend/kpibridge/exports/fonts/NotoSansSC-Regular.ttf` | Noto Sans SC (google/fonts), instance statique wght=400 | SIL Open Font License 1.1 (`OFL.txt`) |

## Logiciels externes installés par le lanceur (non redistribués dans le dépôt)

| Logiciel | Version | Licence | Source |
|---|---|---|---|
| Ollama | 0.40.3 | MIT | github.com/ollama/ollama (archive officielle) |
| Modèle qwen3:4b | — | Apache-2.0 | ollama.com/library/qwen3 |
| Tesseract OCR | 5.5.x | Apache-2.0 | conda-forge (Windows, mode utilisateur) / paquets système (Linux, macOS) |
| Modèles tessdata_fast (fra, eng, deu, spa, rus, chi_sim) | main | Apache-2.0 | github.com/tesseract-ocr/tessdata_fast |
| Node.js (construction de l'interface uniquement) | 24.21.0 | MIT | nodejs.org (empreinte SHA-256 vérifiée) |
| micromamba (installation de Tesseract sous Windows) | dernière | BSD-3-Clause | github.com/mamba-org/micromamba-releases |

## Dépendances Python d'exécution (`backend/requirements.lock`)

| Paquet | Version | Licence |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 (utilisé sans modification) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| defusedxml | 0.7.1 | PSF |
| duckdb | 1.5.6 | MIT |
| et_xmlfile | 2.0.0 | MIT |
| fastapi | 0.143.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| lxml | 6.1.3 | BSD-3-Clause |
| openpyxl | 3.1.5 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20260107 | MIT |
| pdfplumber | 0.11.10 | MIT |
| pillow | 12.3.0 | MIT-CMU |
| pyarrow | 26.0.0 | Apache-2.0 |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic / pydantic_core | 2.14.1 / 2.50.1 | MIT |
| pypdfium2 (PDFium) | 5.14.0 | BSD-3-Clause, Apache-2.0 |
| pytesseract | 0.3.13 | Apache-2.0 |
| python-docx | 1.2.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| reportlab | 5.0.1 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| xlrd | 2.0.2 | BSD-3-Clause |

PyMuPDF (AGPL) a été volontairement écarté au profit de pdfplumber + pypdfium2.

## Dépendances JavaScript incluses dans l'interface compilée

| Paquet | Version | Licence |
|---|---|---|
| react / react-dom / scheduler | 19.3.0 / 19.3.0 / 0.28.0 | MIT |
| echarts | 6.1.0 | Apache-2.0 |
| zrender | 6.1.0 | BSD-3-Clause |
| i18next | 26.4.3 | MIT |
| react-i18next | 17.0.16 | MIT |
| html-parse-stringify | 4.0.1 | MIT |
| use-sync-external-store | 1.7.0 | MIT |
| @babel/runtime | 7.29.10 | MIT |
| tslib | 2.3.0 | 0BSD |

Outils de développement (non distribués) : TypeScript 5.9.3 (Apache-2.0), Vite 8.3.4 (MIT), Vitest, ESLint (MIT),
Playwright 1.64.0 (Apache-2.0), pytest (MIT), ruff (MIT), mypy (MIT), xlwt (BSD, création de fixtures XLS),
fontTools (MIT, génération des polices statiques), pip-audit (Apache-2.0).
