# Rapport de tests — 2026-10-11

Environnement d'exécution : Windows 11 Home (10.0.26300), AMD Ryzen (Family 25 Model 124), 15,3 Go RAM,
NVIDIA RTX 3050 6 Go (portable), Python 3.14.7, Node 24.21.0, Tesseract 5.5.3 (conda-forge), Ollama 0.40.3,
modèle `qwen3:4b`. Aucun droit administrateur, pas de WSL, pas de Docker.

Toutes les valeurs ci-dessous ont été **exécutées et mesurées** ; ce qui n'a pas pu l'être est listé à la fin.

## 1. Synthèse

| Suite | Commande | Résultat |
|---|---|---|
| Tests backend (unitaires, ingestion, règles KPI, API, sécurité, worker réel, exports, i18n) | `cd backend && pytest` | **55 réussis / 55** (≈ 30 s) |
| Lint Python | `ruff check backend scripts` | aucune erreur |
| Types Python | `mypy kpibridge` | aucune erreur (40 fichiers) |
| Lint / types frontend | `eslint src e2e` · `tsc -b` | aucune erreur |
| Navigateur, substitut d'IA (comme la CI) | `npx playwright test journey` | **10 réussis / 10** (6 parcours dans les 6 langues + 2 tests mobile/session × 2 profils), 1,1 min |
| Navigateur, **vrai modèle local** | `KPI_LLM_PROVIDER=ollama npx playwright test -g parcours --project=desktop` | **6 réussis / 6** en 2,6 min ; les 6 plans ont pour origine `ai:ollama` (aucun repli) |
| Évaluation du vrai modèle (hors CI) | `python scripts/eval_llm.py` | **6 plans valides / 6**, 1 tentative chacun, 13,9 à 18,3 s ; tous les contrôles sémantiques réussis (`docs/llm_eval.json`) |
| Audit Python | `pip-audit -r requirements.lock --require-hashes --strict` | aucune vulnérabilité connue |
| Audit npm | `npm audit --audit-level=high` | 0 vulnérabilité |
| Licences | `scripts/check_licenses.py` | aucune licence incompatible |
| Installation propre | clone neuf + `launch.cmd --no-browser` (port 8790, données neuves) | réussie en **40,9 s** (outils déjà présents sur la machine) |
| Second lancement | `launch.cmd --no-browser` | réutilisation, **1,0 s** |
| Mise à jour | `kpi update` sur le clone | réussie, 11,4 s |
| Exploitation | `kpi stop / status / doctor / backup / uninstall` | réussies ; `uninstall` conserve les données sans `--purge-data` |

## 2. Critères de réception (cahier des charges § 14)

| Cas | Test(s) | Résultat |
|---|---|---|
| CSV ventes propre : KPI exacts, filtres et exports concordants | `test_clean_csv_exact_kpis`, `test_global_filter_applies_to_cards_charts_and_table`, parcours navigateur | ✅ (15,30 exact ; filtre appliqué aux cartes, graphiques, tableau et CSV) |
| CSV français `;`, décimales `,`, identifiants à zéros initiaux | `test_french_csv_semicolon_decimals_leading_zero_ids` | ✅ (`00042` conservé, `1 234,50` → 1234.50, ligne Total exclue) |
| Dates `03/04/2026` sans convention fiable | `test_ambiguous_dates_require_clarification`, `test_date_order_ambiguous_until_resolved` | ✅ question avant KPI temporel ; MDY → 3 mars |
| Excel : titres, cellules fusionnées, plusieurs feuilles, sous-totaux | `test_excel_titles_merged_multisheet_subtotals` | ✅ 2 tableaux, total 190,5 (sous-total et total général non additionnés), fusion appliquée |
| XLS ancien et XLSX avec formule sans cache | `test_old_xls_is_usable`, `test_xlsx_formula_without_cache_is_unavailable` | ✅ XLS = 30,75 ; formules sans cache signalées, KPI « indisponible » |
| PDF textuel multi-pages | `test_text_pdf_multipage_repeated_headers_and_provenance` | ✅ 120 lignes, en-têtes répétés exclus, Σ = 7320,00, provenance `page=1;bbox=…` |
| Scan lisible puis scan dégradé | `test_scanned_pdf_ocr_readable`, `test_degraded_scan_never_fabricates`, `test_tabular_image` | ✅ OCR réel = 3540 ; scan dégradé : refus explicite ou cellules signalées, aucun chiffre inventé |
| DOCX tableau, texte sans données, JSON, image tabulaire | `test_docx_table_text_without_data_json_supported`, `test_tabular_image` | ✅ DOCX = 42 ; texte vide → `no_data_found_text` ; JSON imbriqué = 3,75 ; JSON non supporté → message ciblé |
| Commandes à plusieurs lignes : panier moyen | `test_average_basket_uses_distinct_orders_not_rows` | ✅ 250 / 3 commandes (pas / 5 lignes) ; 102,5 avec exclusion des annulations |
| Taux avec dénominateurs différents | `test_weighted_rate_not_average_of_rates` | ✅ 15 / 1010 = 1,485 % (et non 25,5 %) ; somme/moyenne de taux refusées |
| Coûts manquants, dénominateur nul, mois absent, devises mélangées | `test_missing_costs_zero_denominator_missing_month_mixed_currencies` | ✅ indisponible (`no_values`, `division_by_zero`), trou en février, EUR 125 / USD 50 séparés |
| Jointure qui duplique un montant | `test_join_blocked_when_duplicating_amounts` | ✅ bloquée ; jointure sûre conserve Σ = 35 |
| Cellules `<script>`, formule CSV, instruction d'exfiltration | `test_full_flow_with_malicious_cells_and_safe_exports`, `test_model_cannot_inject_code_or_numbers` | ✅ texte brut échappé, formules préfixées `'`, `-5.5` intact, total 24,5 (pas 999999) |
| Faux MIME, traversal, archive bombée, fichier surdimensionné | `test_fake_mime_and_refused_types`, `test_zip_traversal_bomb_and_vba_rejected_before_extraction`, `test_oversized_file_stopped_during_streaming`, `test_upload_limit_via_http`, `test_row_limit_enforced_during_parse` | ✅ rejet avant extraction / arrêt borné |
| Accès API sans session et origine non autorisée | `test_session_required_and_single_use_bootstrap`, `test_host_origin_csrf_and_headers`, `test_cookie_flags`, test navigateur « page sans session » | ✅ 401 / 403 ; jeton à usage unique ; cookie HttpOnly SameSite=Strict |
| Annulation, crash worker, redémarrage | `test_cancel_running_job`, `test_crash_is_retried_then_failed_and_timeout_enforced`, `test_supervisor_crash_recovery_on_restart` | ✅ (worker réel en sous-processus) annulation, 2 tentatives puis échec, délai, reprise après arrêt brutal, temporaires balayés |
| Suppression projet | `test_delete_project_removes_everything` | ✅ fichiers et lignes SQLite supprimés, 404 ensuite |
| Actualisation avec schéma modifié | `test_recipe_refresh_requires_remap_and_recomputes` | ✅ remapping demandé, résultat recalculé (7, pas l'ancien 15) |
| Correction et absence de chiffre périmé | `test_correction_invalidates_results_no_stale_figure` | ✅ nouvelle version, cache invalidé, conflit de version 409, retour arrière |
| Parcours dans les six langues | parcours navigateur ×6, `test_key_parity_and_placeholders`, `test_frontend_static_keys_exist`, `test_error_codes_raised_in_backend_are_translated`, `test_pdf_export_glyphs_pagination_and_filters[×6]` | ✅ 503 clés × 6 langues ; demande traitée par le **vrai modèle** dans les 6 langues ; exports PDF paginés avec cyrillique et chinois, sans glyphe manquant |
| Installation fraîche, second lancement, hors ligne | § 1 et § 5 | ✅ installation et réutilisation ; hors ligne **simulé** (voir § 6) |

## 3. Tests navigateur (Playwright, Chromium)
Parcours vérifié dans chaque langue : lien de session retiré de l'URL → choix de la langue → exemple synthétique →
écran « Comprendre » → demande libre → interprétation → réponses aux clarifications → validation du plan →
dashboard (valeur non vide, pas de `NaN`) → filtre de catégorie (périmètre modifié) → « Vérifier ce chiffre » →
exports CSV, PDF, PNG non vides → aucune clé de traduction brute affichée. Tests supplémentaires : page sans
session (401), dashboard mobile (Pixel 7) sans défilement horizontal.
- Substitut d'IA (configuration CI) : **10/10** (6 parcours, tests mobiles et sans session sur 2 profils).
- Vrai modèle `qwen3:4b` : **6/6**, origine des plans `ai:ollama`, aucun repli.

Défauts trouvés par ces tests et corrigés : rangées de boutons non repliées sur mobile ; course à l'affichage des
questions dans un test ; titres de graphiques identiques.

## 4. Évaluation du vrai modèle local (`docs/llm_eval.json`)
Demande « ventes mensuelles, panier moyen, cinq meilleurs produits et marge » sur le profil réel de l'exemple :

| Langue | Plan valide | Tentatives | Durée | Contrôles (somme des montants, panier = commandes distinctes, pas de ratio par lignes, top 5 produits, courbe mensuelle, marge = différence) |
|---|---|---|---|---|
| fr | oui | 1 | 18,3 s | 6/6 |
| en | oui | 1 | 16,2 s | 6/6 |
| ru | oui | 1 | 18,1 s | 6/6 |
| zh-Hans | oui | 1 | 14,4 s | 6/6 |
| de | oui | 1 | 13,9 s | 6/6 |
| es | oui | 1 | 14,2 s | 6/6 |

Historique honnête : la première version obtenait 0/6 (le schéma laissait le modèle omettre `metrics`), puis 4/6
(il remplissait à tort le champ de grain). Corrections : schéma de génération à propriétés obligatoires sans champ
de grain, exemple guidé, indications de réparation, normalisation structurelle sûre de la période, échéance
globale réelle (un appel avait duré 1319 s malgré le délai de lecture). Le modèle peut encore se tromper sur
d'autres demandes : le plan est toujours affiché et doit être validé.

## 5. Mesures de performance (`docs/benchmark.json`, `scripts/benchmark.py --ai`)

| Mesure | Valeur | Cible |
|---|---|---|
| Démarrage de l'API (processus → santé OK) | 3,8 – 4,4 s | — |
| Import + normalisation d'un CSV de 50 000 lignes (1,9 Mo) | 4,9 – 5,4 s | — |
| Premier calcul du dashboard (3 KPI dont variation, 2 graphiques, tableau) | 0,23 – 0,27 s | — |
| Application d'un filtre (16 combinaisons distinctes, sans cache) | médiane 0,19 – 0,21 s, max 0,21 s | < 1 s : **atteinte** |
| Interprétation IA (`qwen3:4b`, 100 % GPU) | 11,8 – 18,9 s | — |
| Pic mémoire du processus import + calcul | 172 Mio | pas de saturation : **constatée** |
| Mémoire GPU du modèle | 3,2 Go (sur 6 Go) | — |
| RAM libre pendant les mesures (autres applications ouvertes) | 3,7 Go sur 15,3 Go | — |
| Premier lancement depuis un clone neuf (outils déjà présents) | 40,9 s | — |
| Lancement suivant | 1,0 s | — |

## 6. Non vérifié, avec la cause
| Élément | Cause | Ce qui a été fait à la place |
|---|---|---|
| Distribution **Docker Compose** (profils CPU et GPU) | pas de Docker, de WSL ni de droits administrateur sur la machine | Dockerfile, compose et surcouche GPU écrits ; construction et analyse Trivy prévues en CI |
| Lancement natif **Linux / macOS** (`launch.sh`, `setup.sh`) | aucune machine Linux/macOS disponible | logique commune à Windows (`kpictl.py`, testé) ; tests backend prévus sur Ubuntu en CI |
| **CI GitHub Actions** | dépôt pas encore publié (authentification GitHub requise) | toutes les étapes exécutées localement (tests, lint, types, audits, build, e2e) |
| **Hors ligne strict** (carte réseau désactivée) | désactiver le réseau demande des droits administrateur | relancement avec un proxy mort (`HTTP(S)_PROXY=127.0.0.1:9`) : application, OCR et IA locale fonctionnent (parcours russe complet réussi) ; Ollama n'était pas isolé du réseau |
| Installation **complète depuis zéro** des outils (Node, Tesseract, Ollama, modèle) par le script | ces outils étaient déjà téléchargés sur la machine pendant le développement | les mêmes URL et commandes ont été exécutées manuellement pendant le développement ; le script les réutilise |
| Recherche de secrets **gitleaks** | outil non installé localement | prévue en CI ; aucun secret n'est requis par le projet (`.env.example` sans secret) |
