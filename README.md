# KPI Bridge

**Déposez un document → l'application l'examine → elle vous demande ce que vous voulez savoir → elle clarifie les
ambiguïtés utiles → elle produit un dashboard interactif dont chaque chiffre est vérifiable.**

Outil local pour une personne. IA locale (Ollama), calculs déterministes (DuckDB, décimales exactes), six langues
(français, English, Русский, 简体中文, Deutsch, Español). Aucune donnée envoyée à un tiers.

## Démarrage le plus simple

**Windows** : double-cliquez sur `launch.cmd`.
**Linux / macOS** : `./launch.sh`

Le premier lancement installe tout ce qui manque, de façon idempotente et sans droits administrateur sous Windows :
environnement Python verrouillé, interface compilée, Tesseract (OCR), Ollama et le modèle `qwen3:4b` (≈ 2,5 Gio),
avec affichage de la progression et des volumes. Il démarre ensuite les services et ouvre le navigateur sur
`http://127.0.0.1:8765` via un lien de session **à usage unique**. Les lancements suivants réutilisent
l'installation (≈ 1 s).

```bash
launch.cmd --no-browser
```
affiche le lien au lieu d'ouvrir le navigateur (il expire au bout de 5 minutes et ne sert qu'une fois).

Autres commandes (`kpi.cmd` sous Windows, `./kpi.sh` ailleurs) :

| Commande | Effet |
|---|---|
| `kpi stop` | arrête l'application et le worker (et Ollama s'il a été lancé par KPI Bridge) |
| `kpi status` | état des services |
| `kpi doctor` | diagnostic : système, GPU, Python, OCR, Ollama, modèle, disque, santé |
| `kpi update` | `git pull`, puis réinstallation de ce qui a changé uniquement |
| `kpi backup` | sauvegarde cohérente des données dans `~/.kpi-bridge/backups/` |
| `kpi uninstall [--purge-tools] [--purge-data]` | désinstalle ; les données ne sont supprimées qu'avec `--purge-data` et confirmation |
| `setup.ps1` / `./setup.sh` | installation seule (`-NoAi`, `-NoOcr` / `--no-ai`, `--no-ocr`) |

**Docker** (chemin de référence quand Docker est disponible) : `python scripts/kpictl.py launch --docker`
(`--gpu` pour NVIDIA). Voir « Limites connues » : ce chemin n'a pas pu être exécuté sur la machine de développement.

## Prérequis
- Python **3.12 ou plus** (testé avec 3.14 sous Windows 11 ; la CI teste 3.12 et 3.13 sous Ubuntu).
- Internet pour la **première installation** seulement ; ensuite le fonctionnement est hors ligne.
- Matériel de référence : RTX 3050 6 Go, 15 Go de RAM → le modèle tourne à 100 % sur GPU (≈ 3,2 Go de VRAM). Sans GPU
  compatible, Ollama bascule sur le CPU (plus lent) ; aucun GPU n'est requis pour lancer l'application.
- Sous Linux/macOS, Tesseract et Ollama s'installent avec les commandes système indiquées par `setup.sh`
  (droits administrateur). Sans eux, l'application fonctionne sans OCR / sans IA (suggestions et constructeur guidé).

## Utilisation
1. **Déposer** un ou plusieurs fichiers (glisser-déposer ou bouton), ou **Essayer avec un exemple** (données synthétiques).
   La progression est réelle (envoi, import, extraction, OCR, vérification) et la tâche peut être annulée ;
   elle continue côté serveur si vous rechargez la page.
2. **Comprendre et demander** : tableaux détectés, taille, période, aperçu, puis « Que veux-tu savoir à partir de ce
   document ? ». Écrivez librement dans l'une des six langues ou choisissez des suggestions fondées sur les colonnes
   réelles (jamais de marge sans coûts, jamais de « commandes » comptées en lignes).
3. **Vérifier** : 1 à 3 questions utiles si nécessaire (« 03/04/2026 : 3 avril ou 4 mars ? », devise, annulations,
   montant répété par commande, HT/TTC, doublons), puis le plan lisible à valider. L'écran « Vérifier les données »
   permet de choisir la ligne d'en-tête, corriger types et conventions, exclure ou réintégrer des lignes, corriger
   une cellule OCR en voyant sa zone source, et revenir à une version précédente.
4. **Dashboard** : 3 à 6 cartes, 2 à 4 graphiques, filtres de période et de catégories (appliqués partout),
   tableau détaillé avec provenance, « Vérifier ce chiffre » (définition, champs, filtres, exclusions, règles,
   requête reproductible, lignes sources), exports PDF/PNG (instantanés) et CSV, recette réutilisable,
   « Modifier ma demande » (réutilise les données extraites).

Si l'IA locale échoue (service absent, délai, plan invalide après réparation), l'application l'affiche et ouvre le
**constructeur guidé**, qui utilise exactement les mêmes calculs ; ce n'est pas présenté comme une interprétation.

## Formats
| Pris en charge | Détails |
|---|---|
| CSV, TSV, TXT délimité | encodages (BOM, UTF-8/16, détection), séparateurs `, ; TAB \|`, titres, totaux, zéros initiaux conservés |
| XLSX, XLS | feuilles, tableaux multiples, cellules fusionnées, sous-totaux ; formules : valeur en cache uniquement, sinon « indisponible » |
| PDF texte | tableaux multi-pages, en-têtes répétés exclus, provenance page + zone |
| PDF scanné, PNG, JPG/JPEG | OCR Tesseract local, reconstruction des colonnes, cellules peu lisibles signalées |
| DOCX | tableaux ; sinon valeurs « libellé : nombre » du texte |
| TXT | délimité, ou valeurs « libellé : nombre » reliées à leur ligne source |
| JSON | listes d'objets (ou un objet contenant une telle liste), aplaties sur 3 niveaux |

**Refusés** : exécutables, archives (zip, 7z, rar…), fichiers à macros (xlsm, xlsb, docm…), fichiers protégés par
mot de passe, contenu ne correspondant pas à l'extension, autres formats. Aucune macro, formule, script, lien
externe ni action PDF n'est exécuté. Un texte sans chiffres ou une image illisible donne un message clair et une
action suivante, jamais de faux KPI.

## Limites (modifiables dans `.env`)
25 Mio par fichier · 200 000 lignes · 200 colonnes · 50 pages PDF · 20 pages en OCR · 15 min par tâche.
Elles sont appliquées pendant le traitement (réception en flux, lecture bornée). Au-delà : refus explicite, ou
sélection de pages proposée pour les PDF. Mesuré sur la machine de référence : import d'un CSV de 50 000 lignes en
≈ 5 s, filtres en ≈ 0,2 s (voir `docs/TEST_REPORT.md`).

## Stockage, suppression, confidentialité
Données dans `~/.kpi-bridge/data` (`KPI_DATA_DIR` pour changer) : SQLite (métadonnées), sources en lecture seule,
Parquet versionnés, images OCR. Projets conservés jusqu'à leur suppression et rouvrables après redémarrage.
« Supprimer le projet » retire sources, dérivés, résultats, exports temporaires et métadonnées (pas d'effacement
forensique, sauvegardes externes non concernées). Pas de chiffrement au repos par l'application : utilisez le
chiffrement du disque. Détails : `PRIVACY.md`, `SECURITY.md`.

## Maintenance et dépannage
- **« Session locale requise »** : relancez `launch.cmd` (le lien est à usage unique, 5 min).
- **IA indisponible** : `kpi doctor` ; vérifiez qu'Ollama répond et que le modèle est présent. Modèle configurable :
  `KPI_OLLAMA_MODEL` dans `.env`.
- **OCR indisponible** : `kpi doctor` ; sous Windows relancez `setup.ps1` (ou `launch.cmd`), sinon installez Tesseract.
- **Port occupé** : changez `KPI_PORT` dans `.env`.
- **Journaux** : `~/.kpi-bridge/data/logs/` (sans contenu de documents ni secrets).
- **Politique PowerShell** : `launch.cmd` n'exécute aucun script PowerShell ; si `setup.ps1` est bloqué, utilisez
  `launch.cmd` (qui installe au premier lancement). Aucune politique système n'est modifiée.
- **Proxy d'entreprise** : les services locaux sont toujours contactés sans proxy.

## Développement
```bash
python -m venv .venv && .venv/Scripts/pip install --require-hashes -r backend/requirements.lock -r backend/requirements-dev.lock
cd backend && ../.venv/Scripts/python -m pytest          # 55 tests, IA remplacée par un substitut identifié
cd frontend && npm ci && npm run build && npx playwright test
python scripts/eval_llm.py                               # évaluation du vrai modèle local, 6 langues
python scripts/benchmark.py --ai                          # mesures de performance
```
Documentation : `docs/ARCHITECTURE.md`, `docs/KPI_RULES.md`, `docs/TEST_REPORT.md`, `SECURITY.md`,
`THIRD_PARTY_NOTICES.md`, `PRIVACY.md`. Licence : MIT (`LICENSE`).

## Limites connues (honnêtes)
- **Docker Compose non exécuté** : la machine de développement n'a ni Docker, ni WSL, ni droits administrateur.
  Les fichiers sont écrits et la CI construit et analyse l'image, mais le parcours Compose (dont le profil GPU)
  n'a pas été lancé.
- **Linux/macOS non testés manuellement** : seuls les tests automatisés de la CI (Ubuntu) les couvrent, une fois le
  dépôt publié.
- Le modèle `qwen3:4b` est un choix mesuré, pas une garantie : il réussit les demandes de référence dans les six
  langues (voir rapport) mais peut se tromper sur des demandes ambiguës ; le plan est toujours montré pour validation.
- Le PDF/PNG sont des instantanés ; l'OCR ne lit pas l'écriture manuscrite ; les jointures sont limitées au
  « plusieurs vers un ».
