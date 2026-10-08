# Power Query (M) — connexion au modèle SQL Server

À coller dans **Accueil → Transformer les données → Éditeur avancé**, une requête par table. On importe les tables du
**modèle en étoile** (dimensions + faits) plutôt que les vues aplaties : plus léger et plus flexible.

\> ✅ **Statut** : ces requêtes ont été utilisées dans Power BI Desktop pour construire le rapport final sur le modèle SQL Server réel. Prérequis : `sql_runner --stage finalize` terminé avec `0 FAIL`.

## 1. Paramètres (Accueil → Gérer les paramètres)

| Nom | Type | Valeur |
|---|---|---|
| `ServerName` | Texte | `localhost` (ou `localhost\SQLEXPRESS`) |
| `DatabaseName` | Texte | `FranceBusinessDynamics` |

## 2. Requêtes

Même modèle pour les huit tables ; seul `Item` change.

```m
// DimDate
let
    Source = Sql.Database(ServerName, DatabaseName),
    Table  = Source{[Schema = "analytics", Item = "DimDate"]}[Data]
in
    Table
```

Valeurs d'`Item` : `DimDate`, `DimGeography`, `DimActivity`, `DimWorkforce`, `DimLegalCategory`,
`FactEstablishmentSnapshot`, `FactLegalUnitSnapshot`, `FactAdministrativeEvents`.

## 3. Types à vérifier après import

| Colonne | Type Power BI attendu |
|---|---|
| `DimActivity[IsNafRev2]` | Vrai/Faux |
| `DimActivity[NafSection]`, `*Code` (tous) | **Texte** (jamais numérique : `2A001`, `01004`, région `04`) |
| `Fact*[…Count]` | Nombre entier |
| `FactAdministrativeEvents[EventYear]` | Nombre entier (ne **pas** en faire une hiérarchie de dates) |

## 4. Volume

`FactEstablishmentSnapshot` est la plus grande table (une ligne par commune × activité × effectifs × état). Le run réel contient **8 990 763 lignes** dans `FactEstablishmentSnapshot`. Le rapport final utilise le mode **Importation** ; Power BI compresse ce modèle suffisamment pour le livrable portfolio actuel.

## 5. Planification

Le modèle SQL est reconstruit par `06_load_mart.sql` à chaque stock SIRENE : actualiser le rapport **après** chaque
rechargement SQL réussi.
