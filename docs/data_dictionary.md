# Dictionnaire de données

Définit **chaque colonne** des couches du projet et justifie sa présence. Les règles de calcul sont dans
[`methodology.md`](methodology.md) ; la structure technique dans [`architecture.md`](architecture.md).

Principe : **aucune colonne source n'est conservée « au cas où »**. Sur 54 colonnes de `StockEtablissement`, 16 sont lues ;
sur 35 de `StockUniteLegale`, 15. Les historiques : 9 sur 18 et 7 sur 28.

## 1. Couche courante — établissements (`etablissement_current.parquet`)

Une ligne par SIRET. Autant de lignes que la source (aucune ligne filtrée).

| Colonne | Type | Origine | Justification / usage |
|---|---|---|---|
| `siren`, `nic`, `siret` | String | source | identifiants ; `str.zfill` restaure les zéros initiaux (9 / 5 / 14) ; contrôle `siret = siren + nic` |
| `dateCreationEtablissement` | Date | source | date de création **enregistrée** ; événements de création |
| `trancheEffectifsEtablissement` | String | source | tranche d'effectifs (code officiel) |
| `anneeEffectifsEtablissement` | Int64 | source | année de référence de la tranche ; **conservée pour la traçabilité, non utilisée dans les marts** |
| `etablissementSiege` | Boolean | source | siège ou non ; conservée (cahier des charges), non utilisée dans les marts actuels |
| `codePostalEtablissement`, `libelleCommuneEtablissement` | String | source | **diagnostic** des communes non appariées au COG ; non utilisées dans les marts |
| `codeCommuneEtablissement` | String | source | **clé géographique** vers le COG (chaîne : `2A001`) |
| `dateDebut` | Date | source | début de la période courante ; conservée (cahier des charges) |
| `etatAdministratifEtablissement` | String | source | `A` actif · `F` fermé |
| `activitePrincipaleEtablissement` | String | source | code d'activité (dans la nomenclature de la ligne suivante) |
| `nomenclatureActivitePrincipaleEtablissement` | String | source | `NAFRev2`, `NAFRev1`, `NAF1993`, `NAP` |
| `caractereEmployeurEtablissement` | String | source | caractère employeur ; conservée (cahier des charges), non utilisée dans les marts |
| `activitePrincipaleNAF25Etablissement` | String | source | **NAF 2025**, dimension séparée, jamais convertie |
| `date_creation_invalid` | Boolean | dérivée | vrai si la date source est renseignée mais illisible (la ligne est conservée) ; les dates lisibles mais < 1900 ou > snapshot sont conservées et signalées par le quality gate |
| `naf_nomenclature` | String | dérivée | copie nettoyée de la nomenclature (clé du mart) |
| `naf_section` | String | dérivée | section NAF **Rev.2 uniquement** ; nulle sinon |
| `is_naf_rev2` | Boolean | dérivée | nomenclature `NAFRev2` **et** code au format `99.99A` |
| `creation_year` | Int32 | dérivée | année de `dateCreationEtablissement` |
| `is_active`, `is_closed` | Boolean | dérivées | `état = A` / `état = F` |
| `workforce_label` | String | dérivée | libellé de la tranche (nul si code inconnu) |
| `type_commune`, `code_departement`, `code_region`, `commune` | String | COG | `TYPECOM`, `DEP`, `REG`, `LIBELLE` ; nuls si non apparié |
| `geo_status` | String | dérivée | `MATCHED` · `UNMATCHED` · `NO_COMMUNE` · `NOT_ENRICHED` |

## 2. Couche courante — unités légales (`unite_legale_current.parquet`)

Une ligne par SIREN. Mêmes principes.

| Colonne | Type | Origine | Justification / usage |
|---|---|---|---|
| `siren` | String | source | identifiant (9 chiffres) |
| `dateCreationUniteLegale` | Date | source | date de création enregistrée |
| `categorieEntreprise` | String | source | `PME` · `ETI` · `GE` |
| `categorieJuridiqueUniteLegale` | String | source | code à 4 chiffres ; niveau I = 1er chiffre |
| `etatAdministratifUniteLegale` | String | source | `A` active · `C` cessée |
| `trancheEffectifsUniteLegale`, `anneeEffectifsUniteLegale` | String / Int64 | source | effectifs et année de référence ; non utilisées dans les marts |
| `activitePrincipaleUniteLegale`, `nomenclatureActivitePrincipaleUniteLegale` | String | source | activité et sa nomenclature |
| `activitePrincipaleNAF25UniteLegale` | String | source | NAF 2025, séparée |
| `dateDebut`, `nombrePeriodesUniteLegale` | Date / Int64 | source | période courante ; nombre de périodes ; conservées (cahier des charges) |
| `caractereEmployeurUniteLegale`, `economieSocialeSolidaireUniteLegale`, `societeMissionUniteLegale` | String | source | indicateurs `O`/`N` ; conservés (cahier des charges), non utilisés dans les marts actuels |
| `date_creation_invalid`, `naf_nomenclature`, `naf_section`, `is_naf_rev2`, `creation_year`, `is_active`, `is_ceased`, `workforce_label` | dérivées | | mêmes définitions que ci-dessus (`is_ceased` : état `C`) |

## 3. Marts (Parquet) — tables de faits

Colonnes **identiques** au staging SQL (contrat unique, voir §5). Ordre = ordre des colonnes du contrat.

* `fact_establishment_snapshot` : `date_key`, `commune_code`, `commune_name`, `department_code`, `region_code`,
  `geo_status`, `naf_nomenclature`, `naf_code`, `naf_section`, `workforce_code`, `workforce_label`, `status_code`,
  `establishment_count`.
* `fact_legal_unit_snapshot` : `date_key`, `naf_nomenclature`, `naf_code`, `naf_section`, `legal_category_code`,
  `enterprise_category`, `status_code`, `legal_unit_count`.
* `fact_administrative_events` : `event_year`, `entity_type`, `event_type`, `naf_nomenclature`, `naf_code`,
  `naf_section`, `event_count`.

### Sentinelles (marts uniquement)

| Valeur | Remplace | Largeur nécessaire |
|---|---|---:|
| `UNKNOWN` | code absent (commune, département, région, NAF, nomenclature, effectif, catégorie juridique/entreprise) | **7** |
| `U` | état administratif absent | 1 |
| `Non renseigné` | libellé absent (commune, effectif) | 13 |

### Valeurs de `event_type` / `entity_type`

| `event_type` | `entity_type` | Définition |
|---|---|---|
| `Cessation administrative` | `Unité légale` | période dont l'état devient `C` (changement vrai) |
| `Fermeture administrative` | `Établissement` | période dont l'état devient `F` (changement vrai) |
| `Date de création enregistrée` | `Établissement` | `dateCreationEtablissement` dans la fenêtre |

> ⚠ Ces trois types **ne s'additionnent jamais** (voir `methodology.md` §4.2).

## 4. Modèle analytique (`analytics.*`)

| Table | Colonnes (type) |
|---|---|
| `DimDate` | `DateKey int` PK · `FullDate date` · `CalendarYear smallint` · `CalendarQuarter tinyint` · `CalendarMonth tinyint` · `MonthName nvarchar(20)` |
| `DimGeography` | `CommuneCode varchar(10)` PK · `CommuneName nvarchar(200)` · `DepartmentCode varchar(10)` · `DepartmentName nvarchar(100)` · `RegionCode varchar(10)` · `RegionName nvarchar(100)` · `GeoStatus varchar(20)` |
| `DimActivity` | `ActivityKey int` PK · `NafNomenclature varchar(20)` · `NafCode varchar(20)` · `NafSection char(1) NULL` · `NafSectionLabel nvarchar(150)` · `IsNafRev2 bit` — unique `(NafNomenclature, NafCode)` |
| `DimWorkforce` | `WorkforceCode varchar(10)` PK · `WorkforceLabel nvarchar(100)` · `SortOrder smallint` |
| `DimLegalCategory` | `LegalCategoryCode varchar(10)` PK · `Level1Code char(1) NULL` · `Level1Label nvarchar(100)` |
| `FactEstablishmentSnapshot` | `DateKey`, `CommuneCode`, `ActivityKey`, `WorkforceCode`, `StatusCode char(1)` (CHECK `A/F/U`), `EstablishmentCount bigint` (CHECK > 0) |
| `FactLegalUnitSnapshot` | `DateKey`, `ActivityKey`, `LegalCategoryCode`, `EnterpriseCategory varchar(10)`, `StatusCode char(1)` (CHECK `A/C/U`), `LegalUnitCount bigint` |
| `FactAdministrativeEvents` | `EventYear smallint`, `EntityType nvarchar(40)`, `EventType nvarchar(60)`, `ActivityKey`, `EventCount bigint` |

`StatusCode` est **étroit par conception** (`char(1)` + `CHECK`) : une valeur inattendue fait échouer le chargement de façon
bruyante au lieu d'être tronquée. Pour toutes les autres colonnes, la largeur de l'aval est **supérieure ou égale** à celle
de l'amont (vérifié par `tests/test_sql_scripts.py`).

## 5. Staging SQL Server (`stg.*`) — contrat de largeurs

Source de vérité : `src/france_business_dynamics/sql_contract.py` (copie exacte dans `sql/04_create_facts.sql`).
Le staging est **large et permissif** ; les largeurs retenues couvrent les valeurs réelles *et* les sentinelles.

Pourquoi ces largeurs : `commune_code`/`department_code`/`region_code` `varchar(10)` (code COG 5, département 2–3,
sentinelle `UNKNOWN` = 7) ; `naf_nomenclature`/`naf_code` `varchar(20)` (valeurs réelles ≤ 7 caractères) ;
`workforce_label` `nvarchar(100)` (« Non employeur », « 10 000 salariés et plus ») ; `status_code` `varchar(5)`
(permissif ; contrôlé en `analytics` par `CHECK`). Les colonnes `varchar` n'acceptent que de l'**ASCII** (contrôlé avant
insertion) ; les libellés accentués sont en `nvarchar`.

### `stg.EstablishmentSnapshot`

Établissements par commune × activité × tranche d'effectifs × état.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `date_key` | `int` | non |
| `commune_code` | `varchar(10)` | non |
| `commune_name` | `nvarchar(200)` | non |
| `department_code` | `varchar(10)` | non |
| `region_code` | `varchar(10)` | non |
| `geo_status` | `varchar(20)` | non |
| `naf_nomenclature` | `varchar(20)` | non |
| `naf_code` | `varchar(20)` | non |
| `naf_section` | `char(1)` | oui |
| `workforce_code` | `varchar(10)` | non |
| `workforce_label` | `nvarchar(100)` | non |
| `status_code` | `varchar(5)` | non |
| `establishment_count` | `bigint` | non |

### `stg.LegalUnitSnapshot`

Unités légales par activité × catégorie juridique × catégorie d'entreprise × état.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `date_key` | `int` | non |
| `naf_nomenclature` | `varchar(20)` | non |
| `naf_code` | `varchar(20)` | non |
| `naf_section` | `char(1)` | oui |
| `legal_category_code` | `varchar(10)` | non |
| `enterprise_category` | `varchar(10)` | non |
| `status_code` | `varchar(5)` | non |
| `legal_unit_count` | `bigint` | non |

### `stg.AdministrativeEvents`

Changements d'état administratif et dates de création enregistrées, par année.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `event_year` | `smallint` | non |
| `entity_type` | `nvarchar(40)` | non |
| `event_type` | `nvarchar(60)` | non |
| `naf_nomenclature` | `varchar(20)` | non |
| `naf_code` | `varchar(20)` | non |
| `naf_section` | `char(1)` | oui |
| `event_count` | `bigint` | non |

### `stg.RefRegion`

Libellés des régions (référentiel statique du dépôt).

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `region_code` | `varchar(10)` | non |
| `region_name` | `nvarchar(100)` | non |

### `stg.RefDepartment`

Libellés des départements.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `department_code` | `varchar(10)` | non |
| `department_name` | `nvarchar(100)` | non |

### `stg.RefNafSection`

Libellés des 21 sections NAF Rev.2.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `naf_section` | `char(1)` | non |
| `naf_section_label` | `nvarchar(150)` | non |

### `stg.RefLegalCategoryLevel1`

Catégories juridiques — niveau I.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `level1_code` | `char(1)` | non |
| `level1_label` | `nvarchar(100)` | non |

### `stg.RefWorkforce`

Tranches d'effectifs et ordre de tri.

| Colonne | Type SQL | Nullable |
|---|---|:---:|
| `workforce_code` | `varchar(10)` | non |
| `workforce_label` | `nvarchar(100)` | non |
| `sort_order` | `smallint` | non |
## 6. Référentiels de libellés

| Table de staging | Source Python | Lignes |
|---|---|---:|
| `stg.RefRegion` | `transformation/geo_labels.py::REGION_LABELS` | 18 |
| `stg.RefDepartment` | `DEPARTMENT_LABELS` | 101 |
| `stg.RefNafSection` | `transformation/naf.py::SECTION_LABELS` | 21 |
| `stg.RefLegalCategoryLevel1` | `transformation/legal.py::LEGAL_CATEGORY_LEVEL1` | 9 |
| `stg.RefWorkforce` | `transformation/workforce.py::WORKFORCE_LABELS` | 16 |
