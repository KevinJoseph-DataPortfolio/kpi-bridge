# Règles de calcul des KPI

**L'IA interprète la demande et propose un plan ; le moteur de données calcule.** Le modèle ne produit jamais de
chiffre, de SQL ou de code. Les trois origines de plan (IA, suggestions, constructeur guidé) et les recettes
passent par la même grammaire, la même validation et le même moteur.

## Grammaire
- Agrégations : `sum`, `count_rows`, `count`, `count_distinct`, `avg`, `median`, `min`, `max`, avec filtres et grain
  optionnel `per` (une valeur par clé).
- Opérations dérivées : `add`, `sub`, `mul`, `div`, constantes décimales ; profondeur ≤ 4, ≤ 6 agrégats.
  Le format simplifié (modèle et constructeur) propose : agrégat, ratio, différence, taux de marge
  `(A − B) ÷ A × 100`, pourcentage `A ÷ B × 100`.
- Filtres : `eq neq in not_in gt gte lt lte is_null not_null between` ; `neq`/`not_in` conservent les valeurs vides.
- Graphiques : `line` (par période), `bar`, `hbar` (top N ≤ 20). Pas de camembert, pas de 3D.
- Définition de KPI : identifiant stable, libellé, unité, expression structurée, filtres, comparaison, règle de taux,
  décimales d'affichage. Grain, champs sources, période, traitement des absents et de la division par zéro sont
  ceux décrits ci-dessous et visibles dans « Vérifier ce chiffre ».

## Règles (toutes testées dans `backend/tests/test_kpi_rules.py`)
| Règle | Comportement |
|---|---|
| Panier moyen | `somme(montant) ÷ nombre distinct de commandes` ; jamais divisé par le nombre de lignes. Le modèle reçoit cette consigne et l'évaluation vérifie l'usage de `count_distinct`. |
| Taux global | `Σ numérateur ÷ Σ dénominateur`. Somme de pourcentages refusée ; moyenne de taux par ligne refusée sauf règle `row_average` explicite. |
| Division par zéro | Résultat « indisponible » avec motif `division_by_zero`. |
| Valeur absente | Jamais convertie en 0 ; ignorée par l'agrégat, comptée et affichée (« valeurs vides ignorées »). Un agrégat sans aucune valeur est « indisponible » (`no_values`). Une valeur illisible est comptée comme invalide, jamais remplacée par 0. |
| Variation | Période sélectionnée vs période précédente de même durée ; sans filtre, dernier mois des données vs mois précédent. Indisponible si la période précédente est hors des données ou vide (`no_comparable_period`) ; une couverture partielle est signalée (`previous_partial`). |
| Mois absents | Points absents (trous) dans les courbes, jamais 0. |
| Décimales | Montants en `DECIMAL(38, s)` dans DuckDB et `decimal.Decimal` en Python ; médiane calculée avec une décimale de plus pour rester exacte ; arrondi uniquement à l'affichage (Intl.NumberFormat sur chaînes, ReportLab sur Decimal). |
| Devises | Montants de devises différentes : résultats séparés par devise (colonne devise, symbole par ligne, ou devise de colonne). Aucune conversion ; combiner deux colonnes de devises différentes est refusé. Une devise inconnue fait l'objet d'une question. |
| HT/TTC, annulations, remboursements, avoirs | Jamais devinés : si deux bases de montant existent, ou si une colonne contient des valeurs d'annulation/remboursement, une question est posée (exclure / inclure). |
| Grains différents | Un montant constant au sein d'une commande et répété sur chaque ligne déclenche une question ; « une fois par commande » applique `per`. Le moteur vérifie que la valeur est constante par clé (`grain_conflict`) et refuse de répartir une clé sur plusieurs catégories (`grain_dimension_conflict`). |
| Totaux et sous-totaux | Lignes dont une cellule texte commence par un mot-clé de total (6 langues) ou ligne sans libellé égale à la somme des lignes précédentes : exclues de façon visible et réversible. |
| Doublons | Jamais supprimés automatiquement ; signalés, et une question propose de les exclure. |
| Jointures | Uniquement « plusieurs vers un » : clé de droite unique, types de clé compatibles, nombre de lignes et sommes identiques avant/après, sinon blocage. |
| Filtre global | S'applique aux cartes, graphiques, tableau détaillé, preuve et exports. |
| Périmètre complet | Tous les calculs portent sur la totalité des lignes retenues ; le modèle ne reçoit qu'un profil, jamais un échantillon utilisé pour calculer. |
| KPI impossible | Le modèle doit le déclarer `unavailable` avec les données manquantes ; l'interface affiche les champs manquants et propose les suggestions et le constructeur guidé. Un champ inexistant ou une opération incompatible rend le plan invalide (jamais d'approximation). |

## Normalisations sûres de la sortie du modèle
Uniquement structurelles et déterministes : si une seule colonne date existe et que le plan demande une variation
ou une courbe temporelle sans période, cette colonne devient la période ; un graphique dont la dimension est cette
colonne devient un graphique par période. Le plan obtenu est toujours montré à l'utilisateur, qui le valide.
