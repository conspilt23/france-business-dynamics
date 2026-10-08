from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from france_business_dynamics.transformation.current import (
    clean_text,
    normalize_dates,
    padded_id,
    parse_date,
    transform_etablissement,
)

pytestmark = pytest.mark.layer("E-transformation")


def test_padded_id_restores_leading_zeros_even_from_integers():
    frame = pl.DataFrame({"siren": [12345678, 123456789, None], "nic": [1, 12345, 7]})
    result = frame.lazy().select(padded_id("siren", 9), padded_id("nic", 5)).collect()
    assert result["siren"].to_list() == ["012345678", "123456789", None]
    assert result["nic"].to_list() == ["00001", "12345", "00007"]


def test_clean_text_strips_and_turns_blank_into_null():
    result = pl.DataFrame({"x": ["  A ", "", "   ", None, "B"]}).lazy().select(clean_text("x")).collect()
    assert result["x"].to_list() == ["A", None, None, None, "B"]


def test_parse_date_from_strings_flags_invalid_but_keeps_row():
    frame = pl.LazyFrame({"d": ["2022-03-15", "2022-13-45", "", None, " 2021-01-01 "]})
    result = parse_date(frame, "d", flag="bad").collect()
    assert result["d"].to_list() == [date(2022, 3, 15), None, None, None, date(2021, 1, 1)]
    assert result["bad"].to_list() == [False, True, False, False, False]
    assert result.height == 5


def test_parse_date_is_a_noop_when_source_is_already_a_date():
    frame = pl.LazyFrame({"d": [date(2022, 3, 15), None]})
    result = parse_date(frame, "d", flag="bad").collect()
    assert result["d"].to_list() == [date(2022, 3, 15), None]
    assert result["bad"].to_list() == [False, False]


def test_normalize_dates_ignores_absent_columns():
    frame = pl.LazyFrame({"a": ["2022-01-01"]})
    assert normalize_dates(frame, ["a", "missing"]).collect()["a"].dtype == pl.Date


def _estab(**overrides) -> pl.LazyFrame:
    base = {
        "siren": ["123456789"], "nic": ["00011"], "siret": ["12345678900011"],
        "dateCreationEtablissement": ["2022-03-15"], "dateDebut": ["2022-03-15"],
        "trancheEffectifsEtablissement": ["11"], "etablissementSiege": ["true"],
        "etatAdministratifEtablissement": ["A"], "codeCommuneEtablissement": ["01004"],
        "codePostalEtablissement": ["01500"], "activitePrincipaleEtablissement": ["47.11A"],
        "nomenclatureActivitePrincipaleEtablissement": ["NAFRev2"],
        "activitePrincipaleNAF25Etablissement": ["47.11F"],
    }
    base.update(overrides)
    return pl.LazyFrame(base)


@pytest.mark.parametrize("siege", [["true"], [True], ["O"], ["1"]])
def test_siege_flag_is_boolean_whatever_the_source_encoding(siege):
    assert transform_etablissement(_estab(etablissementSiege=siege)).collect()["etablissementSiege"].to_list() == [True]


def test_false_siege_and_flags():
    result = transform_etablissement(_estab(etablissementSiege=["false"], etatAdministratifEtablissement=["F"])).collect()
    assert result["etablissementSiege"].to_list() == [False]
    assert result["is_active"].to_list() == [False] and result["is_closed"].to_list() == [True]


def test_naf25_is_preserved_untouched_and_never_mapped():
    result = transform_etablissement(_estab()).collect()
    assert result["activitePrincipaleNAF25Etablissement"].to_list() == ["47.11F"]
    assert result["naf_section"].to_list() == ["G"]  # section issue de la NAF Rev.2 uniquement


def test_creation_year_is_derived_from_the_parsed_date():
    assert transform_etablissement(_estab()).collect()["creation_year"].to_list() == [2022]


@pytest.mark.parametrize("nomenclature", ["NAFRev1", "NAF1993", "NAP", "NAF2025"])
def test_rev2_shaped_code_under_another_nomenclature_gets_no_section_end_to_end(nomenclature):
    """Cas dangereux : le code a la FORME d'un code Rev.2 (99.99A) mais appartient à une autre nomenclature.

    Seul le contrôle explicite de la nomenclature l'empêche de recevoir une section (le format seul ne suffit pas).
    """
    from france_business_dynamics.transformation.marts import establishment_snapshot_from_frame
    from france_business_dynamics.transformation.workforce import add_workforce_label
    from france_business_dynamics.transformation.geography import GEO_STATUS_NOT_ENRICHED

    curated = add_workforce_label(
        transform_etablissement(_estab(activitePrincipaleEtablissement=["62.01Z"],
                                       nomenclatureActivitePrincipaleEtablissement=[nomenclature])),
        "trancheEffectifsEtablissement",
    ).with_columns(
        pl.lit(None, dtype=pl.String).alias("commune"), pl.lit(None, dtype=pl.String).alias("code_departement"),
        pl.lit(None, dtype=pl.String).alias("code_region"), pl.lit(GEO_STATUS_NOT_ENRICHED).alias("geo_status"),
    )
    layer = curated.collect()
    assert layer["naf_section"].to_list() == [None] and layer["is_naf_rev2"].to_list() == [False]
    mart = establishment_snapshot_from_frame(curated).collect()
    assert mart["naf_section"].to_list() == [None] and mart["naf_nomenclature"].to_list() == [nomenclature]
