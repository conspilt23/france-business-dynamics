# Fichiers JSON générés — rôle de chacun

Tous les JSON sont écrits **de façon atomique** (fichier temporaire puis remplacement) : jamais de fichier à moitié écrit.

| Fichier | Produit par | Versionné ? | Rôle |
|---|---|:---:|---|
| `docs/schema_snapshot_<date>.json` | `ingestion.inspect_schema --json-output` | oui | Photographie du schéma des Parquet SIRENE à une date : lignes, colonnes, types. Sert à détecter une **dérive de schéma** (`--compare`). |
| `docs/source_snapshot_<date>.json` | (référence manuelle) | oui | Statuts HTTP et redirections observés pour chaque source à une date. |
| `data/raw/<fichier>.json` | `ingestion.download_sources` | non | **Manifeste de téléchargement** : preuve d'intégrité du fichier brut. |
| `data/raw/<fichier>.part.json` | `ingestion.download_sources` | non | Identité d'un téléchargement **en cours** (permet de reprendre en sécurité). Supprimé à la fin. |
| `data/processed/pipeline_manifest.json` | `transform`, `build_mart` | non | Traçabilité de chaque couche : lignes, **échantillon ou complet**, entrées, date. |
| `data/processed/quality_report.json` | `quality_gate` | non (exemple dans `docs/examples/`) | Résultat des contrôles : statut par couche, détail de chaque contrôle. |

## `schema_snapshot_<date>.json`

```text
snapshot_generated_at_utc, tool_version
datasets.<clé>: stable_url, resolved_url, rows, row_groups, column_count,
                columns: [{name, type}], created_by
```

Le snapshot de 2026-10-01 versionné dans le dépôt est au format initial (`columns` = nombre, `column_names` = noms,
sans types) ; `--compare` sait le lire (voir `ingestion/sources.py::diff_snapshots`).

## Manifeste de téléchargement (`data/raw/<fichier>.json`)

| Champ | Contenu |
|---|---|
| `stable_url` / `resolved_url` | lien stable et URL finale après redirection |
| `bytes` / `expected_bytes` | taille écrite / taille annoncée par le serveur (doivent être égales) |
| `sha256` | empreinte SHA-256 du fichier **complet** |
| `downloaded_at_utc`, `duration_seconds` | horodatage et durée |
| `attempts`, `resumed`, `resumed_from_bytes` | nombre de tentatives, reprise éventuelle |
| `accept_ranges`, `etag`, `last_modified` | capacités et identité de la version distante |
| `verification` | contrôle de format (Parquet : lignes, groupes de lignes, colonnes ; CSV : en-tête) |

## `pipeline_manifest.json`

Par couche (`etablissement_current`, `fact_establishment_snapshot`, …) : `path`, `rows`, `limit`, `scope`
(`ECHANTILLON` ou `COMPLET`), `built_at_utc`, `inputs` (chemin, lignes, SHA-256 du téléchargement).
Le quality gate l'utilise pour **ne jamais présenter un échantillon de 10 000 lignes comme une validation complète**.

## `quality_report.json`

```text
status (PASS|FAIL), mode (partial|strict), complete, contains_samples, generated_at_utc, tool_version
summary: checks, passed, failed, warnings, layers_checked, layers_skipped, sample_layers
layers.<couche>: status (PASS|FAIL|SKIPPED), rows, scope, warnings,
                 checks: [{id, description, status, severity, observed, expected}]
                 (couche absente : reason, produce_with)
```

Les tests automatisés construisent leurs rapports à partir de fixtures isolées ; aucun rapport synthétique n’est présenté comme un résultat du projet.
