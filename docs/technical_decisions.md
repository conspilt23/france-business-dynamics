# Décisions techniques

Chaque décision est présentée avec **son motif** et **ce qu'on a écarté**. Pour la structure : [`architecture.md`](architecture.md).

## Fondations (conservées de la version initiale)

### Layout `src/` et paquet installable
`pip install -e .` évite les imports fragiles basés sur `sys.path`. **Aucun** `sys.path.append/insert` ni `pythonpath` pytest
n'est utilisé (vérifié par `validation/structure.py`). La version initiale s'appuyait encore sur
`sys.path.insert` dans `conftest.py` et `pythonpath = ["src"]` : supprimés.

### Parquet + Polars paresseux
Stocks de plusieurs Go : lecture colonne par colonne, pushdown de colonnes et de filtres (`scan_parquet`), écriture en
flux (`sink_parquet`). Jamais de `collect()` sur un stock complet.

### SQL Server comme couche analytique
Démontre dimensions, faits, contraintes d'intégrité et contrôles. Les 100+ millions de lignes brutes **ne sont pas** chargées
dans le modèle Power BI : brut → curated → faits agrégés → modèle sémantique.

## Décisions de la refonte

### Un seul contrat SQL (`sql_contract.py`)
*Problème* : l'erreur `right truncation: length 14 buffer 6` venait de colonnes `varchar` trop étroites ; corriger **une**
colonne laissait la classe d'erreur intacte.
*Décision* : un contrat unique (colonne, type, largeur, nullabilité) alimente (1) un pré-contrôle hors ligne, (2) la
vérification du schéma vivant, (3) un test d'alignement avec le DDL réel.
*Écarté* : élargir les colonnes à la main ; `ALTER COLUMN` dans le script (les tables existantes de versions précédentes
restent étroites si le script « crée si absent »).

### Reconstruction déterministe (03/04) plutôt que migrations
Les données sont régénérables ; un `DROP`/`CREATE` rejouable est plus sûr qu'une chaîne de migrations. Le comportement est
documenté en tête de fichier et demande `--yes` dans le `sql_runner`.

### Staging permissif, analytics strict
Le staging accepte large (aucune clé, `varchar` généreux) pour que le contrôle soit explicite et diagnosticable ; `analytics`
impose clés, FK et `CHECK`. `StatusCode` est volontairement `char(1)` + `CHECK` : échec bruyant plutôt que troncature.

### `DimActivity` indexée par (nomenclature, code)
Un même code peut exister dans deux nomenclatures sans signification commune ; l'ancienne clé `NafCode` seule fusionnait
des codes de nomenclatures différentes. Clé de substitution déterministe (`ROW_NUMBER` ordonné), pas d'`IDENTITY`.

### Section NAF seulement pour NAFRev2
L'ancienne implémentation mappait toute division à une section Rev.2, quelle que soit la nomenclature (audit : `72.2C` NAFRev1
« activités informatiques » recevait la section M). Voir `methodology.md` §4.3.

### Aucune ligne filtrée, valeurs invalides conservées et signalées
Pas de `filter` silencieux dans la couche courante ; indicateurs `date_creation_invalid`, `geo_status` ; réconciliation
mart ↔ couche courante.

### Téléchargeur maison plutôt que `requests.get`
Cas réel : coupure à 64 % (`IncompleteRead` / `ChunkedEncodingError`) → fichier inutilisable. Le téléchargeur écrit un
`.part`, reprend par `Range` (validé par ETag / `Last-Modified` / taille), vérifie taille + SHA-256 + format, publie par
renommage atomique, et **ne supprime jamais un partiel** sans instruction explicite.
*Écarté* : ajouter `tenacity`/`wget` (dépendance ou outil externe pour un besoin couvert en ~300 lignes testées).

### Chargement SQL : `pyodbc` direct plutôt que `pandas.to_sql` / SQLAlchemy
`to_sql` chargeait tout le DataFrame en mémoire et masquait la largeur des colonnes. Désormais : lecture Arrow par lots
(`iter_batches`), `executemany` en `fast_executemany`, une transaction par table. **pandas et SQLAlchemy ont donc été retirés
des dépendances** (moins de surface, aucune fonctionnalité perdue).

### Mémoire bornée : règles **mesurées**, pas supposées
Écrire `scan_parquet … sink_parquet` ne garantit pas le streaming : sur un stock synthétique de 12 M d'établissements
(schéma réel de 54 colonnes, 1 CPU, Polars 1.44), la première version consommait **3,4 Go** et la mémoire **croissait
linéairement** avec le volume (extrapolé : > 11 Go pour 44 M de lignes). La bisection a trouvé quatre causes, corrigées :

| Cause | Mesure (pic RSS) | Correctif |
|---|---|---|
| `replace_strict(dict)` pour les libellés d'effectifs | +900 Mo (non streamable) | chaîne `when/then` |
| `join` au COG sans ordre explicite | 1 827 Mo → 933 Mo | `maintain_order="left"` + COG matérialisé (35 000 lignes) |
| `is_in(liste de chaînes)` (quality gate, **et** reconstruction des événements sur les historiques) | 2 255 Mo → 438 Mo | `utils/exprs.py::equals_any / equals_none` |
| `collect()` : le moteur par défaut n'utilise pas le streaming | 1 198 Mo → 340 Mo | `collect(engine="streaming")` |
| unicité de `siret` sur toutes les valeurs à la fois | table de hachage de 44 M clés | passes par paquets de hachage (≤ 8 M clés) |

Résultat mesuré après correction (12 M établissements + 6 M unités légales + 16 M de périodes historiques, 1 CPU) :
`transform` 1,2 Go · `build_mart` complet (3 marts dont les événements) 1,2 Go · `quality_gate --strict` 0,7 Go ·
`load_to_db --dry-run` 0,1 Go ; et le pic **se stabilise** quand le volume double (1,04 → 1,08 → 1,25 Go pour 3 → 6 → 12 M
de lignes). Des tests statiques (`tests/test_streaming_safety.py`) interdisent de réintroduire ces constructions.
*Limite* : extrapolation, non mesure, vers 44 M / 96 M de lignes ; données synthétiques (distribution plausible, pas réelle).

### Quality gate par couche avec dépendances explicites
Une couche absente est `SKIPPED` (avec la commande qui la produit) ; `--strict` pour exiger les cinq. Le rapport distingue
**échantillon** et **complet**.

### Écritures atomiques et manifestes
Parquet (`.tmp` → `os.replace`), JSON et téléchargements : jamais de fichier final à moitié écrit. `os.replace` est
retenté sous Windows (antivirus, indexeur).

### Sortie standard tolérante
Sous Windows, une sortie redirigée (`> log.txt`) utilise cp1252 qui ne sait pas écrire `→` ou `≤` : un
`UnicodeEncodeError` a été **reproduit** puis corrigé (`harden_streams`) et couvert par un test.

### Validation SQL : émulation en CI + exécution réelle
La suite automatisée exécute la logique des `.sql` sur fixtures via une émulation contrôlée afin de garder une CI indépendante d’un service externe. En complément, la version finale a été exécutée sur un **SQL Server réel** : chargement complet réussi et contrôles finaux à **13 PASS, 0 WARN, 0 FAIL** (`sql_server.md`).

### Dépendances : directes fixées + verrou complet
`requirements.txt` fixe les dépendances directes (identiques à `pyproject.toml`, vérifié par un test) ;
`requirements-lock.txt` fixe l'arbre complet (multiplateforme). Python `>=3.13,<3.15` : testé en 3.13 et 3.14.

### Ce qui a été volontairement écarté
`ruff`/`mypy` (non indispensables pour ce périmètre ; voir roadmap), orchestrateur, base de données dans la CI.
