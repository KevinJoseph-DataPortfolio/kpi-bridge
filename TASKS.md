# TASKS — état du travail

Légende : [x] fait et vérifié · [~] fait, vérification partielle · [ ] à faire · [!] bloqué (cause indiquée)

## 0. Environnement
- [x] Inspection : Windows 11, Python 3.14, RTX 3050 6 Go, 15 Go RAM, pas d'admin, pas de WSL/Docker
- [x] Outils utilisateur : Node 24.21.0, gh 2.102.0, Ollama 0.40.3, Tesseract 5.5.3 (conda-forge), qwen3:4b
- [!] Docker Desktop : nécessite droits admin + WSL2 + redémarrage → distribution Compose écrite, non exécutée ici

## 1. Parcours vertical
- [ ] Backend : config, SQLite + migrations, stockage, sécurité, jobs, worker
- [ ] Ingestion CSV/TSV → normalisation → Parquet versionné
- [ ] Grammaire de plan + validation + moteur de calcul DuckDB
- [ ] Interprétation IA (Ollama) + repli constructeur guidé
- [ ] Dashboard + filtres + preuve
- [ ] Frontend 4 écrans

## 2-4. Ambiguïtés, provenance, formats
- [ ] Dates ambiguës, conventions numériques, sous-totaux, doublons, devises, statuts
- [ ] XLSX/XLS, PDF texte, PDF scanné/OCR, images, DOCX, TXT, JSON
- [ ] Jointure many-to-one vérifiée

## 5. Langues, exports, recettes
- [ ] 6 locales complètes, exports PDF/PNG/CSV, sauvegarde/réouverture, recettes + actualisation

## 6-8. Sécurité, tests, livraison
- [ ] SECURITY.md + tests de sécurité
- [ ] Tests unitaires, API, Playwright, IA réelle 6 langues, mesures de performance
- [ ] Scripts setup/launch/stop/doctor/update/backup/uninstall
- [ ] Docs, CI, archive + checksum, dépôt GitHub privé
