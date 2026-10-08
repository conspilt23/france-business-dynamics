"""Couches H/I — notebooks : JSON valide, structure attendue, aucune logique ETL cachée, exécution RÉELLE sur fixtures."""

from __future__ import annotations

import json
import re

import nbformat
import pytest

from france_business_dynamics.utils.config import PROJECT_ROOT

NOTEBOOKS = ["01_source_profiling", "02_business_analysis", "03_quality_control"]
DIR = PROJECT_ROOT / "notebooks"


def source(name: str) -> str:
    nb = nbformat.read(DIR / f"{name}.ipynb", as_version=4)
    return "\n".join(cell.source for cell in nb.cells if cell.cell_type == "code")


@pytest.mark.layer("H-artifacts")
@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_is_valid_nbformat_and_has_no_stored_outputs(name):
    nb = nbformat.read(DIR / f"{name}.ipynb", as_version=4)
    nbformat.validate(nb)
    assert json.loads((DIR / f"{name}.ipynb").read_text(encoding="utf-8"))["nbformat"] == 4
    assert all(not cell.get("outputs") for cell in nb.cells if cell.cell_type == "code")  # pas de résultats périmés committés
    assert any(cell.cell_type == "markdown" for cell in nb.cells)


@pytest.mark.layer("H-artifacts")
@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebooks_contain_no_production_logic_or_hacks(name):
    code = source(name)
    for forbidden in ("sys.path", "sink_parquet", "write_parquet", "to_sql", "pyodbc", ".write_csv", "requests.get"):
        assert forbidden not in code, f"{name} : « {forbidden} » interdit (la logique de production vit dans src/)"
    assert "france_business_dynamics" in code  # le notebook s'appuie sur le paquet


@pytest.mark.layer("H-artifacts")
def test_notebook_02_refuses_unvalidated_marts_and_labels_flows():
    code = source("02_business_analysis")
    assert 'report["status"] != "PASS"' in code
    nb = (DIR / "02_business_analysis.ipynb").read_text(encoding="utf-8")
    assert "FLUX administratifs, pas un stock reconstruit" in nb
    assert not re.search(r"mortalité|taux de survie|faillite", nb, re.IGNORECASE) or "pas une" in nb


@pytest.mark.layer("I-integration")
@pytest.mark.integration
@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_executes_end_to_end_on_test_fixtures(name, fixture_string, monkeypatch):
    from nbclient import NotebookClient

    monkeypatch.setenv("FBD_DATA_DIR", str(fixture_string.data))
    nb = nbformat.read(DIR / f"{name}.ipynb", as_version=4)
    NotebookClient(nb, timeout=180, kernel_name="python3", resources={"metadata": {"path": str(DIR)}}).execute()
    errors = [o for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", []) if o.output_type == "error"]
    assert errors == []
    if name != "01_source_profiling":
        figures = sum(1 for c in nb.cells if c.cell_type == "code" for o in c.get("outputs", [])
                      if o.output_type == "display_data" and "image/png" in o.get("data", {}))
        assert figures >= 2


@pytest.mark.layer("I-integration")
@pytest.mark.integration
def test_notebook_02_refuses_to_run_when_the_quality_gate_failed(fixture_string, tmp_path, monkeypatch):
    """Garde-fou : aucune analyse sur des marts non validés."""
    import shutil

    import polars as pl
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError

    from france_business_dynamics.quality_gate import run_quality_gate
    from france_business_dynamics.utils.config import ProjectPaths

    broken = tmp_path / "data"
    shutil.copytree(fixture_string.data, broken)
    paths = ProjectPaths(root=PROJECT_ROOT, data=broken)
    mart = paths.processed / "fact_establishment_snapshot.parquet"
    frame = pl.read_parquet(mart)
    pl.concat([frame, frame.head(1)]).write_parquet(mart)
    assert run_quality_gate(paths, log=lambda _m: None)["status"] == "FAIL"

    monkeypatch.setenv("FBD_DATA_DIR", str(broken))
    nb = nbformat.read(DIR / "02_business_analysis.ipynb", as_version=4)
    with pytest.raises(CellExecutionError, match="quality gate"):
        NotebookClient(nb, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(DIR)}}).execute()
