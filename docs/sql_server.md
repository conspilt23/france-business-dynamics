# SQL Server — guide d'exploitation

Guide **opérationnel** pour charger le modèle analytique. La structure du modèle est dans
[`architecture.md`](architecture.md) ; les colonnes et largeurs dans [`data_dictionary.md`](data_dictionary.md).

> ✅ **Statut de validation.** Les scripts T-SQL ont été testés par la suite automatisée **et exécutés sur un SQL Server réel** avec les marts complets. Le run de référence a chargé **9 088 475 lignes** dans le staging et terminé à **13 PASS, 0 WARN, 0 FAIL**.

## Prérequis

| Élément | Détail |
|---|---|
| SQL Server | **2016 SP1 ou plus récent** (`DROP TABLE IF EXISTS`, `CREATE OR ALTER`) ; l'édition Express convient |
| Pilote ODBC | **Microsoft ODBC Driver 18 for SQL Server** (ou 17 : renseigner `SQL_DRIVER`) |
| Configuration | copier `.env.example` en `.env` (jamais committé) |

Exemple `.env` (authentification Windows) :

```ini
SQL_SERVER=localhost            # ou localhost\SQLEXPRESS
SQL_DATABASE=FranceBusinessDynamics
SQL_DRIVER=ODBC Driver 18 for SQL Server
SQL_TRUSTED_CONNECTION=yes
SQL_ENCRYPT=no
SQL_TRUST_SERVER_CERTIFICATE=yes
```

Authentification SQL : `SQL_TRUSTED_CONNECTION=no` + `SQL_USER` + `SQL_PASSWORD` (le mot de passe n'est jamais affiché).

## Ordre d'exécution (non négociable)

```text
1. sql/01_create_database.sql   (sur master)      crée la base — idempotent
2. sql/02_create_schema.sql                        schémas stg et analytics — idempotent
3. sql/03_create_dimensions.sql                    ⚠ RECONSTRUIT analytics.*, dimensions vides
4. sql/04_create_facts.sql                         ⚠ RECONSTRUIT stg.* et les faits
5. sql/05_analytical_views.sql                     vues — idempotent
6. python -m france_business_dynamics.load_to_db   remplit stg.* (Python, par lots)
7. sql/06_load_mart.sql                            remplit le modèle depuis stg.*
8. sql/07_quality_checks.sql                       contrôles PASS / WARN / FAIL
```

Raccourcis : `python -m france_business_dynamics.sql_runner --stage create --yes` (1→5), `--stage finalize` (7→8),
`--stage all --yes` (tout, avec les contrôles hors ligne **d'abord**), `--list` (rappel de l'ordre).
Dans SSMS, exécuter les fichiers à la main reste possible : ils sont découpés en lots par `GO`.

## Pourquoi « reconstruction » et pas « créer si absent »

L'ancien schéma créait les tables seulement si elles n'existaient pas, puis tentait des `ALTER COLUMN` : une table
créée par une version précédente (colonnes trop étroites) **restait étroite**, d'où l'erreur
`String data, right truncation: length 14 buffer 6`. Désormais :

* 03 et 04 font `DROP TABLE IF EXISTS` puis `CREATE TABLE` : rejouables, résultat identique ;
* les données sont **régénérables** (depuis les Parquet) : rien d'irremplaçable n'est détruit ;
* `load_to_db` compare le schéma *vivant* au contrat avant d'insérer et **nomme la colonne** trop étroite ;
* 06 est rejouable sans reconstruire (il vide `analytics.*` puis recharge, en une seule transaction).

## Contrôles avant chargement (`load_to_db`)

1. Fichiers présents. 2. Chaque colonne : présence, type, **largeur maximale observée ≤ largeur SQL**, NOT NULL, ASCII des
`varchar`. 3. Connexion. 4. Schéma vivant (`INFORMATION_SCHEMA.COLUMNS`) conforme au contrat. 5. Insertion par lots de
50 000 lignes, **une transaction par table** (échec → `ROLLBACK`, contenu précédent intact), avec le nom de la table et la
plage de lignes en cause. 6. Comptage en base = lignes du Parquet.

Options : `--dry-run` (hors ligne, sans connexion), `--check-only` (schéma réel, sans insérer), `--table NOM` (répétable),
`--batch-size N`.

## Dépannage

| Message | Cause probable | Action |
|---|---|---|
| `Pilote ODBC « … » non installé. Pilotes détectés : […]` | pilote absent ou autre version | installer le pilote 18 ou fixer `SQL_DRIVER` |
| `Connexion à SQL Server impossible` | service arrêté, nom d'instance erroné, base absente | vérifier `SQL_SERVER` (`localhost\SQLEXPRESS`) ; exécuter 01 |
| `incompatibilité(s) entre les données et le contrat SQL` | valeur plus large que la colonne, null, non-ASCII | corriger le mart ; **aucune insertion n'a eu lieu** |
| `Le schéma SQL existant ne correspond pas au contrat` | tables d'une ancienne version | `sql_runner --stage create --yes` |
| `Staging vide` (THROW 50001) | 06 lancé avant le chargement | lancer `load_to_db` puis 06 |
| `Journal de transactions plein` | très gros chargement en mode FULL | sauvegarder le journal, ou charger par `--table` |
| `FAIL` dans la sortie de 07 | réconciliation, clé orpheline, section NAF… | lire `Observed` / `Expected` ; relancer quality gate Python |

## Exemples de requêtes

```sql
-- Établissements actifs par région (la ligne « Non renseigné » assure la réconciliation avec le total)
SELECT RegionName, SUM(EstablishmentCount) AS Etablissements
FROM analytics.vw_EstablishmentSnapshot
WHERE StatusCode = 'A'
GROUP BY RegionName
ORDER BY Etablissements DESC;

-- Fermetures administratives par année (toujours filtrer sur EventType : les séries ne s'additionnent pas)
SELECT EventYear, SUM(EventCount) AS Fermetures
FROM analytics.vw_AdministrativeEvents
WHERE EventType = N'Fermeture administrative'
GROUP BY EventYear ORDER BY EventYear;
```

## Validation réelle et portée de la CI

La CI continue d’utiliser des fixtures et une émulation contrôlée afin de rester rapide et indépendante d’un service externe. Cette stratégie vérifie la logique des scripts à chaque commit.

En complément, le run final a été exécuté sur un **SQL Server réel** avec ODBC Driver 18 :

- schéma vivant conforme au contrat ;
- chargement par lots réussi ;
- **9 088 475** lignes dans les huit tables de staging ;
- reconstruction du modèle `analytics.*` réussie ;
- **13 PASS, 0 WARN, 0 FAIL** dans `07_quality_checks.sql`.

Les performances exactes peuvent varier selon la machine, l’édition SQL Server et le sous-système disque.