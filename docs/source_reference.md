# Référence des sources

Ce document décrit **ce que sont les données** et d'où elles viennent. Le *comment* est dans
[`architecture.md`](architecture.md), le *pourquoi* des choix dans [`methodology.md`](methodology.md).

## Sources officielles

| Clé du projet | Contenu | Fichier local | URL stable | HTTP attendu |
|---|---|---|---|---:|
| `stock_unite_legale` | SIRENE — unités légales (état courant) | `data/raw/stock_unite_legale.parquet` | `https://www.data.gouv.fr/api/1/datasets/r/350182c9-148a-46e0-8389-76c2ec1374a3` | **302** |
| `stock_etablissement` | SIRENE — établissements (état courant) | `data/raw/stock_etablissement.parquet` | `https://www.data.gouv.fr/api/1/datasets/r/a29c1297-1f92-4e2a-8f6b-8c902ce96c5f` | **302** |
| `stock_unite_legale_historique` | SIRENE — périodes des unités légales | `data/raw/stock_unite_legale_historique.parquet` | `https://www.data.gouv.fr/api/1/datasets/r/1b9290ed-d0bc-461f-ba31-0250a99cc140` | **302** |
| `stock_etablissement_historique` | SIRENE — périodes des établissements | `data/raw/stock_etablissement_historique.parquet` | `https://www.data.gouv.fr/api/1/datasets/r/2b3a0c79-f97b-46b8-ac02-8be6c1f01a8c` | **302** |
| `cog_communes_2026` | COG 2026 — communes | `data/reference/v_commune_2026.csv` | `https://www.insee.fr/fr/statistiques/fichier/8740222/v_commune_2026.csv` | **200** |

Les URL SIRENE sont des **liens stables** qui *redirigent* (HTTP 302) vers le stockage du fichier courant : l'URL finale
change à chaque mise à jour, pas le lien. Le COG est servi **directement** (HTTP 200). Le vérificateur
`check_sources` distingue explicitement les deux comportements (une redirection inattendue sur le COG, ou l'absence de
redirection sur SIRENE, est signalée).

> **Statut de validation.** Les statuts 302/200 ont été observés par l'auteur du projet le 2026-10-01 (journal
> d'exécution Windows). Ils n'ont pas pu être reproduits lors de la dernière revue : le réseau sortant de l'environnement
> de développement était bloqué pour `data.gouv.fr` et `insee.fr`. Relancer `check_sources` pour les confirmer.

## Schémas inspectés et confirmés

Mesurés par lecture du pied de page Parquet via requêtes HTTP `Range`
([`schema_snapshot_2026-10-01.json`](schema_snapshot_2026-10-01.json)) :

| Source | Lignes | Colonnes |
|---|---:|---:|
| `StockUniteLegale` | 30 148 401 | 35 |
| `StockEtablissement` | 44 282 364 | 54 |
| `StockUniteLegaleHistorique` | 72 004 236 | 28 |
| `StockEtablissementHistorique` | 96 657 561 | 18 |

**Validation réelle.** Le snapshot versionné reste le garde-fou de dérive, mais les quatre fichiers ont ensuite été téléchargés et inspectés localement. Les schémas historiques de **28** et **18** colonnes ont donc été confirmés par l’exécution réelle de `inspect_schema --local` et par `build_mart`. Les identifiants (`siren`, `siret`, codes géographiques) sont conservés comme chaînes ; les dates Parquet observées sont typées explicitement et normalisées par la transformation.

## COG 2026

Fichier `v_commune_2026.csv` (séparateur « , », UTF-8). Colonnes utilisées :

| Colonne | Signification | Remarque |
|---|---|---|
| `TYPECOM` | type de commune | `COM`, `ARM` (arrondissement), `COMA` (associée), `COMD` (déléguée) |
| `COM` | **code commune** | **chaîne** : `2A001` (Corse-du-Sud), `01004` (zéro initial) |
| `DEP` | **département** | chaîne : `01`, `2A`, `974` |
| `REG` | **région** | chaîne : `84`, `04` (zéro initial) |
| `LIBELLE` | nom de la commune | |
| `COMPARENT` | commune parente (COMA/COMD/ARM) | sert à hériter de DEP/REG si absents |

Toutes les colonnes sont lues en `String` (`infer_schema=False`) : aucun code géographique n'est converti en entier.
Si un même code apparaît plusieurs fois, la commune actuelle l'emporte (`COM` > `ARM` > `COMA` > `COMD`).
Les libellés de régions/départements ne sont pas dans ce fichier : ils viennent d'un référentiel statique du dépôt
(`transformation/geo_labels.py`, 18 régions, 101 départements), à remplacer au besoin par les fichiers
`v_region_*.csv` / `v_departement_*.csv` du COG.

## Nomenclatures

| Variable | Valeurs rencontrées | Usage dans le projet |
|---|---|---|
| `nomenclatureActivitePrincipale…` | `NAFRev2`, `NAFRev1`, `NAF1993`, `NAP` (+ absence) | identifie la nomenclature du code ; **analyse sectorielle en `NAFRev2` uniquement** |
| `activitePrincipaleNAF25…` | code NAF 2025 | conservé tel quel, **dimension séparée**, aucune table de conversion inventée |
| `trancheEffectifs…` | `NN`, `00`, `01` … `53` | 16 tranches officielles ; `NN` = non employeur ; absence = non renseigné |
| `etatAdministratifEtablissement` | `A` actif, `F` fermé | |
| `etatAdministratifUniteLegale` | `A` active, `C` cessée | |
| `categorieEntreprise` | `PME`, `ETI`, `GE` | |
| `categorieJuridiqueUniteLegale` | code à 4 chiffres | libellés du **niveau I** embarqués (1er chiffre) |

Pour la NAF 2025 : l'entrée en vigueur est annoncée au 1er janvier 2027 (à confirmer sur insee.fr). Les périodes
historiques conservent la nomenclature applicable à la période.

## Licence et réutilisation

Données ouvertes (Licence Ouverte / Etalab) publiées par l'Insee sur data.gouv.fr — vérifier les conditions
actuelles sur la fiche du jeu de données avant toute republication. Ce dépôt ne contient **aucune donnée SIRENE** :
uniquement du code et des fixtures synthétiques (`tests/fixtures/`).
