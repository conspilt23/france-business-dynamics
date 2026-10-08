from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.sql_contract import BY_NAME
from france_business_dynamics.transformation.marts import ESTABLISHMENT_GRAIN
from france_business_dynamics.validation.quality import (
    check_establishment_curated,
    check_mart,
    count_check,
    duplicate_key_count,
    make_check,
    rate_check,
)
from france_business_dynamics.validation.quality import assert_unique_key

pytestmark = pytest.mark.layer("F-quality")


def test_unique_key_passes():
    frame = pl.DataFrame({"id": [1, 2, 3]}).lazy()
    assert_unique_key(frame, ["id"])


def test_unique_key_fails():
    frame = pl.DataFrame({"id": [1, 1, 2]}).lazy()
    with pytest.raises(ValueError):
        assert_unique_key(frame, ["id"])


def test_duplicate_key_count_simple_and_composite():
    frame = pl.LazyFrame({"a": [1, 1, 2, 2, 2], "b": ["x", "x", "y", "z", "y"]})
    assert duplicate_key_count(frame, ["a"]) == 3
    assert duplicate_key_count(frame, ["a", "b"]) == 2


def test_partitioned_uniqueness_check_gives_exactly_the_same_answer_as_a_single_pass():
    """Mémoire bornée : le comptage par paquets de hachage ne doit jamais différer de la passe unique."""
    import random

    rng = random.Random(7)
    keys = [f"{rng.randrange(0, 4000):05d}" for _ in range(10_000)]  # beaucoup de doublons
    frame = pl.LazyFrame({"k": keys, "g": [i % 3 for i in range(10_000)]})
    single = duplicate_key_count(frame, ["k"], max_rows_per_pass=10**9)
    assert single == 10_000 - len(set(keys))
    for per_pass in (10_000, 4_999, 1_000, 97):
        assert duplicate_key_count(frame, ["k"], max_rows_per_pass=per_pass) == single, per_pass
    composite = duplicate_key_count(frame, ["k", "g"], max_rows_per_pass=10**9)
    assert duplicate_key_count(frame, ["k", "g"], max_rows_per_pass=700) == composite


def test_partitioned_uniqueness_on_unique_and_empty_inputs():
    assert duplicate_key_count(pl.LazyFrame({"k": [str(i) for i in range(500)]}), ["k"], max_rows_per_pass=50) == 0
    assert duplicate_key_count(pl.LazyFrame({"k": []}, schema={"k": pl.String}), ["k"]) == 0


def test_check_helpers_statuses_and_severity():
    assert count_check("c", "d", 0).status == "PASS"
    assert count_check("c", "d", 3).status == "FAIL"
    assert count_check("c", "d", 3, severity="warning").status == "WARN"
    assert rate_check("r", "d", 1, 1000, 0.01).status == "PASS"
    assert rate_check("r", "d", 50, 1000, 0.01).status == "WARN"
    assert make_check("x", "d", False, severity="error", observed=1, expected="0").status == "FAIL"


# --- défauts plantés : le gate doit les DÉTECTER -------------------------------------------------
@pytest.fixture
def curated(fixture_string) -> pl.DataFrame:
    return pl.read_parquet(fixture_string.processed / "etablissement_current.parquet")


def statuses(frame: pl.DataFrame) -> dict[str, str]:
    _, checks = check_establishment_curated(frame.lazy())
    return {c.id: c.status for c in checks}


def test_clean_curated_layer_has_no_error(curated):
    assert "FAIL" not in statuses(curated).values()


def test_duplicate_siret_is_an_error(curated):
    assert statuses(pl.concat([curated, curated.head(1)]))["etablissement.siret_unique"] == "FAIL"


def test_malformed_identifiers_are_errors(curated):
    bad = curated.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("123")).otherwise(pl.col("siret")).alias("siret"))
    result = statuses(bad)
    assert result["etablissement.siret_format"] == "FAIL"
    assert result["etablissement.siret_consistency"] == "FAIL"


def test_unexpected_state_is_an_error(curated):
    bad = curated.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("X")).otherwise(pl.col("etatAdministratifEtablissement"))
        .alias("etatAdministratifEtablissement"))
    assert statuses(bad)["etablissement.etat_values"] == "FAIL"


def test_rev2_section_on_a_historical_nomenclature_is_an_error(curated):
    bad = curated.with_columns(
        pl.when(pl.col("naf_nomenclature") == "NAFRev1").then(pl.lit("M")).otherwise(pl.col("naf_section"))
        .alias("naf_section"))
    result = statuses(bad)
    assert result["etablissement.no_section_outside_rev2"] == "FAIL"
    assert result["etablissement.naf_section_consistency"] == "FAIL"


def test_sentinel_leaking_into_curated_layer_is_an_error(curated):
    bad = curated.with_columns(pl.lit("UNKNOWN").alias("codeCommuneEtablissement"))
    assert statuses(bad)["etablissement.no_sentinel"] == "FAIL"


def test_missing_columns_are_reported_not_crashed(curated):
    _, checks = check_establishment_curated(curated.drop("geo_status").lazy())
    assert checks[0].status == "FAIL" and "geo_status" in checks[0].observed


def test_unmatched_geography_and_bad_dates_are_warnings_not_errors(curated):
    result = statuses(curated)
    assert result["etablissement.geo_unmatched"] == "WARN"
    assert result["etablissement.date_creation"] == "WARN"
    assert result["etablissement.rev2_code_format"] == "WARN"


def test_implausible_creation_dates_are_warnings(curated):
    from datetime import date

    idx = pl.int_range(pl.len())
    bad = curated.with_columns(
        pl.when(idx == 0).then(pl.lit(date(1899, 12, 31)))
        .when(idx == 1).then(pl.lit(date(2099, 1, 1)))
        .otherwise(pl.col("dateCreationEtablissement"))
        .alias("dateCreationEtablissement")
    )
    result = statuses(bad)
    assert result["etablissement.date_creation_before_1900"] == "WARN"
    assert result["etablissement.date_creation_after_snapshot"] == "WARN"


# --- marts -------------------------------------------------------------------------------------
@pytest.fixture
def mart(fixture_string) -> pl.DataFrame:
    return pl.read_parquet(fixture_string.processed / "fact_establishment_snapshot.parquet")


def run_mart(frame: pl.DataFrame, expected_total: int | None = None):
    return check_mart(
        frame.lazy(), prefix="m", contract=BY_NAME["EstablishmentSnapshot"], grain=ESTABLISHMENT_GRAIN,
        count_column="establishment_count", status_values=["A", "F", "U"], expected_total=expected_total,
        expected_total_label="curated")[2]


def by_id(checks):
    return {c.id: c for c in checks}


def test_clean_mart_passes_reconciliation_and_contract(mart):
    result = by_id(run_mart(mart, expected_total=int(mart["establishment_count"].sum())))
    assert all(c.status == "PASS" for c in result.values()), {k: c.observed for k, c in result.items() if c.status != "PASS"}


def test_reconciliation_detects_lost_rows(mart):
    result = by_id(run_mart(mart.head(mart.height - 1), expected_total=int(mart["establishment_count"].sum())))
    assert result["m.reconciliation"].status == "FAIL"


def test_duplicate_grain_and_non_positive_counts_are_errors(mart):
    result = by_id(run_mart(pl.concat([mart, mart.head(1).with_columns(pl.lit(0, dtype=pl.Int64).alias("establishment_count"))])))
    assert result["m.grain_unique"].status == "FAIL"
    assert result["m.positive_counts"].status == "FAIL"


def test_contract_check_catches_a_value_wider_than_the_sql_column(mart):
    """Reproduit la classe d'erreur « right truncation » AVANT toute insertion."""
    wide = mart.with_columns(
        pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("X" * 25)).otherwise(pl.col("naf_nomenclature")).alias("naf_nomenclature"))
    check = by_id(run_mart(wide))["m.sql_contract"]
    assert check.status == "FAIL"
    assert any("naf_nomenclature" in line and "25" in line for line in check.observed)


def test_contract_check_catches_missing_unexpected_and_null_columns(mart):
    result = by_id(run_mart(mart.drop("naf_code").with_columns(pl.lit(1).alias("surprise"))))
    # une colonne manquante empêche aussi les contrôles de grain : l'erreur est reportée proprement
    assert result["m.columns"].status == "FAIL"
    nulls = by_id(run_mart(mart.with_columns(pl.lit(None, dtype=pl.String).alias("commune_name"))))
    assert nulls["m.sql_contract"].status == "FAIL"
