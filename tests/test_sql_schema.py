"""Régression de l'erreur SQL historique : `String data, right truncation: length 14 buffer 6` sur stg.EstablishmentSnapshot.

Ancienne version de ce test : elle vérifiait un PARCHE précis (`varchar(30)` + `ALTER COLUMN`). Ce design a été
remplacé par une reconstruction déterministe alignée sur un contrat (voir sql_contract.py). L'intention d'origine
— « les valeurs réelles qui ont provoqué l'erreur doivent tenir » — est conservée ici, contrôlée sur les VRAIES
valeurs et sur les DEUX schémas (contrat Python et DDL réel), pas sur un morceau de texte.
"""

from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.sql_contract import BY_NAME, check_summary, summarize
from france_business_dynamics.transformation.workforce import WORKFORCE_LABELS
from tests.support.ddl import parse_tables

pytestmark = pytest.mark.layer("G-sql")

# Valeurs observées dans l'INSERT qui a échoué (log utilisateur) + nomenclatures SIRENE connues.
OBSERVED_NOMENCLATURES = ["NAFRev2", "NAFRev1", "NAF1993", "NAP", "NAF2025", "UNKNOWN"]
OBSERVED_LABELS = ["Non employeur", *WORKFORCE_LABELS.values(), "Non renseigné"]


def test_values_that_broke_the_original_insert_now_fit_the_contract():
    contract = BY_NAME["EstablishmentSnapshot"]
    frame = pl.LazyFrame({
        "date_key": [20261001] * len(OBSERVED_NOMENCLATURES),
        "commune_code": ["2A001"] * len(OBSERVED_NOMENCLATURES),
        "commune_name": ["Ambérieu-en-Bugey"] * len(OBSERVED_NOMENCLATURES),
        "department_code": ["974"] * len(OBSERVED_NOMENCLATURES),
        "region_code": ["04"] * len(OBSERVED_NOMENCLATURES),
        "geo_status": ["NOT_ENRICHED"] * len(OBSERVED_NOMENCLATURES),
        "naf_nomenclature": OBSERVED_NOMENCLATURES,
        "naf_code": ["47.11A"] * len(OBSERVED_NOMENCLATURES),
        "naf_section": [None] * len(OBSERVED_NOMENCLATURES),
        "workforce_code": ["UNKNOWN"] * len(OBSERVED_NOMENCLATURES),
        "workforce_label": ["Non employeur"] * len(OBSERVED_NOMENCLATURES),
        "status_code": ["U"] * len(OBSERVED_NOMENCLATURES),
        "establishment_count": [1] * len(OBSERVED_NOMENCLATURES),
    })
    assert check_summary(summarize(frame, contract), contract) == []


def test_every_workforce_label_fits_the_staging_and_the_dimension_columns():
    ddl = parse_tables("04_create_facts.sql")
    staging_width = ddl["stg.EstablishmentSnapshot"]["workforce_label"][1]
    dimension_width = parse_tables("03_create_dimensions.sql")["analytics.DimWorkforce"]["WorkforceLabel"][1]
    assert max(len(label) for label in OBSERVED_LABELS) <= staging_width <= dimension_width


def test_the_failing_widths_of_the_old_schema_would_have_been_flagged():
    """Reproduit l'ancien schéma (naf_nomenclature varchar(6)) : le contrôle de schéma vivant le refuse."""
    from france_business_dynamics.sql_contract import check_live_schema

    contract = BY_NAME["EstablishmentSnapshot"]
    live = [(c.name, c.sql_type, 6 if c.name == "naf_nomenclature" else c.length, "YES" if c.nullable else "NO")
            for c in contract.columns]
    problems = check_live_schema(contract, live)
    assert [p.column for p in problems] == ["naf_nomenclature"] and problems[0].kind == "width"


def test_old_staging_widths_that_caused_trouble_are_no_longer_present():
    ddl = parse_tables("04_create_facts.sql")["stg.EstablishmentSnapshot"]
    assert ddl["status_code"][1] >= 5 and ddl["department_code"][1] >= 10 and ddl["naf_nomenclature"][1] >= 20
