# Données

Ce dossier contient les données locales du pipeline. Elles sont volumineuses et reproductibles, donc **jamais versionnées dans Git**.

| Dossier | Contenu | Produit par |
|---|---|---|
| `raw/` | stocks SIRENE officiels (Parquet) + manifestes de téléchargement | `python -m france_business_dynamics.ingestion.download_sources` |
| `reference/` | référentiels géographiques, notamment le COG 2026 | `python -m france_business_dynamics.ingestion.download_sources --dataset cog_communes_2026` |
| `processed/` | couches préparées, marts, rapports qualité et manifestes de pipeline | `transform`, `build_mart`, `quality_gate` |

Le chemin peut être déplacé avec `FBD_DATA_DIR` (voir `.env.example`). Les fichiers `.part` correspondent à des téléchargements interrompus et peuvent être repris avec `--resume`.

Aucune donnée SIRENE réelle ne doit être committée.
