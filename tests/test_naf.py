from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.transformation.naf import (
    SECTION_LABELS,
    SECTION_RANGES,
    add_naf_section,
    section_for_division,
)

pytestmark = pytest.mark.layer("E-transformation")


def test_naf_section_mapping():
    frame = pl.DataFrame({"naf": ["01.11Z", "10.11Z", "47.11A", "99.00Z", None]})
    result = add_naf_section(frame.lazy(), "naf").collect()
    assert result["naf_section"].to_list() == ["A", "C", "G", "U", None]


def test_historical_nomenclatures_never_receive_a_rev2_section():
    """Régression de l'audit : '72.2C' (NAFRev1, informatique) recevait la section M de la NAF Rev.2."""
    frame = pl.DataFrame({
        "code": ["72.2C", "62.01Z", "72.19Z", "52.4A", "6301", "62.01Z", "62.01Z"],
        "nom": ["NAFRev1", "NAFRev2", "NAFRev2", "NAF1993", "NAP", "NAF2025", None],
    })
    result = add_naf_section(frame.lazy(), "code", nomenclature_column="nom").collect()
    assert result["naf_section"].to_list() == [None, "J", "M", None, None, None, None]
    assert result["is_naf_rev2"].to_list() == [False, True, True, False, False, False, False]


def test_malformed_rev2_codes_get_no_section():
    frame = pl.DataFrame({"code": ["4711A", "47.11", "47.1A", "[ND]", "47.11a", " 47.11A"], "nom": ["NAFRev2"] * 6})
    assert add_naf_section(frame.lazy(), "code", nomenclature_column="nom").collect()["naf_section"].null_count() == 6


def test_section_boundaries_cover_every_division_exactly_once():
    mapped = {d: section_for_division(d) for d in range(0, 100)}
    covered = [d for d, s in mapped.items() if s]
    assert sorted(covered) == sorted(
        d for low, high in SECTION_RANGES.values() for d in range(low, high + 1)
    )
    # les divisions 00 et 04 n'existent pas en NAF Rev.2
    assert section_for_division(4) is None and section_for_division(0) is None
    assert section_for_division(3) == "A" and section_for_division(5) == "B"
    assert section_for_division(97) == "T" and section_for_division(99) == "U"


def test_there_are_21_sections_with_labels():
    assert set(SECTION_RANGES) == set(SECTION_LABELS) and len(SECTION_LABELS) == 21


def test_unclassified_0000z_is_well_formed_but_has_no_section():
    frame = pl.DataFrame({"code": ["00.00Z"], "nom": ["NAFRev2"]})
    result = add_naf_section(frame.lazy(), "code", nomenclature_column="nom").collect()
    assert result["is_naf_rev2"].to_list() == [True]
    assert result["naf_section"].to_list() == [None]
