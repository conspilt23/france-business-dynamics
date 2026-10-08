# Méthodologie

Ce document explique **ce qui est mesuré, comment, et jusqu'où l'on peut interpréter les résultats**.
Pour la structure technique : [`architecture.md`](architecture.md). Pour les contrôles : [`data_quality.md`](data_quality.md).

## 1. Périmètre

* **Vue courante** : stock SIRENE à la date de snapshot (`SNAPSHOT_DATE`, 2026-10-01), identifiée par `date_key`.
* **Fenêtre d'analyse des événements** : 2021 – 2025 (`ANALYSIS_START_YEAR` / `ANALYSIS_END_YEAR`).
* **Sources** : quatre Parquet SIRENE + COG 2026 ([`source_reference.md`](source_reference.md)).

## 2. Grains des tables de faits

| Mart | Grain (clé fonctionnelle) | Mesure |
|---|---|---|
| `fact_establishment_snapshot` | `date_key × commune_code × naf_nomenclature × naf_code × workforce_code × status_code` | établissements |
| `fact_legal_unit_snapshot` | `date_key × naf_nomenclature × naf_code × legal_category_code × enterprise_category × status_code` | unités légales |
| `fact_administrative_events` | `event_year × entity_type × event_type × naf_nomenclature × naf_code` | événements |

Les libellés (commune, département, région, section NAF, effectifs) sont **fonctionnellement dépendants** de la clé :
ils n'augmentent pas le nombre de lignes. L'unicité du grain est contrôlée à chaque exécution.

## 3. Questions analytiques et ce qui y répond réellement

| # | Question | Source | Réponse apportée | Limite |
|---|---|---|---|---|
| 1 | Évolution du tissu 2021–2025 | événements | **flux** annuels (créations enregistrées, fermetures, cessations) | **pas un stock** reconstruit ; voir §6 |
| 2 | Répartition région / département / commune | marts établissements | oui, état courant | communes non appariées au COG isolées |
| 3 | Secteurs NAF dominants | marts établissements | sections **NAF Rev.2** | hors NAFRev2 isolé dans « Hors NAF Rev.2 » |
| 4 | Structure par effectifs | marts établissements | tranches officielles | année de référence variable |
| 5 | Changements d'état | événements (historiques) | cessations (UL), fermetures (établ.) | pas une mortalité |
| 6 | Catégories juridiques × secteurs | mart unités légales | niveau I des catégories | libellés détaillés non embarqués |

## 4. Règles méthodologiques

### 4.1 Date de création ≠ création d'entreprises (statistiques officielles)

`dateCreation…` est une **variable du répertoire**. Les statistiques officielles de démographie d'entreprises de l'Insee
reposent sur des concepts et des sources dédiés. L'indicateur du projet s'appelle donc **« Date de création
enregistrée »** (jamais « créations d'entreprises »). Les unités purgées (colonne `unitePurgeeUniteLegale`) pourraient
être mal couvertes : biais de survie possible sur les années anciennes, à vérifier dans la documentation Insee.

### 4.2 Cessation et fermeture administratives ≠ mortalité économique

* **Cessation administrative** (unité légale) : une *période* dont l'état devient `C` avec `changementEtatAdministratif…`
  vrai ; date = `dateDebut` de la période.
* **Fermeture administrative** (établissement) : idem, état `F`.

Ces événements **ne mesurent pas la disparition économique d'une entreprise** : un établissement ou une unité peut être
réactivé (les réactivations ne sont pas comptées, mais une nouvelle fermeture l'est), une cessation peut être purement
administrative, et la durée de vie n'est pas modélisée. On compte des **événements, pas des entités distinctes**.
En règle générale, la cessation d'une unité légale s'accompagne de fermetures d'établissements : **les deux séries ne
s'additionnent jamais**, ni avec les dates de création.

### 4.3 NAF : jamais de mélange de nomenclatures

`nomenclatureActivitePrincipale…` identifie la nomenclature du code. La **section NAF** (A–U) n'est calculée que si
la nomenclature est `NAFRev2` *et* que le code a le format Rev.2 (`99.99A`). Pour `NAFRev1`, `NAF1993`, `NAP` et
`NAF2025`, la section est **nulle** : les divisions ont changé de sens entre nomenclatures (par exemple, la division 72
désigne les activités informatiques en NAF Rev.1 mais la recherche-développement en NAF Rev.2 — l'audit du projet avait
trouvé exactement ce défaut). **Aucune table de conversion non fournie par les données n'est inventée.**

* `DimActivity` est unique sur **(nomenclature, code)**.
* Les établissements hors NAFRev2 forment une ligne explicite (« Hors NAF Rev.2 ou non classé ») : la somme des
  secteurs réconcilie toujours avec le total.
* La **NAF 2025** (`activitePrincipaleNAF25…`) est conservée comme **colonne séparée** de la couche courante. Elle n'est
  pas utilisée comme dimension d'analyse dans cette version (voir [`roadmap.md`](roadmap.md)) ; elle ne doit jamais être
  fusionnée avec la Rev.2 sans table de correspondance officielle.

### 4.4 Géographie (COG 2026)

* `COM` = code commune, `DEP` = département, `REG` = région. **Toujours des chaînes** : `2A001`, `01004`, région `04`.
* Les établissements à Paris, Lyon, Marseille portent un code d'**arrondissement** (`TYPECOM = ARM`), présent dans le COG.
* `geo_status` rend l'appariement visible : `MATCHED`, `UNMATCHED` (code absent du COG 2026),
  `NO_COMMUNE` (aucun code, p. ex. à l'étranger), `NOT_ENRICHED` (couche construite avec `--without-cog`).
* Aucun département n'est déduit du préfixe d'un code non apparié : ce serait inventer une information.

### 4.5 Effectifs

`NN` signifie **non employeur** (information renseignée) ; une valeur absente signifie **non renseigné**
(`UNKNOWN`). La tranche se rapporte à une **année de référence** (`anneeEffectifs…`) qui peut être ancienne : ce n'est
pas l'effectif à la date du snapshot.

### 4.6 Sentinelles

Une valeur absente dans une clé de mart est remplacée par `UNKNOWN` (codes) / `U` (état) / `Non renseigné` (libellés),
**uniquement dans les marts** ; la couche courante conserve les `null`. Les sentinelles sont documentées dans
[`data_dictionary.md`](data_dictionary.md) et prises en compte dans les largeurs SQL.

### 4.7 Aucune ligne perdue en silence

La couche courante contient **autant de lignes que la source**. Les valeurs invalides (date illisible, commune absente
du COG…) sont **conservées et signalées** (`date_creation_invalid`, `geo_status`) puis comptées par le quality gate.
Les dates de création lisibles mais antérieures au 01/01/1900 ou postérieures au snapshot sont également signalées comme anomalies de plausibilité sans suppression de ligne. La
somme de chaque mart est réconciliée avec la couche courante.

## 5. Ce que le projet ne mesure pas

* La **mortalité** ou la **survie** des entreprises.
* La **démographie officielle** d'entreprises (créations, défaillances).
* Le **nombre d'établissements actifs à chaque fin d'année** (voir §6).
* Un effectif salarié à une date donnée.

## 6. Limite principale : flux, pas stock

La question 1 est traitée par des **flux** : on connaît les établissements dont la date de création enregistrée tombe
dans l'année et les fermetures de l'année, mais pas le stock d'établissements actifs au 31 décembre de chaque année. Ce
stock est reconstructible à partir des périodes (`dateDebut`/`dateFin` + état), c'est la **première évolution
prévue** ([`roadmap.md`](roadmap.md)). D'ici là, un graphique de flux ne doit pas être présenté comme une courbe de
stock.

## 7. Validation technique réalisée

Le pipeline a été exécuté sur les **stocks complets** du snapshot de référence, puis chargé sur un **SQL Server réel**. La validation finale comprend :

- 353 tests automatisés passants ;
- Quality Gate complet : **PASS, 0 FAIL, 4 WARN** ;
- 9 088 475 lignes chargées dans le staging SQL ;
- contrôles SQL : **13 PASS, 0 WARN, 0 FAIL** ;
- rapport Power BI final construit sur le modèle réel.

L’inventaire détaillé est dans [`FINAL_VALIDATION.md`](FINAL_VALIDATION.md) et la procédure reproductible dans [`execution_checklist.md`](execution_checklist.md).

### Cas NAF `00.00Z`

Le code `00.00Z` est conservé comme activité non classée : il respecte la forme d’un code NAF Rev.2 mais ne correspond à aucune section économique. Il ne déclenche donc pas le contrôle `rev2_section_unmapped`.
