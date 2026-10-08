from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.sql_contract import (
    ALL_STAGING,
    BY_NAME,
    Column,
    check_live_schema,
    check_summary,
    reference_frames,
    summarize,
)

pytestmark = pytest.mark.layer("G-sql")
EST = BY_NAME["EstablishmentSnapshot"]


def valid_frame(**overrides) -> pl.LazyFrame:
    base = {
        "date_key": [20261001], "commune_code": ["2A001"], "commune_name": ["Afa"], "department_code": ["2A"],
        "region_code": ["94"], "geo_status": ["MATCHED"], "naf_nomenclature": ["NAFRev2"], "naf_code": ["47.11A"],
        "naf_section": ["G"], "workforce_code": ["NN"], "workforce_label": ["Non employeur"], "status_code": ["A"],
        "establishment_count": [3],
    }
    base.update(overrides)
    return pl.LazyFrame(base)


def problems(frame: pl.LazyFrame, contract=EST):
    return check_summary(summarize(frame, contract), contract)


def kinds(found) -> set[tuple]:
    return {(p.column, p.kind) for p in found}


def test_valid_frame_has_no_problem():
    assert problems(valid_frame()) == []


def test_every_table_has_unique_columns_and_positive_widths():
    for table in ALL_STAGING:
        assert len(set(table.column_names)) == len(table.columns)
        assert all((c.length or 1) > 0 for c in table.columns)
        assert all(c.length for c in table.columns if c.is_string)  # jamais de varchar sans largeur explicite


def test_width_null_type_ascii_and_range_problems_are_each_detected():
    assert ("naf_code", "width") in kinds(problems(valid_frame(naf_code=["X" * 21])))
    assert ("commune_name", "width") in kinds(problems(valid_frame(commune_name=["é" * 201])))
    assert ("commune_name", "null") in kinds(problems(valid_frame(commune_name=[None], ), ))
    assert ("commune_code", "non_ascii") in kinds(problems(valid_frame(commune_code=["2Ä001"])))
    assert ("commune_name", "non_ascii") not in kinds(problems(valid_frame(commune_name=["Ambérieu"])))  # nvarchar : OK
    assert ("establishment_count", "type") in kinds(problems(valid_frame(establishment_count=["3"])))
    assert ("date_key", "range") in kinds(problems(valid_frame(date_key=[2**31])))


def test_nullable_column_accepts_nulls_even_when_entirely_null():
    assert problems(valid_frame(naf_section=[None])) == []  # dtype Null : sans risque


def test_missing_and_unexpected_columns_are_reported():
    frame = valid_frame().drop("naf_code").with_columns(pl.lit(1).alias("extra"))
    assert {("naf_code", "missing_column"), ("extra", "unexpected_column")} <= kinds(problems(frame))


def test_width_is_counted_in_characters_for_nvarchar():
    assert problems(valid_frame(commune_name=["é" * 200])) == []  # 200 caractères, 400 octets : valide en nvarchar(200)


def test_live_schema_accepts_wider_columns_and_max_but_rejects_narrow_or_retyped():
    live = [(c.name, c.sql_type, (c.length or 0) + 5 if c.is_string else None, "YES" if c.nullable else "NO") for c in EST.columns]
    assert check_live_schema(EST, live) == []
    as_max = [(n, t, -1 if t in ("varchar", "nvarchar") else l, nl) for n, t, l, nl in live]
    assert check_live_schema(EST, as_max) == []
    retyped = [(n, "int" if n == "commune_code" else t, l, nl) for n, t, l, nl in live]
    assert ("commune_code", "type") in kinds(check_live_schema(EST, retyped))
    flipped = [(n, t, l, "YES" if n == "date_key" else nl) for n, t, l, nl in live]
    assert ("date_key", "nullability") in kinds(check_live_schema(EST, flipped))
    assert check_live_schema(EST, [])[0].kind == "missing_table"
    extra = live + [("legacy_col", "int", None, "YES")]
    assert ("legacy_col", "unexpected_column") in kinds(check_live_schema(EST, extra))


def test_column_ddl_rendering():
    assert Column("a", "varchar", 10).ddl() == "a varchar(10) NOT NULL"
    assert Column("b", "char", 1, True).ddl() == "b char(1) NULL"
    assert Column("c", "bigint").ddl() == "c bigint NOT NULL"


def test_reference_frames_match_their_contracts():
    frames = reference_frames()
    for table in ALL_STAGING:
        if table.reference_loader:
            frame = frames[table.reference_loader]
            assert frame.columns == table.column_names and frame.height > 0
            assert check_summary(summarize(frame.lazy(), table), table) == []
