# Sécurité — modèle de menace et protections

Périmètre : application **locale, mono-utilisateur**, écoutant sur `127.0.0.1`. Ce document n'est pas une
certification ; il décrit les menaces retenues, les protections implémentées et **testées**, et leurs limites.
Signalement d'une vulnérabilité : ouvrir une issue privée (« Security advisory ») sur le dépôt.

## Actifs
Fichiers importés et données dérivées, résultats et exports, secret de session, intégrité des chiffres affichés.

## Menaces retenues et protections

| Menace | Protection | Vérification |
|---|---|---|
| Page web tierce qui appelle l'API locale (CSRF) | Cookie `SameSite=Strict` + en-tête obligatoire `X-KPI-CSRF` sur toute requête modifiante + contrôle `Origin` / `Sec-Fetch-Site` ; aucun en-tête CORS | `test_host_origin_csrf_and_headers` |
| Rebinding DNS | Liste blanche stricte de l'en-tête `Host` (`127.0.0.1:<port>`, `localhost:<port>`) | idem |
| Autre processus / utilisateur local sans le secret | Session obligatoire sur toute l'API sauf santé/amorçage ; secret à usage unique créé par le lanceur, valable 5 min, seule son empreinte SHA-256 est écrite sur disque ; il transite dans le fragment d'URL (jamais envoyé en HTTP ni journalisé) puis est échangé contre un cookie HttpOnly et retiré de l'historique (`history.replaceState`) | `test_session_required_and_single_use_bootstrap`, `test_cookie_flags`, test navigateur « page sans session » |
| Fichier malveillant : faux MIME, exécutable renommé | Liste blanche d'extensions **et** contrôle du contenu réel (signatures, conteneur OOXML attendu, octets nuls) | `test_fake_mime_and_refused_types` |
| Bombe de décompression, chemins dangereux, macros | Inspection du ZIP **avant** extraction : nombre d'entrées, taille totale décompressée, ratio par entrée, chemins absolus/`..`, chiffrement, `vbaProject.bin` ; fichiers `.xlsm/.docm/...` refusés | `test_zip_traversal_bomb_and_vba_rejected_before_extraction` |
| Fichier surdimensionné / déni de service | Limite d'octets appliquée **pendant** la réception en flux ; limites de lignes, colonnes, cellules, pages, pages OCR, pixels appliquées pendant l'analyse ; délai maximal par tâche ; une tâche lourde à la fois | `test_oversized_file_stopped_during_streaming`, `test_row_limit_enforced_during_parse`, `test_upload_limit_via_http`, `test_crash_is_retried_then_failed_and_timeout_enforced` |
| XXE / entités XML | `defusedxml` pour le XML lu directement ; openpyxl utilise defusedxml quand il est installé ; python-docx désactive la résolution d'entités | revue de code |
| Exécution de code via le document | Aucune macro, formule recalculée, script, lien externe, action PDF ni rendu actif : valeurs en cache Excel uniquement, PDF rasterisé par PDFium sans formulaires | `test_xlsx_formula_without_cache_is_unavailable` |
| Injection SQL / code via le plan ou le modèle | Grammaire fermée validée par Pydantic (`extra=forbid`) puis sémantiquement ; identifiants de colonnes en liste blanche `c<n>` ; valeurs en paramètres ; DuckDB en mémoire avec `enable_external_access=false`, extensions interdites et `lock_configuration=true` | `test_model_cannot_inject_code_or_numbers`, `test_unknown_field_and_incompatible_ops_refused`, test manuel DuckDB (lecture fichier, INSTALL/LOAD, COPY, ATTACH refusés) |
| Injection de prompt (fichier ou demande) | Le modèle n'a ni outil, ni réseau, ni fichier ; il reçoit un profil borné (3 échantillons ≤ 30 caractères) ; sortie contrainte par schéma JSON puis validée ; il ne produit jamais de chiffre ; ses libellés sont du texte échappé | `test_full_flow_with_malicious_cells_and_safe_exports`, `test_prompt_contains_bounded_data_only` |
| XSS (cellules, libellés, infobulles) | React échappe tout ; `dangerouslySetInnerHTML` interdit par ESLint ; infobulles ECharts construites à partir de texte échappé ; CSP `script-src 'self'` | ESLint, test API (« `<script>` » renvoyé comme texte brut) |
| Injection de formules dans les exports CSV | Cellules **texte** commençant par `= + - @ TAB CR LF \| %` préfixées par `'` ; les vrais nombres (typés décimaux) ne sont pas modifiés | `test_full_flow_with_malicious_cells_and_safe_exports` |
| Fuite par les journaux | Pas de journal d'accès HTTP ; journaux limités aux types d'erreurs, sans contenu de document, jeton ni cookie ; traces jamais renvoyées à l'utilisateur | revue de code |
| Exfiltration réseau | Aucune télémétrie ; seul hôte contacté : Ollama local configuré (`trust_env=False`, sans redirection) ; en Docker le worker est sur un réseau interne sans Internet | revue de code, `docker-compose.yml` |
| Accès aux fichiers hors stockage | Noms de stockage générés, identifiants validés (`prj_<hex>`), chemins résolus côté serveur et confinés au répertoire de données ; données hors du dossier statique | `test_ids_and_paths_are_server_side` |
| Résultat périmé après correction | Version de données dans la clé de cache ; toute correction crée une version, invalide les caches et force la revalidation du plan | `test_correction_invalidates_results_no_stale_figure` |
| Suppression incomplète | Suppression des sources, Parquet, images OCR, exports temporaires, lignes SQLite (cascade) et fermeture des connexions DuckDB | `test_delete_project_removes_everything` |

## En-têtes HTTP
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, COOP/CORP `same-origin`,
`Cache-Control: no-store` sur l'API. `style-src 'unsafe-inline'` est nécessaire aux styles calculés d'ECharts.

## Isolation des traitements
- **Natif (testé sous Windows)** : chaque tâche s'exécute dans un sous-processus séparé, tué en cas d'annulation ou de
  dépassement du délai ; limite mémoire `RLIMIT_AS` sous Linux/macOS uniquement. Sous Windows, il n'y a ni limite
  mémoire par processus ni système de fichiers en lecture seule : l'isolation est moindre qu'en Docker.
- **Docker (écrit, non exécuté sur la machine de développement)** : conteneurs en lecture seule, `cap_drop: ALL`,
  `no-new-privileges`, utilisateur non privilégié, limites mémoire/CPU/PID pour le worker, réseau interne sans
  Internet pour le worker, aucun montage du domicile ni du socket Docker, seul le port de l'application publié sur
  `127.0.0.1`.

## Stockage
Les données sont stockées en clair dans `~/.kpi-bridge/data` (permissions 700/600 sous POSIX ; ACL du profil
utilisateur sous Windows). **L'application ne chiffre pas au repos** : utilisez le chiffrement du disque
(BitLocker, FileVault, LUKS). La suppression n'est pas un effacement forensique et ne touche pas aux sauvegardes
externes.

## Dépendances et chaîne d'approvisionnement
Versions verrouillées avec empreintes (`requirements.lock`, `package-lock.json`), images Docker fixées par version
précise, téléchargements du lanceur depuis les sources officielles (empreinte SHA-256 vérifiée pour Node.js).
CI : `pip-audit --strict`, `npm audit --audit-level=high`, gitleaks, Trivy (vulnérabilités corrigibles élevées et
critiques), contrôle de licences. Au 2026-10-11 : **0 vulnérabilité connue** (`pip-audit`, `npm audit`).

## Hors périmètre (V1)
Exposition sur Internet ou un réseau local, multi-utilisateur, authentification par compte, TLS, chiffrement
serveur, isolation entre clients. Changer le port ou l'adresse d'écoute ne rend pas l'application sûre pour
Internet. `KPI_EXTRA_HOSTS` et `KPI_SECURE_COOKIES` n'existent que pour un futur déploiement derrière un proxy HTTPS.
