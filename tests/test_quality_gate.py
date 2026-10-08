from __future__ import annotations

import json
import shutil

import polars as pl
import pytest

from france_business_dynamics.quality_gate import LAYERS, main, run_quality_gate
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths
from france_business_dynamics.utils.errors import PreconditionError
from france_business_dynamics.utils.jsonio import read_json

pytestmark = pytest.mark.layer("F-quality")
QUIET = {"log": lambda _m: None}


@pytest.fixture
def work(fixture_string, tmp_path) -> ProjectPaths:
    """Copie jetable des données synthétiques de test (les tests peuvent la corrompre sans effet de bord)."""
    target = tmp_path / "data"
    shutil.copytree(fixture_string.data, target)
    return ProjectPaths(root=PROJECT_ROOT, data=target)


def test_full_pipeline_passes_and_writes_a_structured_report(work):
    report = run_quality_gate(work, strict=True, **QUIET)
    assert report["status"] == "PASS" and report["complete"] is True and report["mode"] == "strict"
    assert report["summary"]["failed"] == 0 and report["summary"]["layers_checked"] == 5
    stored = read_json(work.processed / "quality_report.json")
    assert stored["status"] == "PASS"
    assert set(stored["layers"]) == {layer.name for layer in LAYERS}
    first_check = stored["layers"]["etablissement_current"]["checks"][0]
    assert {"id", "description", "status", "severity", "observed", "expected"} <= set(first_check)
    assert stored["generated_at_utc"] and stored["tool_version"]


def test_missing_layer_is_skipped_with_the_command_to_produce_it(work, capsys):
    """Le cas réel de l'utilisateur : unite_legale_current.parquet n'existe pas encore."""
    (work.processed / "unite_legale_current.parquet").unlink()
    (work.processed / "fact_legal_unit_snapshot.parquet").unlink()
    logs: list[str] = []
    report = run_quality_gate(work, log=logs.append)
    assert report["status"] == "PASS"  # pas d'échec confus
    assert report["complete"] is False
    skipped = report["layers"]["unite_legale_current"]
    assert skipped["status"] == "SKIPPED" and "--dataset unite-legale" in skipped["produce_with"]
    assert any("unite_legale_current" in line and "transform" in line for line in logs)
    assert report["summary"]["layers_skipped"] == ["unite_legale_current", "fact_legal_unit_snapshot"]


def test_strict_mode_turns_a_missing_layer_into_a_failure(work):
    (work.processed / "fact_administrative_events.parquet").unlink()
    report = run_quality_gate(work, strict=True, **QUIET)
    assert report["status"] == "FAIL"
    assert report["layers"]["fact_administrative_events"]["status"] == "FAIL"


def test_empty_data_dir_is_a_precondition_error_with_guidance(tmp_path):
    paths = ProjectPaths(root=PROJECT_ROOT, data=tmp_path / "vide")
    with pytest.raises(PreconditionError) as err:
        run_quality_gate(paths, **QUIET)
    assert "transform" in err.value.hint


def test_stage_selection_only_checks_requested_layers(work):
    report = run_quality_gate(work, stage="curated", **QUIET)
    assert set(report["layers"]) == {"etablissement_current", "unite_legale_current"}


def test_corrupted_mart_makes_the_gate_fail(work):
    path = work.processed / "fact_establishment_snapshot.parquet"
    frame = pl.read_parquet(path)
    pl.concat([frame, frame.head(1)]).write_parquet(path)  # clé dupliquée + réconciliation cassée
    report = run_quality_gate(work, **QUIET)
    assert report["status"] == "FAIL"
    failed = {c["id"] for c in report["layers"]["fact_establishment_snapshot"]["checks"] if c["status"] == "FAIL"}
    assert {"mart_etablissement.grain_unique", "mart_etablissement.reconciliation"} <= failed


def test_unreadable_layer_is_a_failure_not_a_crash(work):
    (work.processed / "fact_legal_unit_snapshot.parquet").write_bytes(b"not a parquet")
    report = run_quality_gate(work, **QUIET)
    assert report["layers"]["fact_legal_unit_snapshot"]["status"] == "FAIL"


def test_sample_runs_are_labelled_and_reconciled_against_the_limit(tmp_path):
    from france_business_dynamics.build_mart import run_build_mart
    from tests.support.fixture_data import build_test_inputs
    from france_business_dynamics.transform import run_transform

    paths = ProjectPaths(root=PROJECT_ROOT, data=tmp_path)
    build_test_inputs(paths)
    run_transform(paths, "etablissement", limit=10, **QUIET)
    run_build_mart(paths, limit=10, skip_events=True, skip_legal_units=True, **QUIET)  # commande de smoke test réelle
    report = run_quality_gate(paths, **QUIET)
    assert report["status"] == "PASS"
    assert report["contains_samples"] is True
    assert report["layers"]["etablissement_current"]["scope"] == "ECHANTILLON (limit=10)"
    assert report["layers"]["unite_legale_current"]["status"] == "SKIPPED"
    recon = {c["id"]: c for c in report["layers"]["fact_establishment_snapshot"]["checks"]}["mart_etablissement.reconciliation"]
    assert recon["status"] == "PASS" and recon["observed"] == 10


def test_main_exit_codes(work, monkeypatch, capsys):
    monkeypatch.setenv("FBD_DATA_DIR", str(work.data))
    assert main([]) == 0
    path = work.processed / "fact_establishment_snapshot.parquet"
    frame = pl.read_parquet(path)
    pl.concat([frame, frame.head(1)]).write_parquet(path)
    assert main([]) == 1
