"""Requêtes analytiques sur les marts VALIDÉS (notebook 02) — aucune logique ETL ici.

Chaque fonction répond à une question analytique du README et retourne un `DataFrame` prêt à tracer.
Les libellés (région, département, section NAF, catégorie juridique, effectifs) viennent des référentiels
du paquet ; les codes restent la clé. Les sections NAF ne concernent QUE la NAF Rev.2.
"""

from __future__ import annotations

import polars as pl

from france_business_dynamics.transformation.geo_labels import DEPARTMENT_LABELS, REGION_LABELS
from france_business_dynamics.transformation.historical import (
    EVENT_CESSATION,
    EVENT_CLOSURE,
    EVENT_CREATION_DATE,
)
from france_business_dynamics.transformation.legal import ENTERPRISE_CATEGORY_LABELS, LEGAL_CATEGORY_LEVEL1
from france_business_dynamics.transformation.naf import SECTION_LABELS
from france_business_dynamics.transformation.workforce import WORKFORCE_SORT_ORDER
from france_business_dynamics.utils.config import UNKNOWN_CODE, UNKNOWN_LABEL, ProjectPaths

MART_FILES = {
    "establishments": "fact_establishment_snapshot.parquet",
    "legal_units": "fact_legal_unit_snapshot.parquet",
    "events": "fact_administrative_events.parquet",
}


def load_marts(paths: ProjectPaths) -> dict[str, pl.LazyFrame]:
    """Marts présents dans `data/processed` (les absents sont simplement omis)."""
    return {
        name: pl.scan_parquet(paths.processed / filename)
        for name, filename in MART_FILES.items()
        if (paths.processed / filename).exists()
    }


def _label(column: str, mapping: dict[str, str], default: str = UNKNOWN_LABEL) -> pl.Expr:
    return pl.col(column).replace_strict(mapping, default=default, return_dtype=pl.String)


def _status(frame: pl.LazyFrame, status: str | None) -> pl.LazyFrame:
    return frame if status is None else frame.filter(pl.col("status_code") == status)


# --- Q2 : géographie -----------------------------------------------------------------------------
def establishments_by_region(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    return (
        _status(mart, status).group_by("region_code")
        .agg(pl.col("establishment_count").sum().alias("etablissements"))
        .with_columns(_label("region_code", REGION_LABELS).alias("region"))
        .sort("etablissements", descending=True).collect(engine="streaming")
    )


def establishments_by_department(mart: pl.LazyFrame, status: str | None = "A", top_n: int | None = 20) -> pl.DataFrame:
    frame = (
        _status(mart, status).group_by("department_code")
        .agg(pl.col("establishment_count").sum().alias("etablissements"))
        .with_columns(_label("department_code", DEPARTMENT_LABELS).alias("departement"))
        .sort("etablissements", descending=True).collect(engine="streaming")
    )
    return frame if top_n is None else frame.head(top_n)


def top_communes(mart: pl.LazyFrame, status: str | None = "A", top_n: int = 15) -> pl.DataFrame:
    return (
        _status(mart, status).group_by("commune_code", "commune_name")
        .agg(pl.col("establishment_count").sum().alias("etablissements"))
        .sort("etablissements", descending=True).head(top_n).collect(engine="streaming")
    )


def geography_coverage(mart: pl.LazyFrame) -> pl.DataFrame:
    """Part des établissements par statut d'appariement géographique (MATCHED, UNMATCHED, NO_COMMUNE…)."""
    frame = mart.group_by("geo_status").agg(pl.col("establishment_count").sum().alias("etablissements")).collect(engine="streaming")
    return frame.with_columns((pl.col("etablissements") / frame["etablissements"].sum() * 100).round(3).alias("pct")
                              ).sort("etablissements", descending=True)


# --- Q3 : secteurs (NAF Rev.2 uniquement) ----------------------------------------------------------
def establishments_by_naf_section(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    """Sections NAF Rev.2 ; la ligne « Hors NAF Rev.2 » isole nomenclatures historiques et codes absents."""
    return (
        _status(mart, status).group_by("naf_section")
        .agg(pl.col("establishment_count").sum().alias("etablissements"))
        .with_columns(
            pl.col("naf_section").fill_null("—").alias("section"),
            pl.col("naf_section").replace_strict(SECTION_LABELS, default="Hors NAF Rev.2 ou non classé",
                                                 return_dtype=pl.String).alias("secteur"),
        )
        .sort("etablissements", descending=True).collect(engine="streaming")
    )


def naf_nomenclature_coverage(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    """Combien d'établissements relèvent de chaque nomenclature (justifie le périmètre NAF Rev.2)."""
    frame = (
        _status(mart, status).group_by("naf_nomenclature")
        .agg(pl.col("establishment_count").sum().alias("etablissements")).collect(engine="streaming")
    )
    return frame.with_columns((pl.col("etablissements") / frame["etablissements"].sum() * 100).round(3).alias("pct")
                              ).sort("etablissements", descending=True)


# --- Q4 : effectifs -------------------------------------------------------------------------------
def establishments_by_workforce(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    frame = (
        _status(mart, status).group_by("workforce_code", "workforce_label")
        .agg(pl.col("establishment_count").sum().alias("etablissements")).collect(engine="streaming")
    )
    return frame.with_columns(
        pl.col("workforce_code").replace_strict(WORKFORCE_SORT_ORDER, default=98, return_dtype=pl.Int32).alias("ordre")
    ).sort("ordre")


# --- Q1 / Q5 : flux annuels (PAS un stock reconstruit) -----------------------------------------------
def events_by_year(events: pl.LazyFrame) -> pl.DataFrame:
    """Un tableau année × type d'événement. Les trois séries ne s'additionnent jamais (voir methodology.md)."""
    long = events.group_by("event_year", "event_type").agg(pl.col("event_count").sum().alias("evenements")).collect(engine="streaming")
    wide = long.pivot(on="event_type", index="event_year", values="evenements").sort("event_year")
    for column in (EVENT_CESSATION, EVENT_CLOSURE, EVENT_CREATION_DATE):
        if column not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Int64).alias(column))
    return wide.select("event_year", EVENT_CREATION_DATE, EVENT_CLOSURE, EVENT_CESSATION).fill_null(0)


def events_by_section(events: pl.LazyFrame, event_type: str) -> pl.DataFrame:
    return (
        events.filter(pl.col("event_type") == event_type).group_by("naf_section")
        .agg(pl.col("event_count").sum().alias("evenements"))
        .with_columns(pl.col("naf_section").replace_strict(SECTION_LABELS, default="Hors NAF Rev.2 ou non classé",
                                                           return_dtype=pl.String).alias("secteur"))
        .sort("evenements", descending=True).collect(engine="streaming")
    )


# --- Q6 : unités légales ----------------------------------------------------------------------------
def legal_units_by_legal_category(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    return (
        _status(mart, status)
        .with_columns(pl.col("legal_category_code").str.slice(0, 1).alias("niveau1"))
        .group_by("niveau1").agg(pl.col("legal_unit_count").sum().alias("unites_legales"))
        .with_columns(_label("niveau1", LEGAL_CATEGORY_LEVEL1, "Non renseigné").alias("categorie_juridique"))
        .sort("unites_legales", descending=True).collect(engine="streaming")
    )


def legal_units_by_enterprise_category(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    return (
        _status(mart, status).group_by("enterprise_category")
        .agg(pl.col("legal_unit_count").sum().alias("unites_legales"))
        .with_columns(pl.col("enterprise_category").replace_strict(ENTERPRISE_CATEGORY_LABELS, default=UNKNOWN_LABEL,
                                                                   return_dtype=pl.String).alias("categorie"))
        .sort("unites_legales", descending=True).collect(engine="streaming")
    )


def legal_units_by_section(mart: pl.LazyFrame, status: str | None = "A") -> pl.DataFrame:
    return (
        _status(mart, status).group_by("naf_section").agg(pl.col("legal_unit_count").sum().alias("unites_legales"))
        .with_columns(pl.col("naf_section").replace_strict(SECTION_LABELS, default="Hors NAF Rev.2 ou non classé",
                                                           return_dtype=pl.String).alias("secteur"))
        .sort("unites_legales", descending=True).collect(engine="streaming")
    )


def headline_figures(marts: dict[str, pl.LazyFrame]) -> dict[str, int]:
    """Indicateurs d'en-tête (sans interprétation économique)."""
    figures: dict[str, int] = {}
    if "establishments" in marts:
        frame = marts["establishments"].group_by("status_code").agg(pl.col("establishment_count").sum()).collect(engine="streaming")
        counts = dict(frame.iter_rows())
        figures.update(etablissements_actifs=counts.get("A", 0), etablissements_fermes=counts.get("F", 0),
                       etablissements_total=sum(counts.values()))
    if "legal_units" in marts:
        frame = marts["legal_units"].group_by("status_code").agg(pl.col("legal_unit_count").sum()).collect(engine="streaming")
        counts = dict(frame.iter_rows())
        figures.update(unites_legales_actives=counts.get("A", 0), unites_legales_cessees=counts.get("C", 0),
                       unites_legales_total=sum(counts.values()))
    return figures


UNKNOWN_SENTINEL = UNKNOWN_CODE
