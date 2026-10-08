from __future__ import annotations

import json

import polars as pl
import pytest

from tests.support.fixture_data import (
    ETABLISSEMENT_FULL,
    ETABLISSEMENT_HIST_FULL,
    UNITE_LEGALE_FULL,
    UNITE_LEGALE_HIST_FULL,
    build_test_inputs,
)
from france_business_dynamics.utils.config import PROJECT_ROOT, SOURCE_FILES, ProjectPaths
from france_business_dynamics.utils.errors import PreconditionError, SchemaError
from france_business_dynamics.validation.schemas import (
    ETABLISSEMENT_COLUMNS,
    ETABLISSEMENT_HISTORICAL_COLUMNS,
    UNIT_LEGALE_HISTORICAL_COLUMNS,
    UNITE_LEGALE_COLUMNS,
    require_columns,
)

pytestmark = pytest.mark.layer("C-schema")

SNAPSHOT = json.loads((PROJECT_ROOT / "docs" / "schema_snapshot_2026-10-01.json").read_text(encoding="utf-8"))["datasets"]


def test_fixture_schemas_are_identical_to_the_real_versioned_snapshot():
    """Les fixtures reproduisent EXACTEMENT les 35 / 54 colonnes réelles inspectées sur data.gouv.fr."""
    assert UNITE_LEGALE_FULL == SNAPSHOT["stock_unite_legale"]["column_names"]
    assert ETABLISSEMENT_FULL == SNAPSHOT["stock_etablissement"]["column_names"]
    assert len(UNITE_LEGALE_FULL) == SNAPSHOT["stock_unite_legale"]["columns"] == 35
    assert len(ETABLISSEMENT_FULL) == SNAPSHOT["stock_etablissement"]["columns"] == 54


def test_historical_fixture_schemas_have_the_real_column_counts():
    # Le snapshot versionné ne contient que le NOMBRE de colonnes des historiques (pas leurs noms) :
    # les noms utilisés par les fixtures ne sont donc PAS vérifiés contre les fichiers réels.
    # `require_columns` le détecte à la première exécution réelle (message clair, rien n'est écrit).
    assert len(UNITE_LEGALE_HIST_FULL) == SNAPSHOT["stock_unite_legale_historique"]["columns"] == 28
    assert len(ETABLISSEMENT_HIST_FULL) == SNAPSHOT["stock_etablissement_historique"]["columns"] == 18


def test_snapshot_row_counts_are_the_documented_ones():
    assert SNAPSHOT["stock_unite_legale"]["rows"] == 30_148_401
    assert SNAPSHOT["stock_etablissement"]["rows"] == 44_282_364
    assert SNAPSHOT["stock_unite_legale_historique"]["rows"] == 72_004_236
    assert SNAPSHOT["stock_etablissement_historique"]["rows"] == 96_657_561


@pytest.mark.parametrize(
    ("selected", "full"),
    [
        (UNITE_LEGALE_COLUMNS, UNITE_LEGALE_FULL),
        (ETABLISSEMENT_COLUMNS, ETABLISSEMENT_FULL),
        (UNIT_LEGALE_HISTORICAL_COLUMNS, UNITE_LEGALE_HIST_FULL),
        (ETABLISSEMENT_HISTORICAL_COLUMNS, ETABLISSEMENT_HIST_FULL),
    ],
)
def test_selected_columns_exist_in_the_full_schema_without_duplicates(selected, full):
    assert set(selected) <= set(full)
    assert len(selected) == len(set(selected))
    assert len(selected) < len(full)  # aucune colonne « au cas où » : on ne garde que le nécessaire


def test_required_identifiers_are_always_selected():
    assert {"siren", "nic", "siret"} <= set(ETABLISSEMENT_COLUMNS)
    assert "siren" in UNITE_LEGALE_COLUMNS


def test_require_columns_passes_on_fixture_inputs(tmp_path):
    paths = ProjectPaths(root=PROJECT_ROOT, data=tmp_path)
    build_test_inputs(paths)
    require_columns(SOURCE_FILES["stock_etablissement"].local_path(paths), ETABLISSEMENT_COLUMNS, dataset="X")


def test_require_columns_names_the_missing_columns(tmp_path):
    path = tmp_path / "x.parquet"
    pl.DataFrame({"siren": ["1"]}).write_parquet(path)
    with pytest.raises(SchemaError) as err:
        require_columns(path, ["siren", "nic", "siret"], dataset="StockEtablissement")
    assert "nic" in err.value.message and "siret" in err.value.message
    assert "inspect_schema" in err.value.hint


def test_require_columns_missing_file_is_a_precondition_error(tmp_path):
    with pytest.raises(PreconditionError):
        require_columns(tmp_path / "absent.parquet", ["siren"], dataset="X")
