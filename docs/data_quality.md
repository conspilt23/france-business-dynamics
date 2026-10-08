# Contrôle qualité

Ce document décrit **ce qui est contrôlé**, **quand**, et **comment lire le résultat**. La méthodologie est dans
[`methodology.md`](methodology.md) ; l'exécution dans [`execution_checklist.md`](execution_checklist.md).

## Trois filets successifs

| Étape | Outil | Quand | Objet |
|---|---|---|---|
| 1. Intégrité du téléchargement | `download_sources` | à chaque téléchargement | taille, SHA-256, format Parquet/CSV |
| 2. **Quality gate** | `quality_gate` | après `transform` / `build_mart`, avant le chargement SQL | couches courantes et marts |
| 3. Pré-contrôle du chargement | `load_to_db` | avant toute insertion | conformité au contrat SQL, schéma vivant, comptage |
| 4. Contrôles SQL | `07_quality_checks.sql` | après `06_load_mart.sql` | réconciliation, intégrité, méthodologie |

## Lire un résultat

| Statut | Sens | Conséquence |
|---|---|---|
| **PASS** | contrôle satisfait | — |
| **WARN** | anomalie *statistique* (taux au-dessus d'un seuil, format atypique) | signalée, **ne bloque pas** |
| **FAIL** | violation d'*intégrité* | **bloque** (code de sortie 1) |
| **SKIPPED** | couche absente (avec la commande pour la produire) | ne bloque pas ; bloque avec `--strict` |

### Portée : échantillon ou complet

Le rapport indique pour chaque couche si elle est un **échantillon** (`--limit`) ou un traitement **complet**
(`pipeline_manifest.json`). Un `PASS` sur 10 000 lignes est **explicitement étiqueté** (`contains_samples: true`) et ne
vaut pas validation du jeu complet. Le notebook 02 l'affiche en bandeau.

### Couches manquantes

`python -m france_business_dynamics.quality_gate` contrôle **ce qui existe** et marque le reste `SKIPPED`
(`complete: false`). `--strict` exige les cinq couches (à utiliser avant le chargement SQL). `--stage curated|marts`
restreint le périmètre. Code de sortie `2` s'il n'y a rien du tout à contrôler.

## Seuils d'avertissement

| Constante (`validation/quality.py`) | Valeur | Contrôle concerné |
|---|---:|---|
| `UNMATCHED_GEO_WARN_RATE` | 0,5 % | codes commune absents du COG 2026 |
| `UNKNOWN_COMMUNE_WARN_RATE` | 1 % | établissements sans commune dans le mart |
| `INVALID_DATE_WARN_RATE` | 0,01 % | dates de création illisibles |

Ces seuils ont été **exercés sur le stock complet** du snapshot de référence. Ils restent des seuils de contrôle du projet — pas des vérités métier universelles — et devront être réévalués si la distribution des futurs snapshots change.
Les dates lisibles mais **antérieures à 1900** ou **postérieures au snapshot** sont contrôlées séparément : elles ne sont pas supprimées ni corrigées automatiquement. Le code `00.00Z` est traité comme activité non classée et n'est pas compté comme erreur de mapping de section NAF Rev.2.

Les ajuster après le premier traitement complet (et le consigner dans `FINAL_VALIDATION.md`).

## Catalogue des contrôles du quality gate

Le catalogue ci-dessous décrit les contrôles produits par le quality gate. Les contrôles `mart_*` s'appliquent
aux trois marts avec leurs propres colonnes. Les fixtures présentes sous `tests/fixtures/` servent uniquement à automatiser les tests.

| Identifiant | Contrôle | Gravité |
|---|---|---|
| `etablissement.commune_format` | Code commune = 5 chiffres ou 2A/2B + 3 chiffres | avertissement (WARN) |
| `etablissement.date_creation` | Dates de création invalides (renseignées mais illisibles) | avertissement (WARN) |
| `etablissement.date_creation_before_1900` | Dates de création antérieures au 01/01/1900 (valeurs conservées, à auditer) | avertissement (WARN) |
| `etablissement.date_creation_after_snapshot` | Dates de création postérieures au snapshot SIRENE | avertissement (WARN) |
| `etablissement.etat_null` | État administratif manquant | avertissement (WARN) |
| `etablissement.etat_values` | État administratif ∈ {A, F} (ou nul) | **erreur** (FAIL) |
| `etablissement.geo_enriched` | Couche enrichie avec le COG 2026 | avertissement (WARN) |
| `etablissement.geo_unmatched` | Codes commune absents du COG 2026 | avertissement (WARN) |
| `etablissement.naf_nomenclature_known` | Nomenclature NAF dans ['NAFRev2', 'NAFRev1', 'NAF1993', 'NAP', 'NAF2025'] | avertissement (WARN) |
| `etablissement.naf_section_consistency` | Aucune section NAF sans nomenclature NAFRev2 bien formée | **erreur** (FAIL) |
| `etablissement.nic_format` | NIC = 5 chiffres | **erreur** (FAIL) |
| `etablissement.no_section_outside_rev2` | Aucune section NAF Rev.2 attribuée à NAFRev1 / NAF1993 / NAP / NAF2025 | **erreur** (FAIL) |
| `etablissement.no_sentinel` | Aucune sentinelle UNKNOWN dans la couche courante | **erreur** (FAIL) |
| `etablissement.not_empty` | La couche contient des lignes | **erreur** (FAIL) |
| `etablissement.rev2_code_format` | Code NAFRev2 au format 99.99A (sinon section nulle) | avertissement (WARN) |
| `etablissement.rev2_section_unmapped` | Code NAFRev2 bien formé sans section officielle, hors code non classé `00.00Z` | avertissement (WARN) |
| `etablissement.siren_format` | SIREN = 9 chiffres | **erreur** (FAIL) |
| `etablissement.siret_consistency` | SIRET = SIREN + NIC | **erreur** (FAIL) |
| `etablissement.siret_format` | SIRET = 14 chiffres | **erreur** (FAIL) |
| `etablissement.siret_unique` | SIRET unique (une ligne par établissement) | **erreur** (FAIL) |
| `etablissement.workforce_codes` | Tranche d'effectifs dans la nomenclature officielle | avertissement (WARN) |
| `unite_legale.date_creation` | Dates de création invalides | avertissement (WARN) |
| `unite_legale.date_creation_before_1900` | Dates de création antérieures au 01/01/1900 (valeurs conservées, à auditer) | avertissement (WARN) |
| `unite_legale.date_creation_after_snapshot` | Dates de création postérieures au snapshot SIRENE | avertissement (WARN) |
| `unite_legale.etat_null` | État administratif manquant | avertissement (WARN) |
| `unite_legale.etat_values` | État administratif ∈ {A, C} (ou nul) | **erreur** (FAIL) |
| `unite_legale.naf_nomenclature_known` | Nomenclature NAF dans ['NAFRev2', 'NAFRev1', 'NAF1993', 'NAP', 'NAF2025'] | avertissement (WARN) |
| `unite_legale.naf_section_consistency` | Aucune section NAF sans nomenclature NAFRev2 bien formée | **erreur** (FAIL) |
| `unite_legale.no_section_outside_rev2` | Aucune section NAF Rev.2 attribuée à NAFRev1 / NAF1993 / NAP / NAF2025 | **erreur** (FAIL) |
| `unite_legale.not_empty` | La couche contient des lignes | **erreur** (FAIL) |
| `unite_legale.rev2_code_format` | Code NAFRev2 au format 99.99A (sinon section nulle) | avertissement (WARN) |
| `unite_legale.rev2_section_unmapped` | Code NAFRev2 bien formé sans section officielle, hors code non classé `00.00Z` | avertissement (WARN) |
| `unite_legale.siren_format` | SIREN = 9 chiffres | **erreur** (FAIL) |
| `unite_legale.siren_unique` | SIREN unique (une ligne par unité légale) | **erreur** (FAIL) |
| `mart_etablissement.date_key` | date_key = 20261001 (snapshot 2026-10-01) | **erreur** (FAIL) |
| `mart_etablissement.grain_unique` | Grain unique ['date_key', 'commune_code', 'naf_nomenclature', 'naf_code', 'workforce_code', 'status_code'] | **erreur** (FAIL) |
| `mart_etablissement.no_section_outside_rev2` | Aucune section NAF Rev.2 attribuée à une autre nomenclature | **erreur** (FAIL) |
| `mart_etablissement.not_empty` | Le mart contient des lignes | **erreur** (FAIL) |
| `mart_etablissement.positive_counts` | Mesures strictement positives | **erreur** (FAIL) |
| `mart_etablissement.reconciliation` | Réconciliation : somme des mesures = lignes de etablissement_current (26) | **erreur** (FAIL) |
| `mart_etablissement.sql_contract` | Conformité au contrat SQL stg.EstablishmentSnapshot (colonnes, types, largeurs, nulls) | **erreur** (FAIL) |
| `mart_etablissement.status_values` | État ∈ ['A', 'F', 'U'] | **erreur** (FAIL) |
| `mart_etablissement.unknown_commune` | Établissements sans commune (sentinelle UNKNOWN) | avertissement (WARN) |
| `mart_evenements.event_entity_pairing` | Cessation ↔ unité légale ; fermeture/création ↔ établissement | **erreur** (FAIL) |
| `mart_evenements.event_types` | Types d'événement et d'entité connus | **erreur** (FAIL) |
| `mart_evenements.grain_unique` | Grain unique ['event_year', 'entity_type', 'event_type', 'naf_nomenclature', 'naf_code'] | **erreur** (FAIL) |
| `mart_evenements.no_section_outside_rev2` | Aucune section NAF Rev.2 attribuée à une autre nomenclature | **erreur** (FAIL) |
| `mart_evenements.not_empty` | Le mart contient des lignes | **erreur** (FAIL) |
| `mart_evenements.positive_counts` | Mesures strictement positives | **erreur** (FAIL) |
| `mart_evenements.sql_contract` | Conformité au contrat SQL stg.AdministrativeEvents (colonnes, types, largeurs, nulls) | **erreur** (FAIL) |
| `mart_evenements.year_window` | Année d'événement dans 2021–2025 | **erreur** (FAIL) |
| `mart_unite_legale.date_key` | date_key = 20261001 | **erreur** (FAIL) |
| `mart_unite_legale.grain_unique` | Grain unique ['date_key', 'naf_nomenclature', 'naf_code', 'legal_category_code', 'enterprise_category', 'status_code'] | **erreur** (FAIL) |
| `mart_unite_legale.no_section_outside_rev2` | Aucune section NAF Rev.2 attribuée à une autre nomenclature | **erreur** (FAIL) |
| `mart_unite_legale.not_empty` | Le mart contient des lignes | **erreur** (FAIL) |
| `mart_unite_legale.positive_counts` | Mesures strictement positives | **erreur** (FAIL) |
| `mart_unite_legale.reconciliation` | Réconciliation : somme des mesures = lignes de unite_legale_current (14) | **erreur** (FAIL) |
| `mart_unite_legale.sql_contract` | Conformité au contrat SQL stg.LegalUnitSnapshot (colonnes, types, largeurs, nulls) | **erreur** (FAIL) |
| `mart_unite_legale.status_values` | État ∈ ['A', 'C', 'U'] | **erreur** (FAIL) |

Contrôle `*.sql_contract` : le mart respecte le contrat de staging (colonnes présentes et attendues, types, **largeur
maximale observée ≤ largeur SQL**, NOT NULL, ASCII des `varchar`). C'est ce contrôle qui aurait détecté **avant** l'insertion
l'erreur historique `String data, right truncation: length 14 buffer 6`.

## Contrôles SQL (`07_quality_checks.sql`)

Un seul jeu de résultats `CheckName | Status | Observed | Expected` (aucune exception levée). 13 contrôles :
faits non vides (établissements : obligatoire ; autres : avertissement si l'étape n'a pas été exécutée) ;
**réconciliation staging → faits** pour les trois faits ; absence de clés orphelines ; **aucune section NAF Rev.2 hors
`NAFRev2`** ; **dépendance fonctionnelle** des attributs géographiques au code commune ; part de communes inconnues ≤ 1 % ;
libellés de région manquants ; `DimDate` renseignée ; années d'événements dans 2021–2025.
`python -m france_business_dynamics.sql_runner --stage finalize` les interprète (code de sortie 1 s'il y a un `FAIL`).

## Comment ces contrôles ont eux-mêmes été vérifiés

* Des **défauts plantés** (doublon de clé, SIRET mal formé, état inattendu, section Rev.2 sur NAFRev1, valeur plus large
  que la colonne SQL, ligne perdue entre couche et mart…) doivent faire échouer le gate : `tests/test_quality.py`,
  `tests/test_quality_gate.py`.
* **Tests de mutation manuels** : retirer le `ROLLBACK`, le comptage final ou rétrécir une largeur du contrat fait
  échouer des tests précis (voir `FINAL_VALIDATION.md`).
* Anomalies **volontairement présentes** dans les fixtures (date invalide, commune hors COG, NAFRev2 mal formé…) : le gate
  doit les compter, et il le fait (6 avertissements dans l'exemple).

## Ajouter un contrôle

1. Ajouter l'expression dans `check_establishment_curated` / `check_legal_unit_curated` / `check_mart` (une seule passe
   agrégée par couche) avec `severity="error"` (intégrité) ou `"warning"` (statistique).
2. Ajouter un test **qui plante le défaut** et vérifie le `FAIL`/`WARN` (`tests/test_quality.py`).
3. Régénérer l'exemple et ce catalogue.
