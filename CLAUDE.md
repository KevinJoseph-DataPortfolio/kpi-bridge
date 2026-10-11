# KPI Bridge — mémoire de projet pour Claude Code

Source des exigences : cahier des charges « KPI Bridge — mission complète » (copie résumée ici).
Ce fichier conserve les exigences, décisions et contraintes entre les sessions. L'état d'avancement est dans `TASKS.md`.

## Produit
Document → examen → « Que veux-tu savoir ? » → clarifications (1 à 3 questions max par étape) → dashboard interactif
(cartes KPI, graphiques, filtres, tableau). Pas de synthèse narrative. Chaque chiffre est vérifiable (« Vérifier ce chiffre »).
Outil local mono-utilisateur. Six langues d'interface : fr (défaut), en, ru, zh-Hans, de, es.

## Règles non négociables
- L'IA (Ollama, `qwen3:4b` par défaut) propose un plan JSON ; **le moteur déterministe calcule**. Aucun SQL/Python libre.
- Grammaire de calcul fermée (`backend/kpibridge/plan/grammar.py`), validée contre les champs réels.
- Décimales exactes (DECIMAL DuckDB / `decimal.Decimal`), arrondi uniquement à l'affichage.
- Valeur absente ≠ 0 ; division par zéro → indisponible + motif ; devises mélangées → résultats séparés.
- Panier moyen = somme / nombre distinct de commandes. Taux global = Σnum / Σdén.
- Pas de suppression automatique des doublons ; sous-totaux détectés exclus de manière visible et réversible.
- Jointures uniquement many-to-one avec clé unique vérifiée et contrôle des sommes avant/après.
- Formules Excel : valeur en cache uniquement, sinon « indisponible » signalé.
- Toute transformation est enregistrée et réversible ; une correction crée une nouvelle `DatasetVersion`.
- Sécurité : 127.0.0.1 uniquement, session via jeton à usage unique créé par le lanceur, cookie HttpOnly SameSite=Strict,
  contrôle Host/Origin + en-tête CSRF, CSP stricte, DuckDB sans accès externe ni extensions.

## Décisions techniques (et écarts au cahier des charges)
- Backend Python 3.12+ (testé 3.14 sous Windows), FastAPI, Pydantic v2, DuckDB, pyarrow (Parquet immuable par version).
- Pas de pandas : les parseurs produisent des cellules texte ; le typage est fait par `normalize.py`.
- PDF : pdfplumber (texte), pypdfium2 (rendu raster), Tesseract via pytesseract (OCR). PyMuPDF exclu (AGPL).
- Export PDF : ReportLab + polices Noto embarquées (OFL). Export PNG : rendu de la page 1 du PDF via pdfium (même code, mêmes polices).
- Worker séparé (`python -m kpibridge.worker`) avec file de tâches SQLite (pas de Redis).
- Frontend React + TypeScript + Vite, ECharts, i18next. Locales partagées dans `shared/locales/*.json` (frontend + exports).
- Environnement de dev de référence : Windows 11 sans droits admin, sans WSL ⇒ Docker non testable ici.
  Outils installés en mode utilisateur dans `%USERPROFILE%\.kpi-bridge\tools` (Node portable, Ollama, Tesseract via micromamba/conda-forge, gh).
  Attention : l'app Claude (MSIX) virtualise `%LOCALAPPDATA%` ; ne rien y installer qui doive être vu par l'utilisateur.

## Commandes utiles
- Tests backend : `.venv\Scripts\python -m pytest -q backend/tests`
- Lint/types : `.venv\Scripts\ruff check backend` ; `.venv\Scripts\mypy backend/kpibridge`
- Frontend : `cd frontend; npm ci; npm run build; npm run test:e2e`
- Lancement natif : `launch.cmd` (Windows) ou `./launch.sh`
