# Validation finale — version 0.3.2

Ce document résume la validation réellement effectuée sur le projet final, depuis les tests automatisés jusqu’au dashboard Power BI.

## Verdict

| Domaine | État | Résultat vérifié |
|---|:---:|---|
| Structure du dépôt | ✅ | structure et documentation validées |
| Suite de tests | ✅ | **353 tests passés**, 1 warning d’environnement Jupyter/Windows |
| Sources officielles | ✅ | **5/5 sources conformes** |
| Téléchargement complet | ✅ | 4 stocks SIRENE + COG 2026 téléchargés et vérifiés |
| Inspection du schéma | ✅ | 30 148 401 / 44 282 364 / 72 004 236 / 96 657 561 lignes |
| ETL sur stocks complets | ✅ | couches courantes + 3 marts construits |
| Quality Gate | ✅ | **55/59 PASS, 0 FAIL, 4 WARN** |
| Contrat SQL hors ligne | ✅ | 8 tables de staging conformes |
| SQL Server réel | ✅ | **9 088 475 lignes** chargées en staging |
| Contrôles SQL | ✅ | **13 PASS, 0 WARN, 0 FAIL** |
| Power BI | ✅ | rapport final de **4 pages** construit et réconcilié |

## Volumes sources

| Source | Lignes |
|---|---:|
| Stock unités légales | 30 148 401 |
| Stock établissements | 44 282 364 |
| Historique unités légales | 72 004 236 |
| Historique établissements | 96 657 561 |
| **Total SIRENE contrôlé** | **243 092 562** |

## Sorties analytiques

| Couche | Lignes |
|---|---:|
| `fact_establishment_snapshot.parquet` | 8 990 763 |
| `fact_legal_unit_snapshot.parquet` | 83 339 |
| `fact_administrative_events.parquet` | 14 208 |

Le chargement staging ajoute les référentiels région, département, NAF, catégorie juridique et effectifs pour un total de **9 088 475 lignes chargées**.

## Quality Gate réel

Résultat :

```text
Quality gate : PASS (55/59 contrôles réussis, 0 échec, 4 avertissements)
```

Avertissements conservés pour traçabilité :

- 93 dates de création d’établissements antérieures au 01/01/1900 ;
- 10 143 dates de création d’établissements postérieures au snapshot ;
- 50 dates de création d’unités légales antérieures au 01/01/1900 ;
- 7 669 dates de création d’unités légales postérieures au snapshot.

Aucune de ces valeurs n’a été corrigée ou supprimée silencieusement.

## Validation SQL réelle

Le run complet a exécuté :

```text
01_create_database.sql
→ 02_create_schema.sql
→ 03_create_dimensions.sql
→ 04_create_facts.sql
→ 05_analytical_views.sql
→ load_to_db
→ 06_load_mart.sql
→ 07_quality_checks.sql
```

Résultat final :

```text
13 PASS, 0 WARN, 0 FAIL
```

Les réconciliations ont retrouvé exactement :

- **44 282 364** établissements ;
- **30 148 401** unités légales ;
- **20 345 855** événements administratifs agrégés.

Aucune clé dimensionnelle orpheline n’a été détectée.

## Validation Power BI

Le fichier final `dashboard/france-business-dynamics.pbix` contient :

1. `Vue d’ensemble` ;
2. `Géographie` ;
3. `Dynamique 2021–2025` ;
4. `Structure des entreprises`.

Les KPI de contrôle visibles dans le rapport final incluent :

- 16,83 M établissements actifs ;
- 16,74 M unités légales actives ;
- 38,0 % de taux d’établissements actifs ;
- 55,5 % de taux d’unités légales actives ;
- 2,07 M créations enregistrées en 2025 ;
- 1,49 M fermetures administratives en 2025 ;
- 1,00 M cessations administratives en 2025 ;
- +7,0 % d’évolution des créations 2025 vs 2024.

La cartographie utilise une dimension `DimRegionMap` explicite afin de gérer de manière déterministe les 18 régions, y compris les régions ultramarines.

## Conclusion

La version 0.3.2 est validée comme **projet portfolio end-to-end** : ingestion, transformation, qualité, modèle SQL Server, analyse et restitution Power BI ont tous été exécutés sur les données réelles du snapshot de référence.
