"""Couche E — indicateurs `changement…` des historiques : toutes les codifications plausibles sont acceptées."""

from __future__ import annotations

import polars as pl
import pytest

from france_business_dynamics.transformation.historical import (
    _truthy_change,
    administrative_establishment_events,
    administrative_unit_legal_events,
)

pytestmark = pytest.mark.layer("E-transformation")


@pytest.mark.parametrize(
    ("values", "dtype", "expected"),
    [
        ([True, False, None], pl.Boolean, [True, False, False]),
        (["true", "false", "True", " TRUE ", None, ""], pl.String, [True, False, True, True, False, False]),
        (["O", "N", "o", "oui", "1", "0"], pl.String, [True, False, True, True, True, False]),
    ],
)
def test_change_flag_accepts_boolean_and_text_encodings(values, dtype, expected):
    frame = pl.DataFrame({"flag": pl.Series(values, dtype=dtype)})
    assert frame.select(_truthy_change("flag")).to_series().to_list() == expected


def history(flag_values, flag_dtype) -> pl.LazyFrame:
    n = len(flag_values)
    return pl.LazyFrame({
        "siren": [str(i) for i in range(n)],
        "dateDebut": ["2022-05-01"] * n,
        "etatAdministratifUniteLegale": ["C"] * n,
        "changementEtatAdministratifUniteLegale": pl.Series(flag_values, dtype=flag_dtype),
        "activitePrincipaleUniteLegale": ["47.11A"] * n,
        "nomenclatureActivitePrincipaleUniteLegale": ["NAFRev2"] * n,
    })


@pytest.mark.parametrize(("values", "dtype"), [
    ([True, True, False, None], pl.Boolean),
    (["true", "true", "false", None], pl.String),
    (["O", "O", "N", None], pl.String),
])
def test_events_count_only_rows_with_a_true_change_flag_whatever_the_encoding(values, dtype):
    events = administrative_unit_legal_events(history(values, dtype), 2021, 2025).collect()
    assert events["event_count"].sum() == 2 and events["event_year"].to_list() == [2022]


def test_establishment_events_use_the_closed_state_f():
    frame = pl.LazyFrame({
        "dateDebut": ["2023-01-01", "2023-01-01"], "etatAdministratifEtablissement": ["F", "A"],
        "changementEtatAdministratifEtablissement": [True, True],
        "activitePrincipaleEtablissement": ["56.10A", "56.10A"], "nomenclatureActivitePrincipaleEtablissement": ["NAFRev2"] * 2,
    })
    events = administrative_establishment_events(frame, 2021, 2025).collect()
    assert events["event_count"].sum() == 1 and events["event_type"].to_list() == ["Fermeture administrative"]
