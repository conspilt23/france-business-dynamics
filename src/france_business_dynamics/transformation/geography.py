"""Géographie : lecture du COG 2026 et rattachement commune → département → région.

Tous les codes (COM, DEP, REG, TYPECOM) sont lus et conservés comme CHAÎNES : `2A001`
(Corse-du-Sud) et `01004` (zéro initial) seraient détruits par une inférence entière.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.utils.errors import PreconditionError, SchemaError

COG_REQUIRED_COLUMNS = ["TYPECOM", "COM", "DEP", "REG", "LIBELLE"]
GEO_STATUS_MATCHED = "MATCHED"
GEO_STATUS_UNMATCHED = "UNMATCHED"  # code commune renseigné mais absent du COG 2026
GEO_STATUS_NO_COMMUNE = "NO_COMMUNE"  # aucun code commune (ex. établissement à l'étranger)
GEO_STATUS_NOT_ENRICHED = "NOT_ENRICHED"  # couche construite sans COG (--without-cog)

# Priorité en cas de code répété dans le COG : commune actuelle d'abord.
_TYPE_PRIORITY = {"COM": 0, "ARM": 1, "COMA": 2, "COMD": 3}


def read_cog_2026(path: Path) -> pl.LazyFrame:
    """Lit `v_commune_2026.csv` : une ligne par `code_commune`, codes en chaînes.

    * `infer_schema=False` → toutes les colonnes en `String` (aucune inférence numérique).
    * Les communes associées/déléguées (COMA/COMD) sans DEP/REG héritent de ceux de leur
      commune parente (`COMPARENT`) lorsque la colonne est présente.
    """
    if not path.exists():
        raise PreconditionError(
            f"Référence COG introuvable : {path}",
            hint="python -m france_business_dynamics.ingestion.download_sources --dataset cog_communes_2026 "
            "(ou utiliser --without-cog).",
        )

    raw = pl.scan_csv(path, separator=",", encoding="utf8", infer_schema=False, null_values=["", "NA"])
    available = raw.collect_schema().names()
    missing = [column for column in COG_REQUIRED_COLUMNS if column not in available]
    if missing:
        raise SchemaError(
            f"COG {path.name} : colonnes absentes {missing} (colonnes lues : {available[:12]}).",
            hint="Vérifier que le fichier est bien `v_commune_2026.csv` (séparateur « , », UTF-8).",
        )

    has_parent = "COMPARENT" in available
    columns = COG_REQUIRED_COLUMNS + (["COMPARENT"] if has_parent else [])
    cog = (
        raw.select(columns)
        .rename(
            {
                "TYPECOM": "type_commune",
                "COM": "code_commune",
                "DEP": "code_departement",
                "REG": "code_region",
                "LIBELLE": "commune",
                **({"COMPARENT": "_parent"} if has_parent else {}),
            }
        )
        .with_columns(pl.all().cast(pl.String).str.strip_chars())
        .filter(pl.col("code_commune").is_not_null())
    )

    if has_parent:
        parents = (
            cog.filter(pl.col("type_commune") == "COM")
            .select(
                pl.col("code_commune").alias("_parent"),
                pl.col("code_departement").alias("_parent_dep"),
                pl.col("code_region").alias("_parent_reg"),
            )
            .unique(subset=["_parent"], keep="first", maintain_order=True)
        )
        cog = (
            cog.join(parents, on="_parent", how="left")
            .with_columns(
                pl.coalesce("code_departement", "_parent_dep").alias("code_departement"),
                pl.coalesce("code_region", "_parent_reg").alias("code_region"),
            )
            .drop("_parent", "_parent_dep", "_parent_reg")
        )

    return (
        cog.with_columns(
            pl.col("type_commune")
            .replace_strict(_TYPE_PRIORITY, default=9, return_dtype=pl.Int8)
            .alias("_type_priority")
        )
        .sort(["code_commune", "_type_priority"])
        .unique(subset=["code_commune"], keep="first", maintain_order=True)
        .drop("_type_priority")
    )


def enrich_establishments_with_cog(establishments: pl.LazyFrame, cog: pl.LazyFrame) -> pl.LazyFrame:
    """Jointure gauche sur `codeCommuneEtablissement` + indicateur `geo_status` (jamais de ligne perdue).

    Deux choix mesurés sur 6 M d'établissements (docs/FINAL_VALIDATION.md, test d'échelle) :
    * le COG (~35 000 lignes, quelques Mo) est **matérialisé avant la jointure** ;
    * `maintain_order="left"` : avec l'ordre par défaut, Polars 1.44 met en tampon le côté gauche (pic ~1,8 Go
      au lieu de ~0,9 Go, croissance linéaire avec le volume). Bonus : l'ordre des lignes reste déterministe.
    """
    cog_table = cog.collect().lazy()
    joined = establishments.join(
        cog_table.with_columns(pl.lit(True).alias("_cog_hit")),
        left_on="codeCommuneEtablissement",
        right_on="code_commune",
        how="left",
        maintain_order="left",
    )
    return joined.with_columns(
        pl.when(pl.col("codeCommuneEtablissement").is_null())
        .then(pl.lit(GEO_STATUS_NO_COMMUNE))
        .when(pl.col("_cog_hit").fill_null(False))
        .then(pl.lit(GEO_STATUS_MATCHED))
        .otherwise(pl.lit(GEO_STATUS_UNMATCHED))
        .alias("geo_status")
    ).drop("_cog_hit")
