from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.transformation.workforce import (
    WORKFORCE_LABELS,
    WORKFORCE_SORT_ORDER,
    add_workforce_label,
)

pytestmark = pytest.mark.layer("E-transformation")


def test_workforce_labels():
    frame = pl.DataFrame({"code": ["NN", "01", "31", "53", "XX"]})
    result = add_workforce_label(frame.lazy(), "code").collect()
    assert result["workforce_label"].to_list() == [
        "Non employeur",
        "1–2 salariés",
        "200–249 salariés",
        "10 000 salariés et plus",
        None,
    ]


def test_all_sixteen_official_bands_exist_with_unique_labels_and_sort_order():
    assert len(WORKFORCE_LABELS) == 16
    assert len(set(WORKFORCE_LABELS.values())) == 16
    codes = list(WORKFORCE_LABELS)
    assert [WORKFORCE_SORT_ORDER[c] for c in codes] == sorted(WORKFORCE_SORT_ORDER[c] for c in codes)
    assert WORKFORCE_SORT_ORDER["UNKNOWN"] == 99


def test_null_code_gives_null_label():
    result = add_workforce_label(pl.LazyFrame({"c": [None, "NN"]}, schema={"c": pl.String}), "c").collect()
    assert result["workforce_label"].to_list() == [None, "Non employeur"]
