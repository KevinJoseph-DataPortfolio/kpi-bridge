# Architecture

```
Navigateur (React + ECharts, même origine)
   │  HTTP 127.0.0.1:8765, cookie HttpOnly, en-tête CSRF
   ▼
API FastAPI  (kpibridge.app)  ──── calcul déterministe DuckDB en mémoire (engine/)
   │  SQLite : projets, versions, plans, tâches, cache de résultats
   │  file de tâches persistante (jobs)
   ▼
Worker superviseur (kpibridge.worker) ── 1 sous-processus par tâche (annulation, délai, crash)
   ├─ ingestion : détection de format → parseur dédié → RawTable (cellules texte + provenance)
   ├─ normalisation : TableConfig → Parquet typé versionné + profil qualité
   └─ interprétation : Ollama local (qwen3:4b) → plan JSON contraint → validation
```

## Modules backend (`backend/kpibridge`)

| Module | Rôle |
|---|---|
| `config.py` | Paramètres et limites (`KPI_*`), sans secret fixe |
| `db.py`, `migrations/` | SQLite + migrations versionnées (`schema_migrations`) |
| `security.py`, `session.py` | Host/Origin/CSRF, CSP, sessions, jeton d'amorçage à usage unique |
| `storage.py` | Répertoires privés par projet, chemins confinés, suppression, balayage des temporaires |
| `ingest/` | `detect` (liste blanche + contenu réel + ZIP), `csvtab`, `excel` (XLSX/XLS), `pdf` (texte + OCR), `ocr` (Tesseract + reconstruction de grille), `docs` (DOCX/TXT/JSON), `text` (valeurs « libellé : nombre » avec preuve), `tables` (blocs, en-tête, titres, totaux) |
| `normalize/` | `parse` (conventions numériques/dates, devises), `roles` (rôles multilingues), application de la `TableConfig` et profil |
| `plan/` | `grammar` (grammaire fermée), `validate`, `llm_schema` (format modèle/constructeur), `llm` (Ollama, substitut de test), `interpret` (prompt, réparation bornée, normalisation structurelle), `suggest`, `clarify` |
| `engine/` | `store` (DuckDB verrouillé), `compute` (compilation SQL paramétrée, évaluation décimale, devises, grain, variations, preuve) |
| `plans.py` | Cycle de vie demande → plan → clarification → validation → dashboard (cache versionné) |
| `services.py` | Projets, fichiers, tables, versions, aperçus |
| `joins.py` | Jointure many-to-one contrôlée |
| `recipes.py` | Recettes réutilisables et remapping |
| `exports/` | PDF (ReportLab, polices Noto embarquées), PNG (rendu PDFium), CSV neutralisé |
| `jobs.py`, `worker.py`, `handlers.py` | File persistante, superviseur, gestionnaires |
| `i18n.py` | Traductions serveur depuis `shared/locales` |

## Modèle canonique
`Project` (projects) · `SourceFile` (source_files, original en lecture seule) · `ExtractedTable` (extracted_tables +
Parquet brut `raw/<id>.parquet`, cellules texte et colonne `_src`) · `ColumnProfile` (profil JSON de la version) ·
`SourceReference` (colonne `_src` par ligne : `rec=;line=`, `sheet=;row=`, `page=;bbox=`, `line=;chars=`) ·
`Transformation` (transformations) · `DatasetVersion` (dataset_versions + Parquet typé `ds/<id>_v<n>.parquet`, config
complète réversible) · `AnalysisRequest` · `AnalysisPlan` (plan JSON validé, questions, réponses, origine) ·
`MetricDefinition` (métrique de la grammaire) · `MetricResult` (cartes calculées, cache `result_cache` clé = plan +
version + filtres) · `DashboardSpec` (graphiques, dimensions de filtre, disposition de la recette) · `Job` (jobs).

Chaque ligne typée garde `_row` (index brut) et `_src` (provenance) : la preuve d'un chiffre est la requête
reproductible (filtres + agrégat) qui retrouve les lignes contributrices, sans stocker de liste dans les cartes.

## Choix et écarts au cahier des charges
- **pandas non utilisé** : parseurs → cellules texte → typage maîtrisé (Decimal) → Arrow/Parquet. Évite les
  conversions implicites en flottants.
- **PyMuPDF écarté** (AGPL) : pdfplumber pour le texte, pypdfium2 pour le rendu.
- **Export PNG** = rendu de la page 1 du PDF par PDFium : un seul moteur de mise en page et les mêmes polices.
- **DuckDB lit des tables Arrow** chargées par pyarrow, puis l'accès externe est désactivé et la configuration
  verrouillée : le moteur de calcul n'a accès à aucun fichier.
- **Lanceur en Python** (`scripts/kpictl.py`) : aucune dépendance au mode d'exécution des scripts PowerShell ;
  `launch.cmd`/`launch.sh` ne font que trouver Python ≥ 3.12.
- **Tesseract sous Windows sans admin** : l'installeur UB-Mannheim exige une élévation ; le lanceur utilise
  micromamba + conda-forge en mode utilisateur.
- **Docker Compose** : écrit et décrit comme chemin de référence, mais non exécuté ici (voir TEST_REPORT).

## API (`/api/v1`)
Session : `POST /session/bootstrap`, `GET /session`, `POST /session/logout` · Méta : `GET /meta`, `GET /health` ·
Projets : `GET|POST /projects`, `GET|PATCH|DELETE /projects/{id}` · Fichiers : `PUT /projects/{id}/files` (flux
brut, en-tête `X-File-Name`), `POST /projects/{id}/examples/{nom}`, `POST .../files/{fid}/reingest`,
`DELETE .../files/{fid}` · Tâches : `GET /jobs/{id}`, `POST /jobs/{id}/cancel` · Tables : `.../tables/{tid}/preview|raw|config|header|revert/{v}|cells/{r}/{c}[/image]|suggestions` ·
Jointures : `POST .../joins/check`, `POST .../joins` · Plans : `POST .../requests`, `POST .../builder`,
`GET|PUT .../plans/{pid}`, `POST .../plans/{pid}/answers|validate|dashboard|detail|evidence/{metric}|export` ·
Recettes : `GET /recipes`, `GET|DELETE /recipes/{id}`, `POST /recipes/import`, `POST .../recipes`,
`POST .../recipes/{rid}/check|apply`.
