"""Historiques SIRENE : reconstitution des CHANGEMENTS D'ÉTAT ADMINISTRATIF.

Grain d'entrée : une ligne = une *période* de validité (`dateDebut` → `dateFin`) d'une unité légale
ou d'un établissement. Un indicateur `changement…` vaut vrai quand la période marque un changement de
la variable par rapport à la précédente.

Événements produits (comptés, jamais « devinés ») :
* unité légale  : période dont l'état devient `C` (cessée)  → « Cessation administrative » ;
* établissement : période dont l'état devient `F` (fermé)   → « Fermeture administrative ».

Limites (docs/methodology.md) : un événement n'est pas une « mortalité d'entreprise » ; une unité peut
cesser puis être réactivée (plusieurs événements) ; une cessation d'unité légale entraîne des fermetures
d'établissements — les deux séries ne s'additionnent pas.
"""

from __future__ import annotations

import polars as pl

from france_business_dynamics.transformation.current import TRUE_TOKENS, clean_text, parse_date
from france_business_dynamics.transformation.naf import add_naf_section
from france_business_dynamics.utils.config import UNKNOWN_CODE
from france_business_dynamics.utils.exprs import equals_any

ENTITY_LEGAL_UNIT = "Unité légale"
ENTITY_ESTABLISHMENT = "Établissement"
EVENT_CESSATION = "Cessation administrative"
EVENT_CLOSURE = "Fermeture administrative"
EVENT_CREATION_DATE = "Date de création enregistrée"

EVENT_GROUP_KEYS = [
    "event_year", "entity_type", "event_type", "naf_nomenclature", "naf_code", "naf_section",
]


def _truthy_change(column: str) -> pl.Expr:
    """Indicateur de changement, qu'il soit booléen ou encodé en texte (O/N, true/false)."""
    value = pl.col(column).cast(pl.String).str.to_lowercase().str.strip_chars()
    return equals_any(value, TRUE_TOKENS)


def _within_years(frame: pl.LazyFrame, column: str, start_year: int, end_year: int) -> pl.LazyFrame:
    """Filtre précoce sur l'année, dans le type natif (permet le pushdown Parquet)."""
    dtype = frame.collect_schema()[column]
    if dtype == pl.String:  # ISO AAAA-MM-JJ : l'ordre lexicographique = l'ordre chronologique
        return frame.filter(
            pl.col(column).is_between(
                pl.lit(f"{start_year:04d}-01-01"), pl.lit(f"{end_year:04d}-12-31")
            )
        )
    from datetime import date

    return frame.filter(
        pl.col(column).cast(pl.Date).is_between(
            pl.lit(date(start_year, 1, 1)), pl.lit(date(end_year, 12, 31))
        )
    )


def _shape_events(
    frame: pl.LazyFrame, *, entity_type: str, event_type: str, code_column: str, nomenclature_column: str
) -> pl.LazyFrame:
    frame = frame.with_columns(
        clean_text(code_column),
        clean_text(nomenclature_column),
    )
    frame = add_naf_section(
        frame, code_column, "naf_section", nomenclature_column=nomenclature_column
    )
    return (
        frame.with_columns(
            pl.col("dateDebut").dt.year().cast(pl.Int16).alias("event_year"),
            pl.lit(entity_type).alias("entity_type"),
            pl.lit(event_type).alias("event_type"),
            pl.col(nomenclature_column).fill_null(UNKNOWN_CODE).alias("naf_nomenclature"),
            pl.col(code_column).fill_null(UNKNOWN_CODE).alias("naf_code"),
        )
        .group_by(EVENT_GROUP_KEYS)
        .agg(pl.len().cast(pl.Int64).alias("event_count"))
    )


def administrative_unit_legal_events(frame: pl.LazyFrame, start_year: int, end_year: int) -> pl.LazyFrame:
    """Cessations administratives d'unités légales (changement d'état vers `C`)."""
    frame = _within_years(frame, "dateDebut", start_year, end_year)
    frame = frame.filter(
        _truthy_change("changementEtatAdministratifUniteLegale")
        & (pl.col("etatAdministratifUniteLegale").cast(pl.String).str.strip_chars() == "C")
    )
    frame = parse_date(frame, "dateDebut")
    return _shape_events(
        frame,
        entity_type=ENTITY_LEGAL_UNIT,
        event_type=EVENT_CESSATION,
        code_column="activitePrincipaleUniteLegale",
        nomenclature_column="nomenclatureActivitePrincipaleUniteLegale",
    )


def administrative_establishment_events(
    frame: pl.LazyFrame, start_year: int, end_year: int
) -> pl.LazyFrame:
    """Fermetures administratives d'établissements (changement d'état vers `F`)."""
    frame = _within_years(frame, "dateDebut", start_year, end_year)
    frame = frame.filter(
        _truthy_change("changementEtatAdministratifEtablissement")
        & (pl.col("etatAdministratifEtablissement").cast(pl.String).str.strip_chars() == "F")
    )
    frame = parse_date(frame, "dateDebut")
    return _shape_events(
        frame,
        entity_type=ENTITY_ESTABLISHMENT,
        event_type=EVENT_CLOSURE,
        code_column="activitePrincipaleEtablissement",
        nomenclature_column="nomenclatureActivitePrincipaleEtablissement",
    )


def creation_date_events(curated: pl.LazyFrame, start_year: int, end_year: int) -> pl.LazyFrame:
    """Établissements dont la date de création ENREGISTRÉE tombe dans la fenêtre.

    ⚠ Ce n'est pas la « création d'établissements » des statistiques démographiques de l'Insee, et le
    stock ne reflète que les unités encore présentes (les unités purgées — cf. colonne `unitePurgeeUniteLegale`,
    non retenue ici — pourraient être mal couvertes : biais de survie possible sur les années anciennes,
    à vérifier dans la documentation Insee).
    """
    frame = curated.filter(
        pl.col("dateCreationEtablissement").dt.year().is_between(start_year, end_year)
    ).with_columns(
        pl.col("dateCreationEtablissement").alias("dateDebut")  # réutilise la mise en forme commune
    )
    return _shape_events(
        frame,
        entity_type=ENTITY_ESTABLISHMENT,
        event_type=EVENT_CREATION_DATE,
        code_column="activitePrincipaleEtablissement",
        nomenclature_column="nomenclatureActivitePrincipaleEtablissement",
    )
