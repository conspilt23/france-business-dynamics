"""Assemblage des couches courantes (LazyFrame : rien n'est calculé avant `sink_parquet`)."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.transformation.current import (
    transform_etablissement,
    transform_unite_legale,
)
from france_business_dynamics.transformation.geography import (
    GEO_STATUS_NOT_ENRICHED,
    enrich_establishments_with_cog,
    read_cog_2026,
)
from france_business_dynamics.transformation.workforce import add_workforce_label
from france_business_dynamics.validation.schemas import (
    ETABLISSEMENT_COLUMNS,
    UNITE_LEGALE_COLUMNS,
    require_columns,
)


def build_unite_legale_current(input_path: Path, limit: int | None = None) -> pl.LazyFrame:
    """Couche courante des unités légales (colonnes sélectionnées tôt, `limit` pour smoke test)."""
    require_columns(input_path, UNITE_LEGALE_COLUMNS, dataset="StockUniteLegale")
    frame = pl.scan_parquet(input_path).select(UNITE_LEGALE_COLUMNS)
    if limit is not None:
        frame = frame.head(limit)
    frame = transform_unite_legale(frame)
    return add_workforce_label(frame, "trancheEffectifsUniteLegale")


def build_etablissement_current(
    input_path: Path, cog_path: Path | None = None, limit: int | None = None
) -> pl.LazyFrame:
    """Couche courante des établissements, enrichie du COG si `cog_path` est fourni."""
    require_columns(input_path, ETABLISSEMENT_COLUMNS, dataset="StockEtablissement")
    frame = pl.scan_parquet(input_path).select(ETABLISSEMENT_COLUMNS)
    if limit is not None:
        frame = frame.head(limit)
    frame = transform_etablissement(frame)
    frame = add_workforce_label(frame, "trancheEffectifsEtablissement")
    if cog_path is not None:
        frame = enrich_establishments_with_cog(frame, read_cog_2026(cog_path))
    else:
        frame = frame.with_columns(
            pl.lit(None, dtype=pl.String).alias("type_commune"),
            pl.lit(None, dtype=pl.String).alias("code_departement"),
            pl.lit(None, dtype=pl.String).alias("code_region"),
            pl.lit(None, dtype=pl.String).alias("commune"),
            pl.lit(GEO_STATUS_NOT_ENRICHED).alias("geo_status"),
        )
    return frame
