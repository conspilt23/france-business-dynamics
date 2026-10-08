"""Couche « courante » (curated) : une ligne par SIREN / SIRET, types et identifiants normalisés.

Principes :
* aucun filtrage silencieux : le nombre de lignes en sortie = nombre de lignes en entrée ;
* les valeurs invalides sont CONSERVÉES et signalées par des indicateurs (`*_invalid`, `geo_status`) ;
* les colonnes source gardent leur nom ; les colonnes dérivées sont en snake_case.
"""

from __future__ import annotations

import polars as pl

from france_business_dynamics.transformation.naf import add_naf_section
from france_business_dynamics.utils.exprs import equals_any

TRUE_TOKENS = ["true", "1", "o", "oui", "yes", "y"]


# --------------------------------------------------------------------------- #
# Briques réutilisables
# --------------------------------------------------------------------------- #
def clean_text(column: str) -> pl.Expr:
    """Chaîne sans espaces superflus ; chaîne vide → null."""
    text = pl.col(column).cast(pl.String).str.strip_chars()
    return pl.when(text == "").then(None).otherwise(text).alias(column)


def padded_id(column: str, width: int) -> pl.Expr:
    """Identifiant en chaîne, zéros initiaux restaurés (SIREN 9, NIC 5, SIRET 14)."""
    return clean_text(column).str.zfill(width).alias(column)


def parse_date(frame: pl.LazyFrame, column: str, *, flag: str | None = None) -> pl.LazyFrame:
    """Convertit `column` en `Date` (ISO AAAA-MM-JJ). Les valeurs non convertibles deviennent nulles.

    `flag` : nom d'une colonne booléenne vraie quand la valeur source était renseignée mais invalide.
    Fonctionne que la colonne source soit déjà une date ou une chaîne.
    """
    dtype = frame.collect_schema()[column]
    if dtype == pl.Date:
        return frame if flag is None else frame.with_columns(pl.lit(False).alias(flag))
    if dtype == pl.Datetime:
        parsed = pl.col(column).dt.date()
        raw_present = pl.col(column).is_not_null()
    else:
        text = pl.col(column).cast(pl.String).str.strip_chars()
        parsed = text.str.strptime(pl.Date, format="%Y-%m-%d", strict=False)
        raw_present = text.is_not_null() & (text != "")
    expressions = []
    if flag is not None:
        expressions.append((raw_present & parsed.is_null()).alias(flag))
    expressions.append(parsed.alias(column))
    return frame.with_columns(expressions)


def _boolean(frame: pl.LazyFrame, column: str) -> pl.Expr:
    dtype = frame.collect_schema()[column]
    if dtype == pl.Boolean:
        return pl.col(column)
    return equals_any(pl.col(column).cast(pl.String).str.to_lowercase().str.strip_chars(), TRUE_TOKENS)


def normalize_dates(frame: pl.LazyFrame, columns: list[str]) -> pl.LazyFrame:
    """Compatibilité : convertit les colonnes de dates présentes."""
    available = frame.collect_schema().names()
    for column in columns:
        if column in available:
            frame = parse_date(frame, column)
    return frame


# --------------------------------------------------------------------------- #
# Unité légale
# --------------------------------------------------------------------------- #
def transform_unite_legale(frame: pl.LazyFrame) -> pl.LazyFrame:
    frame = parse_date(frame, "dateCreationUniteLegale", flag="date_creation_invalid")
    frame = parse_date(frame, "dateDebut")
    frame = frame.with_columns(
        padded_id("siren", 9),
        clean_text("etatAdministratifUniteLegale"),
        clean_text("categorieEntreprise"),
        clean_text("categorieJuridiqueUniteLegale"),
        clean_text("trancheEffectifsUniteLegale"),
        clean_text("activitePrincipaleUniteLegale"),
        clean_text("nomenclatureActivitePrincipaleUniteLegale"),
        clean_text("activitePrincipaleNAF25UniteLegale"),
    )
    frame = frame.with_columns(
        pl.col("nomenclatureActivitePrincipaleUniteLegale").alias("naf_nomenclature")
    )
    frame = add_naf_section(
        frame,
        "activitePrincipaleUniteLegale",
        "naf_section",
        nomenclature_column="nomenclatureActivitePrincipaleUniteLegale",
    )
    return frame.with_columns(
        pl.col("dateCreationUniteLegale").dt.year().alias("creation_year"),
        (pl.col("etatAdministratifUniteLegale") == "A").alias("is_active"),
        (pl.col("etatAdministratifUniteLegale") == "C").alias("is_ceased"),
    )


# --------------------------------------------------------------------------- #
# Établissement
# --------------------------------------------------------------------------- #
def transform_etablissement(frame: pl.LazyFrame) -> pl.LazyFrame:
    frame = parse_date(frame, "dateCreationEtablissement", flag="date_creation_invalid")
    frame = parse_date(frame, "dateDebut")
    frame = frame.with_columns(
        padded_id("siren", 9),
        padded_id("nic", 5),
        padded_id("siret", 14),
        _boolean(frame, "etablissementSiege").alias("etablissementSiege"),
        clean_text("etatAdministratifEtablissement"),
        clean_text("trancheEffectifsEtablissement"),
        clean_text("codeCommuneEtablissement"),
        clean_text("codePostalEtablissement"),
        clean_text("activitePrincipaleEtablissement"),
        clean_text("nomenclatureActivitePrincipaleEtablissement"),
        clean_text("activitePrincipaleNAF25Etablissement"),
    )
    frame = frame.with_columns(
        pl.col("nomenclatureActivitePrincipaleEtablissement").alias("naf_nomenclature")
    )
    frame = add_naf_section(
        frame,
        "activitePrincipaleEtablissement",
        "naf_section",
        nomenclature_column="nomenclatureActivitePrincipaleEtablissement",
    )
    return frame.with_columns(
        pl.col("dateCreationEtablissement").dt.year().alias("creation_year"),
        (pl.col("etatAdministratifEtablissement") == "A").alias("is_active"),
        (pl.col("etatAdministratifEtablissement") == "F").alias("is_closed"),
    )
