"""Couche E — profilage et requêtes analytiques, vérifiés contre l'oracle indépendant des fixtures."""

from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.analysis import queries as q
from france_business_dynamics.utils.config import SOURCE_FILES
from france_business_dynamics.validation import profile
from tests.support import oracle

pytestmark = pytest.mark.layer("E-transformation")


@pytest.fixture
def marts(fixture_string):
    return q.load_marts(fixture_string)


@pytest.fixture
def raw_et(fixture_string):
    return pl.scan_parquet(SOURCE_FILES["stock_etablissement"].local_path(fixture_string))


# --- profil ----------------------------------------------------------------------------------------
def test_parquet_overview_reads_only_metadata(fixture_string):
    info = profile.parquet_overview(SOURCE_FILES["stock_etablissement"].local_path(fixture_string))
    assert info["rows"] == len(oracle.rows("etablissement_sample.csv")) and len(info["columns"]) == 54


def test_schema_comparison_flags_missing_columns(raw_et):
    result = profile.schema_comparison(raw_et, ["siret", "colonne_inexistante"])
    assert result["statut"].to_list() == ["présente", "MANQUANTE"]


def test_null_profile_counts_nulls_and_blanks_and_sorts(raw_et):
    result = profile.null_profile(raw_et, ["siret", "codeCommuneEtablissement", "trancheEffectifsEtablissement"])
    by_col = {r["colonne"]: r for r in result.to_dicts()}
    expected_null = sum(1 for r in oracle.rows("etablissement_sample.csv") if not r["codeCommuneEtablissement"])
    assert by_col["codeCommuneEtablissement"]["nulls"] == expected_null
    assert by_col["siret"]["nulls"] == 0
    assert result["pct_manquant"].to_list() == sorted(result["pct_manquant"].to_list(), reverse=True)


def test_duplicate_profile_and_distribution(raw_et):
    assert profile.duplicate_profile(raw_et, "siret")["excess_rows"] == 0
    dist = profile.value_distribution(raw_et, "etatAdministratifEtablissement")
    counts = {r["etatAdministratifEtablissement"]: r["lignes"] for r in dist.to_dicts()}
    expected = {}
    for r in oracle.rows("etablissement_sample.csv"):
        expected[r["etatAdministratifEtablissement"]] = expected.get(r["etatAdministratifEtablissement"], 0) + 1
    assert counts == expected and abs(dist["pct"].sum() - 100) < 0.01


def test_date_profile_counts_invalid_without_dropping(raw_et):
    result = profile.date_profile(raw_et, "dateCreationEtablissement")
    assert result["invalid"] == 1 and result["absent"] == 1 and str(result["min"]) == "1988-01-01"


def test_geography_profile_detects_corsica_leading_zeros_and_missing(raw_et):
    result = profile.geography_profile(raw_et)
    rows = oracle.rows("etablissement_sample.csv")
    assert result["corse_2a"] == 1 and result["corse_2b"] == 0
    assert result["no_commune"] == sum(1 for r in rows if not r["codeCommuneEtablissement"])
    assert result["leading_zero"] == sum(1 for r in rows if (r["codeCommuneEtablissement"] or "").startswith("0"))
    assert result["bad_format"] == 0


def test_sample_rows_shows_hard_cases(raw_et):
    sample = profile.sample_rows(raw_et, pl.col("nomenclatureActivitePrincipaleEtablissement") == "NAFRev1",
                                 columns=["siret", "activitePrincipaleEtablissement"])
    assert sample.height == 1 and sample["activitePrincipaleEtablissement"][0] == "52.1B"


# --- requêtes analytiques ----------------------------------------------------------------------------
def test_every_breakdown_reconciles_with_the_mart_total(marts):
    total = marts["establishments"].select(pl.col("establishment_count").sum()).collect().item()
    for frame in (q.establishments_by_region(marts["establishments"], None),
                  q.establishments_by_naf_section(marts["establishments"], None),
                  q.establishments_by_workforce(marts["establishments"], None),
                  q.establishments_by_department(marts["establishments"], None, top_n=None)):
        assert frame["etablissements"].sum() == total == 26
    assert q.geography_coverage(marts["establishments"])["pct"].sum() == pytest.approx(100, abs=0.01)


def test_region_labels_and_unknown_bucket(marts):
    regions = {r["region"]: r["etablissements"] for r in q.establishments_by_region(marts["establishments"], None).to_dicts()}
    assert regions["Corse"] == 1 and regions["La Réunion"] == 1 and regions["Île-de-France"] == 1
    assert regions["Non renseigné"] == 2


def test_active_filter_excludes_closed_establishments(marts):
    active = q.establishments_by_region(marts["establishments"], "A")["etablissements"].sum()
    closed = q.establishments_by_region(marts["establishments"], "F")["etablissements"].sum()
    assert closed == sum(1 for r in oracle.rows("etablissement_sample.csv") if r["etatAdministratifEtablissement"] == "F")
    assert active + closed == 26


def test_naf_sections_isolate_non_rev2_and_cover_expected_letters(marts):
    sections = {r["section"]: r["etablissements"] for r in q.establishments_by_naf_section(marts["establishments"], None).to_dicts()}
    assert sections["—"] == 5 and {"A", "G", "I", "J", "M", "U"} <= set(sections)
    nomen = {r["naf_nomenclature"]: r["pct"] for r in q.naf_nomenclature_coverage(marts["establishments"], None).to_dicts()}
    assert {"NAFRev2", "NAFRev1", "NAF1993", "NAP", "UNKNOWN"} == set(nomen)


def test_workforce_is_ordered_by_size_not_alphabetically(marts):
    frame = q.establishments_by_workforce(marts["establishments"], None)
    assert frame["workforce_code"].to_list()[0] == "NN" and frame["workforce_code"].to_list()[-1] == "UNKNOWN"
    assert frame["ordre"].to_list() == sorted(frame["ordre"].to_list())


def test_events_by_year_matches_oracle_and_never_sums_types(marts):
    wide = q.events_by_year(marts["events"])
    expected: dict = {}
    for (year, event_type, *_), count in oracle.events().items():
        expected[(year, event_type)] = expected.get((year, event_type), 0) + count
    for row in wide.to_dicts():
        for event_type in ("Date de création enregistrée", "Fermeture administrative", "Cessation administrative"):
            assert row[event_type] == expected.get((row["event_year"], event_type), 0)
    assert "total" not in " ".join(wide.columns).lower()  # aucune colonne de somme trompeuse


def test_legal_unit_breakdowns(marts):
    by_cat = {r["categorie_juridique"]: r["unites_legales"] for r in q.legal_units_by_legal_category(marts["legal_units"], None).to_dicts()}
    assert by_cat["Société commerciale"] > by_cat["Entrepreneur individuel"] and sum(by_cat.values()) == 14
    enterprise = {r["categorie"]: r["unites_legales"] for r in q.legal_units_by_enterprise_category(marts["legal_units"], None).to_dicts()}
    assert enterprise["Grande entreprise"] == 1 and enterprise["Non renseigné"] == 2
    assert q.legal_units_by_section(marts["legal_units"], None)["unites_legales"].sum() == 14


def test_headline_figures_and_top_communes(marts):
    figures = q.headline_figures(marts)
    assert figures["etablissements_total"] == 26 and figures["unites_legales_total"] == 14
    assert figures["etablissements_actifs"] + figures["etablissements_fermes"] == 26
    assert q.top_communes(marts["establishments"], None, top_n=1)["commune_code"][0] == "01004"


def test_missing_marts_are_omitted_not_fatal(tmp_path):
    from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths

    assert q.load_marts(ProjectPaths(root=PROJECT_ROOT, data=tmp_path)) == {}
