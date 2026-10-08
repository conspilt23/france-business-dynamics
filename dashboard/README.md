# Tableau de bord Power BI

Ce dossier contient le **rapport Power BI final** ainsi que les éléments nécessaires pour comprendre et reconstruire son modèle sémantique à partir du modèle SQL Server validé.

> ✅ **Statut final.** Le rapport a été construit dans Power BI Desktop sur les données SIRENE réelles du snapshot du **1er octobre 2026**. Il contient **4 pages analytiques**.

## Contenu du dossier

| Fichier / dossier | Rôle |
|---|---|
| `france-business-dynamics.pbix` | rapport Power BI final, versionné dans le dépôt (≈ 15,5 Mo) |
| [`power_query.md`](power_query.md) | requêtes M pour charger les 8 tables `analytics.*` |
| [`DAX_MEASURES.md`](DAX_MEASURES.md) | mesures DAX finales, table `DimRegionMap` et contrôles de réconciliation |
| [`fbd_theme.json`](fbd_theme.json) | thème Power BI |
| `screenshots/` | captures directes des 4 pages finales |

Le `.pbix` est un binaire non diffable, mais sa taille actuelle reste suffisamment faible pour être versionnée avec le reste du portfolio. Les données SIRENE brutes et les fichiers Parquet restent exclus de Git.

## Reproduire le rapport

1. Exécuter le pipeline et SQL Server jusqu’aux contrôles finaux :

   ```powershell
   python -m france_business_dynamics.sql_runner --stage all --yes
   ```

   Le run de référence termine à **13 PASS, 0 WARN, 0 FAIL**.

2. Dans Power BI Desktop, charger les 8 tables `analytics.*` en mode **Importation** selon [`power_query.md`](power_query.md).

3. Créer les relations principales en filtrage **dimension → fait** :

   | Table de faits | Colonne | → Dimension | Colonne |
   |---|---|---|---|
   | `FactEstablishmentSnapshot` | `DateKey` | `DimDate` | `DateKey` |
   | `FactEstablishmentSnapshot` | `CommuneCode` | `DimGeography` | `CommuneCode` |
   | `FactEstablishmentSnapshot` | `ActivityKey` | `DimActivity` | `ActivityKey` |
   | `FactEstablishmentSnapshot` | `WorkforceCode` | `DimWorkforce` | `WorkforceCode` |
   | `FactLegalUnitSnapshot` | `DateKey` | `DimDate` | `DateKey` |
   | `FactLegalUnitSnapshot` | `ActivityKey` | `DimActivity` | `ActivityKey` |
   | `FactLegalUnitSnapshot` | `LegalCategoryCode` | `DimLegalCategory` | `LegalCategoryCode` |
   | `FactAdministrativeEvents` | `ActivityKey` | `DimActivity` | `ActivityKey` |

4. Trier `DimWorkforce[WorkforceLabel]` par `DimWorkforce[SortOrder]`.

5. Créer la table Power BI `DimRegionMap` documentée dans [`DAX_MEASURES.md`](DAX_MEASURES.md), puis relier :

   ```text
   DimRegionMap[RegionCode]  1  →  *  DimGeography[RegionCode]
   ```

6. Ajouter les mesures finales dans la table `_Measures`.

7. Importer [`fbd_theme.json`](fbd_theme.json).

8. Vérifier les KPI principaux avec les totaux SQL avant publication.

## Pages finales

### 1 — Vue d’ensemble

Questions couvertes : volume actif, secteurs dominants, régions dominantes, tranches d’effectifs et tendance générale des flux administratifs.

Principaux visuels :

- établissements actifs : **16,83 M** ;
- unités légales actives : **16,74 M** ;
- taux d’établissements actifs : **38,0 %** ;
- taux d’unités légales actives : **55,5 %** ;
- Top 10 secteurs et régions ;
- structure par tranche d’effectifs ;
- flux administratifs 2021–2025.

### 2 — Géographie

Questions couvertes : répartition territoriale, régions/départements les plus concentrés et exploration interactive.

Principaux visuels :

- **18** régions, **101** départements, **36 916** communes référencées ;
- Azure Maps utilisant les coordonnées de `DimRegionMap` ;
- slicer Région ;
- slicer Département ;
- Top 10 départements par établissements actifs.

La dimension auxiliaire évite les ambiguïtés de géocodification, notamment pour les régions ultramarines. Le membre `(Blank)` généré par les relations n’est pas présenté comme une région valide.

### 3 — Dynamique 2021–2025

Principaux KPI 2025 :

- créations enregistrées : **2,07 M** ;
- fermetures administratives : **1,49 M** ;
- cessations administratives : **1,00 M** ;
- évolution des créations vs 2024 : **+7,0 %**.

La page compare les trois séries sur 2021–2025 et présente les Top 10 secteurs par créations et fermetures en 2025.

> Les créations enregistrées, fermetures d’établissements et cessations d’unités légales sont des **flux administratifs distincts** et ne doivent pas être additionnés.

### 4 — Structure des entreprises

La page présente :

- unités légales actives par catégorie juridique ;
- catégories d’entreprise `PME`, `ETI`, `GE` et `UNKNOWN` ;
- Top 10 secteurs par unités légales actives ;
- établissements actifs par tranche d’effectifs.

`UNKNOWN` est volontairement conservé afin de ne pas masquer l’absence d’information.

## Captures d’écran

Les captures versionnées sont des **exports/captures directes du rapport Power BI final**, pas les visuels de storytelling.

```text
screenshots/
├── 01-vue-ensemble.png
├── 02-geographie.png
├── 03-dynamique-2021-2025.png
└── 04-structure-entreprises.png
```

Pour une publication externe, utiliser ces captures comme preuve du livrable réel ; le storytelling LinkedIn/PDF peut les réutiliser dans une mise en page distincte.
