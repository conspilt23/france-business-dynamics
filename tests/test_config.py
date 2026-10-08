from __future__ import annotations

import pytest

from france_business_dynamics.utils.config import (
    ANALYSIS_YEARS,
    SOURCE_FILES,
    SOURCES,
    ProjectPaths,
    parse_snapshot_date,
    snapshot_date_key,
)

pytestmark = pytest.mark.layer("B-sources")


def test_analysis_years_are_complete():
    assert ANALYSIS_YEARS == (2021, 2022, 2023, 2024, 2025)


def test_all_four_sirene_sources_are_configured():
    keys = {key for key in SOURCES if key.startswith("stock_")}
    assert keys == {
        "stock_unite_legale",
        "stock_etablissement",
        "stock_unite_legale_historique",
        "stock_etablissement_historique",
    }


def test_source_filenames_are_unique():
    filenames = [source.filename for source in SOURCE_FILES.values()]
    assert len(filenames) == len(set(filenames))


def test_cog_source_targets_commune_reference():
    source = SOURCE_FILES["cog_communes_2026"]
    assert source.filename == "v_commune_2026.csv"
    assert source.stable_url.endswith("/v_commune_2026.csv")


def test_official_urls_are_the_documented_ones():
    assert SOURCES["stock_unite_legale"].endswith("350182c9-148a-46e0-8389-76c2ec1374a3")
    assert SOURCES["stock_etablissement"].endswith("a29c1297-1f92-4e2a-8f6b-8c902ce96c5f")
    assert SOURCES["stock_unite_legale_historique"].endswith("1b9290ed-d0bc-461f-ba31-0250a99cc140")
    assert SOURCES["stock_etablissement_historique"].endswith("2b3a0c79-f97b-46b8-ac02-8be6c1f01a8c")
    assert all(url.startswith("https://") for url in SOURCES.values())


def test_paths_are_relocatable_through_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FBD_DATA_DIR", str(tmp_path / "elsewhere"))
    paths = ProjectPaths.from_env()
    assert paths.raw == (tmp_path / "elsewhere").resolve() / "raw"
    assert SOURCE_FILES["cog_communes_2026"].local_path(paths).parent == paths.reference


def test_snapshot_date_key_is_yyyymmdd_and_rejects_garbage():
    assert snapshot_date_key("2026-10-01") == 20261001
    with pytest.raises(ValueError, match="AAAA-MM-JJ"):
        parse_snapshot_date("01/10/2026")
