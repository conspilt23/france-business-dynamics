"""Marts analytiques : tables de faits agrégées, grain explicite, prêtes pour SQL Server / Power BI.

Chaque mart est construit UNIQUEMENT à partir de la couche courante (ou des historiques pour les
événements) : aucune logique de nettoyage n'est dupliquée ici (elle vit dans `current.py`).

Grains (clés fonctionnelles — vérifiées par le quality gate) :
* établissements : date_key × commune_code × naf_nomenclature × naf_code × workforce_code × status_code
* unités légales : date_key × naf_nomenclature × naf_code × legal_category_code × enterprise_category × status_code
* événements     : event_year × entity_type × event_type × naf_nomenclature × naf_code

Les attributs (nom de commune, département, région, section NAF, libellé d'effectif) sont dépendants
fonctionnellement de la clé : ils n'augmentent pas le nombre de lignes.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.transformation.historical import (
    administrative_establishment_events,
    administrative_unit_legal_events,
    creation_date_events,
)
from france_business_dynamics.transformation.pipeline import build_etablissement_current
from france_business_dynamics.utils.config import (
    ANALYSIS_END_YEAR,
    ANALYSIS_START_YEAR,
    SNAPSHOT_DATE,
    UNKNOWN_CODE,
    UNKNOWN_LABEL,
    UNKNOWN_STATUS,
    snapshot_date_key,
)
from france_business_dynamics.validation.schemas import (
    ETABLISSEMENT_HISTORICAL_COLUMNS,
    UNIT_LEGALE_HISTORICAL_COLUMNS,
    require_columns,
)

ESTABLISHMENT_GRAIN = [
    "date_key", "commune_code", "naf_nomenclature", "naf_code", "workforce_code", "status_code",
]
LEGAL_UNIT_GRAIN = [
    "date_key", "naf_nomenclature", "naf_code", "legal_category_code", "enterprise_category", "status_code",
]
EVENTS_GRAIN = ["event_year", "entity_type", "event_type", "naf_nomenclature", "naf_code"]

ESTABLISHMENT_ATTRIBUTES = [
    "commune_name", "department_code", "region_code", "geo_status", "naf_section",
    "workforce_label",
]
LEGAL_UNIT_ATTRIBUTES = ["naf_section"]


def _fill(column: str, sentinel: str, alias: str | None = None) -> pl.Expr:
    return pl.col(column).fill_null(sentinel).alias(alias or column)


# --------------------------------------------------------------------------- #
# Établissements
# --------------------------------------------------------------------------- #
def establishment_snapshot_from_frame(
    curated: pl.LazyFrame, snapshot_date: str = SNAPSHOT_DATE
) -> pl.LazyFrame:
    return (
        curated.with_columns(
            pl.lit(snapshot_date_key(snapshot_date), dtype=pl.Int32).alias("date_key"),
            _fill("codeCommuneEtablissement", UNKNOWN_CODE, "commune_code"),
            _fill("commune", UNKNOWN_LABEL, "commune_name"),
            _fill("code_departement", UNKNOWN_CODE, "department_code"),
            _fill("code_region", UNKNOWN_CODE, "region_code"),
            _fill("naf_nomenclature", UNKNOWN_CODE, "naf_nomenclature"),
            _fill("activitePrincipaleEtablissement", UNKNOWN_CODE, "naf_code"),
            _fill("trancheEffectifsEtablissement", UNKNOWN_CODE, "workforce_code"),
            _fill("workforce_label", UNKNOWN_LABEL, "workforce_label"),
            _fill("etatAdministratifEtablissement", UNKNOWN_STATUS, "status_code"),
        )
        .group_by(ESTABLISHMENT_GRAIN + ESTABLISHMENT_ATTRIBUTES)
        .agg(pl.len().cast(pl.Int64).alias("establishment_count"))
        .select(ESTABLISHMENT_GRAIN[:2] + ["commune_name", "department_code", "region_code", "geo_status"]
                + ESTABLISHMENT_GRAIN[2:4] + ["naf_section"] + ["workforce_code", "workforce_label", "status_code"]
                + ["establishment_count"])
    )


def current_establishment_snapshot_from_curated(
    curated_path: Path, limit: int | None = None, snapshot_date: str = SNAPSHOT_DATE
) -> pl.LazyFrame:
    """Mart établissements depuis `etablissement_current.parquet`."""
    frame = pl.scan_parquet(curated_path)
    if limit is not None:
        frame = frame.head(limit)
    return establishment_snapshot_from_frame(frame, snapshot_date)


def current_establishment_snapshot(
    establishment_path: Path, cog_path: Path, limit: int | None = None
) -> pl.LazyFrame:
    """Compatibilité : mart établissements directement depuis le stock brut + COG."""
    return establishment_snapshot_from_frame(
        build_etablissement_current(establishment_path, cog_path=cog_path, limit=limit)
    )


# --------------------------------------------------------------------------- #
# Unités légales
# --------------------------------------------------------------------------- #
def legal_unit_snapshot_from_frame(
    curated: pl.LazyFrame, snapshot_date: str = SNAPSHOT_DATE
) -> pl.LazyFrame:
    return (
        curated.with_columns(
            pl.lit(snapshot_date_key(snapshot_date), dtype=pl.Int32).alias("date_key"),
            _fill("naf_nomenclature", UNKNOWN_CODE, "naf_nomenclature"),
            _fill("activitePrincipaleUniteLegale", UNKNOWN_CODE, "naf_code"),
            _fill("categorieJuridiqueUniteLegale", UNKNOWN_CODE, "legal_category_code"),
            _fill("categorieEntreprise", UNKNOWN_CODE, "enterprise_category"),
            _fill("etatAdministratifUniteLegale", UNKNOWN_STATUS, "status_code"),
        )
        .group_by(LEGAL_UNIT_GRAIN + LEGAL_UNIT_ATTRIBUTES)
        .agg(pl.len().cast(pl.Int64).alias("legal_unit_count"))
        .select(LEGAL_UNIT_GRAIN[:3] + ["naf_section"] + LEGAL_UNIT_GRAIN[3:] + ["legal_unit_count"])
    )


def legal_unit_current_snapshot(
    unite_legale_path: Path, limit: int | None = None, snapshot_date: str = SNAPSHOT_DATE
) -> pl.LazyFrame:
    """Mart unités légales depuis `unite_legale_current.parquet`."""
    frame = pl.scan_parquet(unite_legale_path)
    if limit is not None:
        frame = frame.head(limit)
    return legal_unit_snapshot_from_frame(frame, snapshot_date)


# --------------------------------------------------------------------------- #
# Événements administratifs
# --------------------------------------------------------------------------- #
def administrative_events(
    unite_legale_historical_path: Path,
    etablissement_historical_path: Path,
    establishment_current_path: Path,
    start_year: int = ANALYSIS_START_YEAR,
    end_year: int = ANALYSIS_END_YEAR,
) -> pl.LazyFrame:
    """Cessations (UL), fermetures (établissements) et dates de création enregistrées, par année."""
    require_columns(unite_legale_historical_path, UNIT_LEGALE_HISTORICAL_COLUMNS,
                    dataset="StockUniteLegaleHistorique")
    require_columns(etablissement_historical_path, ETABLISSEMENT_HISTORICAL_COLUMNS,
                    dataset="StockEtablissementHistorique")

    legal = pl.scan_parquet(unite_legale_historical_path).select(UNIT_LEGALE_HISTORICAL_COLUMNS)
    estab = pl.scan_parquet(etablissement_historical_path).select(ETABLISSEMENT_HISTORICAL_COLUMNS)
    curated = pl.scan_parquet(establishment_current_path)

    parts = [
        administrative_unit_legal_events(legal, start_year, end_year),
        administrative_establishment_events(estab, start_year, end_year),
        creation_date_events(curated, start_year, end_year),
    ]
    columns = EVENTS_GRAIN[:5] + ["naf_section", "event_count"]
    parts = [part.select(
        pl.col("event_year").cast(pl.Int16),
        "entity_type", "event_type", "naf_nomenclature", "naf_code", "naf_section",
        pl.col("event_count").cast(pl.Int64),
    ) for part in parts]
    # Une même clé ne peut venir que d'une seule branche (event_type distinct) : concat sans ré-agrégation.
    return pl.concat(parts, how="vertical").select(columns)
