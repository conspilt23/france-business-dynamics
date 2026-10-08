# Mesures DAX finales

Ce fichier documente les mesures réellement utilisées dans le rapport Power BI final ainsi que la dimension auxiliaire de cartographie.

Créer les mesures dans une table dédiée `_Measures`. Les noms de tables/colonnes ci-dessous correspondent aux tables `analytics.*` importées depuis SQL Server.

## Établissements

```DAX
Établissements =
SUM ( FactEstablishmentSnapshot[EstablishmentCount] )
```

```DAX
Établissements actifs =
CALCULATE ( [Établissements], FactEstablishmentSnapshot[StatusCode] = "A" )
```

```DAX
Établissements fermés =
CALCULATE ( [Établissements], FactEstablishmentSnapshot[StatusCode] = "F" )
```

```DAX
Taux établissements actifs =
DIVIDE ( [Établissements actifs], [Établissements] )
```

## Unités légales

```DAX
Unités légales =
SUM ( FactLegalUnitSnapshot[LegalUnitCount] )
```

```DAX
Unités légales actives =
CALCULATE ( [Unités légales], FactLegalUnitSnapshot[StatusCode] = "A" )
```

```DAX
Unités légales cessées =
CALCULATE ( [Unités légales], FactLegalUnitSnapshot[StatusCode] = "C" )
```

```DAX
Taux unités légales actives =
DIVIDE ( [Unités légales actives], [Unités légales] )
```

## Événements administratifs

```DAX
_Events Base =
SUM ( FactAdministrativeEvents[EventCount] )
```

Masquer `_Events Base` dans la vue Rapport : les trois séries d’événements ne doivent jamais être additionnées.

```DAX
Cessations administratives =
CALCULATE (
    [_Events Base],
    FactAdministrativeEvents[EventType] = "Cessation administrative"
)
```

```DAX
Fermetures administratives =
CALCULATE (
    [_Events Base],
    FactAdministrativeEvents[EventType] = "Fermeture administrative"
)
```

```DAX
Créations enregistrées =
CALCULATE (
    [_Events Base],
    FactAdministrativeEvents[EventType] = "Date de création enregistrée"
)
```

## KPI 2025

```DAX
Créations 2025 =
CALCULATE (
    [Créations enregistrées],
    FactAdministrativeEvents[EventYear] = 2025
)
```

```DAX
Créations 2024 =
CALCULATE (
    [Créations enregistrées],
    FactAdministrativeEvents[EventYear] = 2024
)
```

```DAX
Évolution créations 2025 =
DIVIDE (
    [Créations 2025] - [Créations 2024],
    [Créations 2024]
)
```

```DAX
Fermetures 2025 =
CALCULATE (
    [Fermetures administratives],
    FactAdministrativeEvents[EventYear] = 2025
)
```

```DAX
Cessations 2025 =
CALCULATE (
    [Cessations administratives],
    FactAdministrativeEvents[EventYear] = 2025
)
```

Formater `Évolution créations 2025` en pourcentage avec une décimale.

## KPI géographiques

```DAX
Régions couvertes =
DISTINCTCOUNT ( DimGeography[RegionCode] )
```

```DAX
Départements couverts =
DISTINCTCOUNT ( DimGeography[DepartmentCode] )
```

```DAX
Communes référencées =
DISTINCTCOUNT ( DimGeography[CommuneCode] )
```

## Qualité et périmètre

```DAX
Établissements NAF Rev2 =
CALCULATE ( [Établissements], DimActivity[IsNafRev2] = TRUE () )
```

```DAX
Couverture NAF Rev2 =
DIVIDE ( [Établissements NAF Rev2], [Établissements] )
```

```DAX
Part géographie non appariée =
DIVIDE (
    CALCULATE ( [Établissements], DimGeography[GeoStatus] = "UNMATCHED" ),
    [Établissements]
)
```

```DAX
Date du snapshot =
MAX ( DimDate[FullDate] )
```

## Dimension auxiliaire `DimRegionMap`

Cette table est créée dans Power BI afin de donner à Azure Maps des coordonnées explicites pour les 18 régions valides, y compris les territoires ultramarins.

```DAX
DimRegionMap =
DATATABLE (
    "RegionCode", STRING,
    "RegionName", STRING,
    "Latitude", DOUBLE,
    "Longitude", DOUBLE,
    {
        { "84", "Auvergne-Rhône-Alpes", 45.7640, 4.8357 },
        { "27", "Bourgogne-Franche-Comté", 47.3220, 5.0415 },
        { "53", "Bretagne", 48.1173, -1.6778 },
        { "24", "Centre-Val de Loire", 47.9030, 1.9093 },
        { "94", "Corse", 41.9192, 8.7386 },
        { "44", "Grand Est", 48.5734, 7.7521 },
        { "32", "Hauts-de-France", 50.6292, 3.0573 },
        { "11", "Île-de-France", 48.8566, 2.3522 },
        { "28", "Normandie", 49.4431, 1.0993 },
        { "75", "Nouvelle-Aquitaine", 44.8378, -0.5792 },
        { "76", "Occitanie", 43.6047, 1.4442 },
        { "52", "Pays de la Loire", 47.2184, -1.5536 },
        { "93", "Provence-Alpes-Côte d'Azur", 43.2965, 5.3698 },
        { "01", "Guadeloupe", 15.9985, -61.7255 },
        { "02", "Martinique", 14.6161, -61.0588 },
        { "03", "Guyane", 4.9224, -52.3135 },
        { "04", "La Réunion", -20.8789, 55.4481 },
        { "06", "Mayotte", -12.7806, 45.2278 }
    }
)
```

Configurer :

- `Latitude` → catégorie de données **Latitude** ;
- `Longitude` → catégorie de données **Longitude** ;
- `RegionName` → catégorie **State or Province** ;
- relation `DimRegionMap[RegionCode]` **1 → *** `DimGeography[RegionCode]`, active, filtrage simple.

Colonne utilisée pour masquer le membre relationnel `(Blank)` dans le slicer Région :

```DAX
IsValidRegion =
IF ( NOT ISBLANK ( DimRegionMap[RegionName] ), 1, 0 )
```

Appliquer `IsValidRegion = 1` uniquement dans **Filters on this visual** du slicer Région. Garder `Single select` désactivé afin de permettre `Clear selections`.

## Valeurs de contrôle du rapport final

| KPI | Valeur affichée |
|---|---:|
| Établissements actifs | 16,83 M |
| Unités légales actives | 16,74 M |
| Taux établissements actifs | 38,0 % |
| Taux unités légales actives | 55,5 % |
| Créations 2025 | 2,07 M |
| Fermetures 2025 | 1,49 M |
| Cessations 2025 | 1,00 M |
| Évolution créations 2025 vs 2024 | +7,0 % |
| Régions couvertes | 18 |
| Départements couverts | 101 |
| Communes référencées | 36 916 |

## Réconciliation avec SQL

| Mesure DAX | Requête SQL de contrôle |
|---|---|
| `Établissements` | `SELECT SUM(EstablishmentCount) FROM analytics.FactEstablishmentSnapshot` |
| `Unités légales` | `SELECT SUM(LegalUnitCount) FROM analytics.FactLegalUnitSnapshot` |
| `Fermetures administratives` | `SELECT SUM(EventCount) FROM analytics.vw_AdministrativeEvents WHERE EventType = N'Fermeture administrative'` |

## Notes méthodologiques

- Ne pas renommer fermetures/cessations en « mortalité » ou « défaillances ».
- Ne jamais additionner créations, fermetures et cessations.
- Les graphiques annuels représentent des **flux**, pas un stock historique actif reconstruit.
- `UNKNOWN` et les géographies non résolues sont conservés afin de rendre visible l’absence d’information.
