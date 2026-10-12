# TASKS — état du travail (2026-10-11)

Légende : [x] fait et vérifié · [~] fait, vérification partielle · [!] bloqué (cause indiquée)

## Environnement
- [x] Windows 11, Python 3.14, RTX 3050 6 Go, 15 Go RAM, sans admin ni WSL
- [x] Outils utilisateur dans ~/.kpi-bridge/tools : Node 24.21.0, gh 2.102.0, Ollama 0.40.3, Tesseract 5.5.3, qwen3:4b

## Produit
- [x] Ingestion CSV/TSV/TXT/XLSX/XLS/PDF texte/PDF scanné/PNG/JPG/DOCX/JSON, limites appliquées pendant le traitement
- [x] Structure : titres, en-têtes, tableaux multiples, fusions, totaux, en-têtes répétés, provenance par ligne
- [x] Normalisation versionnée et réversible, profil qualité, corrections de cellules (OCR avec zone source)
- [x] Grammaire fermée, validation, moteur DuckDB verrouillé, règles KPI testées
- [x] IA locale (Ollama) avec réparation bornée, repli constructeur guidé, suggestions déterministes, clarifications
- [x] Dashboard, filtres, preuve, exports PDF/PNG/CSV, recettes + remapping, jointure many-to-one
- [x] 6 langues (503 clés), exports cyrillique/chinois

## Sécurité
- [x] Session à usage unique, cookie HttpOnly/SameSite, Host/Origin/CSRF, CSP, neutralisation CSV, ZIP/MIME, suppression
- [~] Isolation du worker : sous-processus + délais (natif) ; Docker durci écrit mais non exécuté

## Livraison
- [x] Tests : 55 backend, 10 Playwright (substitut), 6 Playwright avec vrai modèle, éval 6/6, benchmark
- [x] Scripts launch/setup/kpi (stop, status, doctor, update, backup, uninstall), testés sous Windows
- [x] Documentation, licences, CI écrite
- [!] Docker Compose : non exécuté (Docker/WSL/admin absents)
- [!] Linux/macOS : non testés manuellement
- [!] Publication GitHub : `gh` non authentifié → intervention utilisateur (`gh auth login`)
