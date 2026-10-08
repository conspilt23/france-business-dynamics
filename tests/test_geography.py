from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.transformation.geo_labels import DEPARTMENT_LABELS, REGION_LABELS
from france_business_dynamics.transformation.geography import (
    GEO_STATUS_MATCHED,
    GEO_STATUS_NO_COMMUNE,
    GEO_STATUS_UNMATCHED,
    enrich_establishments_with_cog,
    read_cog_2026,
)
from france_business_dynamics.utils.errors import PreconditionError, SchemaError
from tests.support import oracle
from tests.support.oracle import FIXTURES

pytestmark = pytest.mark.layer("D-geography")

HEADER = "TYPECOM,COM,REG,DEP,CTCD,ARR,TNCC,NCC,NCCENR,LIBELLE,CAN,COMPARENT\n"


def test_cog_reads_official_commune_schema_and_preserves_codes(tmp_path):
    path = tmp_path / "v_commune_2026.csv"
    path.write_text(
        HEADER
        + "COM,01004,84,01,01,01,0,AMBERIEU EN BUGEY,Ambérieu-en-Bugey,Ambérieu-en-Bugey,01,\n"
        + "COM,75056,11,75,75,75,0,PARIS,Paris,Paris,75,\n"
        + "COM,2A001,94,2A,2A,2A,0,AFA,Afa,Afa,2A,\n",
        encoding="utf-8",
    )
    result = read_cog_2026(path).collect()
    assert result.columns == ["type_commune", "code_commune", "code_departement", "code_region", "commune"]
    assert result.filter(pl.col("code_commune") == "01004").height == 1
    assert result.filter(pl.col("code_commune") == "01004")["code_departement"][0] == "01"
    assert result.filter(pl.col("code_commune") == "2A001")["code_departement"][0] == "2A"


def test_all_geographic_codes_are_strings():
    cog = read_cog_2026(FIXTURES / "cog_communes_sample.csv").collect()
    assert all(dtype == pl.String for dtype in cog.schema.values())


def test_leading_zeros_and_alphanumeric_codes_survive():
    cog = read_cog_2026(FIXTURES / "cog_communes_sample.csv").collect().to_dicts()
    by_code = {row["code_commune"]: row for row in cog}
    assert "01004" in by_code and "2A001" in by_code
    assert by_code["97411"]["code_region"] == "04"  # zéro initial de région
    assert by_code["97411"]["code_departement"] == "974"
    assert by_code["2A001"]["code_departement"] == "2A"


def test_cog_matches_independent_oracle_including_parent_inheritance():
    expected = oracle.cog()
    actual = {r["code_commune"]: r for r in read_cog_2026(FIXTURES / "cog_communes_sample.csv").collect().to_dicts()}
    assert set(actual) == set(expected)
    for code, want in expected.items():
        assert (actual[code]["commune"], actual[code]["code_departement"], actual[code]["code_region"]) == (
            want["name"], want["dep"], want["reg"]), code
    # cas explicites : héritage depuis COMPARENT et priorité COM > COMA
    assert (actual["01999"]["code_departement"], actual["01999"]["code_region"]) == ("01", "84")
    assert actual["01004"]["commune"] == "Ambérieu-en-Bugey" and actual["01004"]["type_commune"] == "COM"


def test_cog_with_utf8_bom_is_read(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(("\ufeff" + HEADER + "COM,01004,84,01,x,x,0,A,A,Ambérieu,x,\n").encode("utf-8"))
    assert read_cog_2026(path).collect()["code_commune"].to_list() == ["01004"]


def test_cog_missing_required_columns_names_them(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("COM,LIBELLE\n01004,X\n", encoding="utf-8")
    with pytest.raises(SchemaError) as err:
        read_cog_2026(path)
    assert "TYPECOM" in err.value.message and "DEP" in err.value.message


def test_cog_missing_file_gives_download_hint(tmp_path):
    with pytest.raises(PreconditionError) as err:
        read_cog_2026(tmp_path / "nope.csv")
    assert "download_sources" in err.value.hint


def test_enrichment_assigns_geo_status_without_losing_rows():
    cog = read_cog_2026(FIXTURES / "cog_communes_sample.csv")
    frame = pl.LazyFrame({"codeCommuneEtablissement": ["01004", "99999", None, "2A001"]})
    result = enrich_establishments_with_cog(frame, cog).collect()
    assert result.height == 4
    assert result.sort("codeCommuneEtablissement", nulls_last=True)["geo_status"].to_list() == [
        GEO_STATUS_MATCHED, GEO_STATUS_MATCHED, GEO_STATUS_UNMATCHED, GEO_STATUS_NO_COMMUNE]


def test_static_labels_cover_every_code_of_the_cog_fixture():
    cog = read_cog_2026(FIXTURES / "cog_communes_sample.csv").collect()
    assert set(cog["code_departement"].drop_nulls()) <= set(DEPARTMENT_LABELS)
    assert set(cog["code_region"].drop_nulls()) <= set(REGION_LABELS)


def test_static_label_tables_are_complete_and_well_formed():
    assert len(REGION_LABELS) == 18  # 13 régions métropolitaines (dont Corse) + 5 d'outre-mer
    assert len(DEPARTMENT_LABELS) == 101  # 96 métropolitains (2A/2B) + 5 d'outre-mer
    assert all(len(code) in (2, 3) for code in {*REGION_LABELS, *DEPARTMENT_LABELS})
    assert {"2A", "2B", "971", "976"} <= set(DEPARTMENT_LABELS)
    assert all(label.strip() == label and label for label in {*REGION_LABELS.values(), *DEPARTMENT_LABELS.values()})
