# Checklist d’exécution

Procédure de reproduction du projet final. Les valeurs indiquées dans la colonne « run de référence » correspondent à l’exécution réelle du snapshot du **1er octobre 2026**.

## A. Installation et tests

| Étape | Commande | Run de référence |
|---|---|---|
| Créer l’environnement | `python -m venv .venv` | ✅ |
| Installer | `python -m pip install -r requirements.txt` | ✅ |
| Tester | `python -m pytest` | ✅ **353 passed**, 1 warning |
| Valider la structure | `python -m france_business_dynamics.validation.structure` | ✅ |

Les tests n’effectuent aucun téléchargement SIRENE et n’ouvrent aucune connexion SQL réelle.

## B. Sources officielles

```powershell
python -m france_business_dynamics.ingestion.check_sources
python -m france_business_dynamics.ingestion.download_sources --all --dry-run
python -m france_business_dynamics.ingestion.download_sources --all
python -m france_business_dynamics.ingestion.inspect_schema --local
python -m france_business_dynamics.ingestion.inspect_schema --compare docs/schema_snapshot_2026-10-01.json
```

Run de référence : **5/5 sources conformes** et quatre Parquet SIRENE correspondant aux volumes documentés dans le README.

## C. Transformation

```powershell
python -m france_business_dynamics.transform
python -m france_business_dynamics.build_mart
python -m france_business_dynamics.quality_gate --strict
```

Résultat de référence :

- `etablissement_current.parquet` : 44 282 364 lignes ;
- `unite_legale_current.parquet` : 30 148 401 lignes ;
- `fact_establishment_snapshot.parquet` : 8 990 763 lignes ;
- `fact_legal_unit_snapshot.parquet` : 83 339 lignes ;
- `fact_administrative_events.parquet` : 14 208 lignes ;
- Quality Gate : **PASS, 0 FAIL, 4 WARN**.

Les WARN correspondent à des dates atypiques documentées dans [`data_quality.md`](data_quality.md) et [`FINAL_VALIDATION.md`](FINAL_VALIDATION.md).

## D. SQL Server

Créer `.env` depuis `.env.example`, puis :

```powershell
python -m france_business_dynamics.load_to_db --dry-run
python -m france_business_dynamics.sql_runner --stage all --yes
```

Run réel de référence :

- **9 088 475** lignes chargées dans 8 tables `stg.*` ;
- modèle `analytics.*` reconstruit ;
- contrôles finaux : **13 PASS, 0 WARN, 0 FAIL**.

## E. Notebooks

```powershell
jupyter lab notebooks/
```

Ordre recommandé :

1. `01_source_profiling.ipynb` ;
2. `02_business_analysis.ipynb` ;
3. `03_quality_control.ipynb`.

La logique de production reste dans `src/` ; les notebooks servent à présenter et analyser les résultats.

## F. Power BI

Le rapport final est versionné sous :

```text
dashboard/france-business-dynamics.pbix
```

Pour le reconstruire :

1. suivre `dashboard/power_query.md` pour charger les 8 tables `analytics.*` ;
2. créer les relations indiquées dans `dashboard/README.md` ;
3. ajouter les mesures et `DimRegionMap` depuis `dashboard/DAX_MEASURES.md` ;
4. importer `dashboard/fbd_theme.json` ;
5. vérifier les KPI de contrôle ;
6. exporter les quatre pages vers `dashboard/screenshots/`.

## G. Avant publication GitHub

Vérifier :

- aucun `.env`, Parquet ou fichier source SIRENE versionné ;
- `dashboard/france-business-dynamics.pbix` présent ;
- les 4 captures finales présentes ;
- aucun panneau d’édition visible sur les captures ;
- `python -m pytest` et `python -m france_business_dynamics.validation.structure` passent ;
- `git status` ne contient que les fichiers intentionnellement publiés.
