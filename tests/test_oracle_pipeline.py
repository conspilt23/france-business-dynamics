"""Le pipeline Polars doit reproduire l'oracle Python pur, pour les DEUX formats de date possibles."""

from __future__ import annotations

import polars as pl
import pytest

from tests.support import oracle

pytestmark = pytest.mark.layer("E-transformation")

DATE_MODES = ["fixture_string", "fixture_date"]


@pytest.fixture(params=DATE_MODES)
def paths(request):
    return request.getfixturevalue(request.param)


def read(paths, name):
    return pl.read_parquet(paths.processed / f"{name}.parquet")


# ---------------------------------------------------------------------------------------------
# Couche courante
# ---------------------------------------------------------------------------------------------
def test_curated_layers_lose_no_row(paths):
    assert read(paths, "etablissement_current").height == len(oracle.rows("etablissement_sample.csv"))
    assert read(paths, "unite_legale_current").height == len(oracle.rows("unite_legale_sample.csv"))


def test_establishment_ids_are_padded_strings(paths):
    frame = read(paths, "etablissement_current")
    assert frame.schema["siret"] == pl.String
    assert frame["siret"].str.len_chars().unique().to_list() == [14]
    assert frame["siren"].str.len_chars().unique().to_list() == [9]
    assert "01234567800001" in frame["siret"].to_list()  # SIRET court → zéro initial restauré
    assert "012345678" in frame["siren"].to_list()
    ul = read(paths, "unite_legale_current")
    assert ul["siren"].str.len_chars().unique().to_list() == [9]
    assert "098765432" in ul["siren"].to_list()  # zéro initial déjà présent : conservé
    assert "012345678" in ul["siren"].to_list()  # SIREN court → zéro initial restauré


def test_invalid_creation_date_is_kept_and_flagged_not_dropped(fixture_string):
    paths = fixture_string  # seul un champ TEXTE peut contenir une date illisible
    frame = read(paths, "etablissement_current")
    invalid = frame.filter(pl.col("date_creation_invalid"))
    expected_invalid = [r["siren"] for r in oracle.rows("etablissement_sample.csv")
                        if r["dateCreationEtablissement"] and oracle.parse_date(r["dateCreationEtablissement"]) is None]
    assert invalid["siren"].to_list() == expected_invalid == ["500000005"]
    assert invalid["dateCreationEtablissement"].null_count() == 1  # valeur invalide → date nulle, ligne conservée


def test_date_typed_source_cannot_hold_invalid_dates_so_nothing_is_flagged(fixture_date):
    frame = read(fixture_date, "etablissement_current")
    assert frame["date_creation_invalid"].sum() == 0
    assert frame.height == len(oracle.rows("etablissement_sample.csv"))  # aucune ligne perdue pour autant


def test_geo_status_distribution_matches_the_fixture_design(paths):
    counts = read(paths, "etablissement_current")["geo_status"].value_counts().to_dicts()
    assert {r["geo_status"]: r["count"] for r in counts} == {"MATCHED": 24, "UNMATCHED": 1, "NO_COMMUNE": 1}


def test_section_is_null_outside_naf_rev2_in_the_curated_layers(paths):
    for name in ("etablissement_current", "unite_legale_current"):
        frame = read(paths, name)
        assert frame.filter((pl.col("naf_nomenclature") != "NAFRev2") & pl.col("naf_section").is_not_null()).is_empty()
        assert frame.filter(pl.col("naf_nomenclature").is_in(["NAFRev1", "NAF1993", "NAP"])).height > 0


# ---------------------------------------------------------------------------------------------
# Marts vs oracle
# ---------------------------------------------------------------------------------------------
def test_establishment_mart_matches_oracle_cell_by_cell(paths):
    mart = read(paths, "fact_establishment_snapshot")
    actual = {
        (r["commune_code"], r["naf_nomenclature"], r["naf_code"], r["workforce_code"], r["status_code"]): r["establishment_count"]
        for r in mart.to_dicts()
    }
    assert actual == oracle.establishment_mart()
    assert mart["establishment_count"].sum() == 26


def test_establishment_mart_attributes_match_independent_cog_oracle(paths):
    cog = oracle.cog()
    for row in read(paths, "fact_establishment_snapshot").to_dicts():
        code = row["commune_code"]
        if code in cog:
            assert (row["commune_name"], row["department_code"], row["region_code"]) == (
                cog[code]["name"], cog[code]["dep"], cog[code]["reg"]), code
            assert row["geo_status"] == "MATCHED"
        else:
            assert (row["commune_name"], row["department_code"], row["region_code"]) == (
                "Non renseigné", "UNKNOWN", "UNKNOWN")
            assert row["geo_status"] in {"UNMATCHED", "NO_COMMUNE"}
    codes = set(read(paths, "fact_establishment_snapshot")["commune_code"])
    assert {"2A001", "01004", "75101", "97411", "UNKNOWN", "99999"} <= codes


def test_establishment_mart_sections_match_hand_written_expectations(paths):
    for row in read(paths, "fact_establishment_snapshot").to_dicts():
        assert row["naf_section"] == oracle.section(
            None if row["naf_nomenclature"] == "UNKNOWN" else row["naf_nomenclature"],
            None if row["naf_code"] == "UNKNOWN" else row["naf_code"]), row


def test_legal_unit_mart_matches_oracle(paths):
    mart = read(paths, "fact_legal_unit_snapshot")
    actual = {
        (r["naf_nomenclature"], r["naf_code"], r["legal_category_code"], r["enterprise_category"], r["status_code"]): r["legal_unit_count"]
        for r in mart.to_dicts()
    }
    assert actual == oracle.legal_unit_mart()
    assert mart["legal_unit_count"].sum() == 14


def test_events_match_oracle_exactly(paths):
    mart = read(paths, "fact_administrative_events")
    actual = {(r["event_year"], r["event_type"], r["naf_nomenclature"], r["naf_code"]): r["event_count"] for r in mart.to_dicts()}
    expected = oracle.events()
    assert actual == expected
    by_type = {}
    for (_, event_type, *_), count in expected.items():
        by_type[event_type] = by_type.get(event_type, 0) + count
    assert by_type == {"Cessation administrative": 8, "Fermeture administrative": 5, "Date de création enregistrée": 18}


def test_events_respect_window_boundaries_reactivations_and_flags(paths):
    events = read(paths, "fact_administrative_events")
    assert events["event_year"].min() == 2021 and events["event_year"].max() == 2025  # 2018 et 2026 exclus
    cessations = events.filter(pl.col("event_type") == "Cessation administrative")
    # S2 (456789123) : cessation 2021, RÉACTIVATION 2023 (non comptée), cessation 2024 → 2 événements 62.01Z
    assert cessations.filter(pl.col("naf_code") == "62.01Z")["event_count"].sum() == 2
    # un indicateur de changement sans passage à l'état C n'est jamais une cessation
    assert events.filter(pl.col("event_year") == 2023, pl.col("event_type") == "Cessation administrative")["event_count"].sum() == 1


def test_events_never_assign_rev2_sections_to_historical_nomenclatures(paths):
    events = read(paths, "fact_administrative_events")
    hist = events.filter(pl.col("naf_nomenclature").is_in(["NAFRev1", "NAF1993", "NAP", "UNKNOWN"]))
    assert hist.height >= 2 and hist["naf_section"].null_count() == hist.height


def test_marts_use_expected_dtypes(paths):
    assert read(paths, "fact_establishment_snapshot").schema["date_key"] == pl.Int32
    assert read(paths, "fact_establishment_snapshot").schema["establishment_count"] == pl.Int64
    assert read(paths, "fact_administrative_events").schema["event_year"] == pl.Int16


def test_string_and_date_source_formats_give_identical_marts(fixture_string, fixture_date):
    for name in ("fact_establishment_snapshot", "fact_legal_unit_snapshot", "fact_administrative_events"):
        a = pl.read_parquet(fixture_string.processed / f"{name}.parquet")
        b = pl.read_parquet(fixture_date.processed / f"{name}.parquet")
        assert a.sort(a.columns).equals(b.sort(b.columns)), name
