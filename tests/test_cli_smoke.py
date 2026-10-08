"""Couche I — commandes CLI exécutées comme de VRAIS sous-processus : codes de sortie, préconditions, messages.

Aucun accès réseau : les téléchargements et la connexion SQL ne sont jamais déclenchés ici.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.support.fixture_data import build_test_inputs
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths

pytestmark = [pytest.mark.layer("I-integration"), pytest.mark.integration]


def run(module: str, *args: str, data_dir, check_traceback: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "FBD_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-m", f"france_business_dynamics.{module}", *args],
                            capture_output=True, text=True, encoding="utf-8", env=env, cwd=PROJECT_ROOT, timeout=300)
    if check_traceback:
        assert "Traceback" not in result.stderr + result.stdout, result.stderr
    return result


@pytest.fixture
def empty(tmp_path):
    return tmp_path / "vide"


@pytest.fixture
def prepared(tmp_path):
    """Jeu synthétique interne aux tests (entrées brutes uniquement), prêt pour transform."""
    target = tmp_path / "d"
    build_test_inputs(ProjectPaths(root=PROJECT_ROOT, data=target))
    return target


# --- préconditions : message clair + code 2, jamais de traceback ---------------------------------
def test_transform_without_raw_data_names_the_download_command(empty):
    result = run("transform", "--dataset", "etablissement", data_dir=empty)
    assert result.returncode == 2
    assert "Source brute absente" in result.stderr and "download_sources --dataset stock_etablissement" in result.stderr


def test_transform_with_partial_download_suggests_resume(empty):
    raw = empty / "raw"
    raw.mkdir(parents=True)
    (raw / "stock_etablissement.parquet.part").write_bytes(b"x" * 100)
    result = run("transform", "--dataset", "etablissement", data_dir=empty)
    assert result.returncode == 2 and "incomplet" in result.stderr and "--resume" in result.stderr


def test_transform_refuses_a_truncated_parquet(empty):
    raw = empty / "raw"
    raw.mkdir(parents=True)
    (raw / "stock_etablissement.parquet").write_bytes(b"PAR1" + b"\x00" * 50)  # début valide, fin absente
    result = run("transform", "--dataset", "etablissement", data_dir=empty)
    assert result.returncode == 1 and "octets magiques" in result.stderr and "--overwrite" in result.stderr


def test_transform_without_cog_names_the_command_or_the_escape_hatch(prepared):
    (prepared / "reference" / "v_commune_2026.csv").unlink()
    result = run("transform", "--dataset", "etablissement", data_dir=prepared)
    assert result.returncode == 2 and "cog_communes_2026" in result.stderr and "--without-cog" in result.stderr
    assert run("transform", "--dataset", "etablissement", "--without-cog", data_dir=prepared).returncode == 0


def test_transform_rejects_a_non_positive_limit(prepared):
    assert run("transform", "--limit", "0", data_dir=prepared).returncode == 2


def test_build_mart_without_curated_layer_names_the_transform_command(empty):
    result = run("build_mart", "--skip-events", "--skip-legal-units", data_dir=empty)
    assert result.returncode == 2 and "Couche requise absente" in result.stderr and "transform --dataset etablissement" in result.stderr


def test_build_mart_limit_requires_skip_events(prepared):
    result = run("build_mart", "--limit", "10", data_dir=prepared)
    assert result.returncode == 2 and "--skip-events" in result.stderr


def test_build_mart_events_need_the_historical_files(prepared):
    assert run("transform", "--dataset", "etablissement", data_dir=prepared).returncode == 0
    (prepared / "raw" / "stock_etablissement_historique.parquet").unlink()
    result = run("build_mart", "--skip-legal-units", "--skip-establishments", data_dir=prepared)
    assert result.returncode == 2 and "Historique requis absent" in result.stderr and "--skip-events" in result.stderr


def test_quality_gate_on_empty_data_explains_what_to_run(empty):
    result = run("quality_gate", data_dir=empty)
    assert result.returncode == 2 and "Aucune couche" in result.stderr and "transform" in result.stderr


def test_load_to_db_dry_run_without_marts_names_build_mart(empty):
    result = run("load_to_db", "--dry-run", data_dir=empty)
    assert result.returncode == 2 and "Marts introuvables" in result.stderr and "build_mart" in result.stderr


def test_sql_runner_requires_confirmation_for_destructive_stages(empty):
    result = run("sql_runner", "--stage", "create", data_dir=empty)
    assert result.returncode == 2 and "--yes" in result.stderr


def test_download_sources_without_selection_lists_the_keys(empty):
    result = run("ingestion.download_sources", data_dir=empty)
    assert result.returncode == 2 and "stock_etablissement" in result.stderr and "--all-sirene" in result.stderr


def test_download_sources_rejects_resume_with_overwrite(empty):
    result = run("ingestion.download_sources", "--dataset", "stock_etablissement", "--resume", "--overwrite", data_dir=empty)
    assert result.returncode == 2 and "incompatibles" in result.stderr


def test_inspect_schema_local_without_files_explains_the_fix(empty):
    result = run("ingestion.inspect_schema", "--local", "--dataset", "stock_etablissement", data_dir=empty)
    assert result.returncode == 1 and "introuvable" in result.stderr and "download_sources" in result.stderr


# --- la chaîne complète, étape par étape, avec les codes de sortie documentés --------------------------
def test_full_documented_chain_on_test_fixtures(prepared):
    steps = [
        ("transform", ["--dataset", "etablissement", "--limit", "10"], 0),          # smoke test réel du README
        ("build_mart", ["--limit", "10", "--skip-events", "--skip-legal-units"], 0),  # smoke test réel du README
        ("quality_gate", [], 0),                                                      # partiel : couches manquantes SKIPPED
        ("quality_gate", ["--strict"], 1),                                            # strict : couches manquantes = échec
        ("transform", [], 0),
        ("build_mart", [], 0),
        ("quality_gate", ["--strict"], 0),
        ("load_to_db", ["--dry-run"], 0),
        ("sql_runner", ["--stage", "all", "--dry-run"], 0),
    ]
    for module, args, expected in steps:
        result = run(module, *args, data_dir=prepared)
        assert result.returncode == expected, f"{module} {args}\n{result.stdout}\n{result.stderr}"
    assert (prepared / "processed" / "quality_report.json").exists()
    assert (prepared / "processed" / "pipeline_manifest.json").exists()


def test_rerunning_the_pipeline_is_idempotent(prepared):
    for _ in range(2):
        assert run("transform", data_dir=prepared).returncode == 0
        assert run("build_mart", data_dir=prepared).returncode == 0
    assert run("quality_gate", "--strict", data_dir=prepared).returncode == 0
    assert not list((prepared / "processed").glob("*.tmp"))  # aucun fichier temporaire résiduel


def test_fixture_generation_succeeds_with_both_source_date_formats(tmp_path):
    for fmt in ("string", "date"):
        target = tmp_path / fmt
        paths = ProjectPaths(root=PROJECT_ROOT, data=target)
        build_test_inputs(paths, date_format=fmt)
        assert (target / "raw" / "stock_unite_legale.parquet").exists()
        assert (target / "raw" / "stock_etablissement.parquet").exists()
        assert (target / "raw" / "stock_unite_legale_historique.parquet").exists()
        assert (target / "raw" / "stock_etablissement_historique.parquet").exists()
        assert (target / "reference" / "v_commune_2026.csv").exists()


def test_debug_flag_is_accepted_by_every_command(empty):
    for module in ("transform", "build_mart", "quality_gate", "load_to_db", "sql_runner"):
        assert "unrecognized arguments" not in run(module, "--debug", "--help", data_dir=empty).stderr


def test_redirected_output_in_a_legacy_windows_codepage_never_crashes(prepared):
    """Régression (reproduite) : `quality_gate > log.txt` sous Windows (cp1252) levait UnicodeEncodeError sur `≤`."""
    assert run("transform", "--dataset", "etablissement", data_dir=prepared).returncode == 0
    env = {**os.environ, "FBD_DATA_DIR": str(prepared), "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run([sys.executable, "-m", "france_business_dynamics.quality_gate"],
                            capture_output=True, env=env, cwd=PROJECT_ROOT, timeout=120)
    text = (result.stdout + result.stderr).decode("cp1252", errors="replace")
    assert result.returncode == 0, text
    assert "UnicodeEncodeError" not in text and "avertissement" in text


def test_error_messages_survive_a_legacy_codepage_too(empty):
    env = {**os.environ, "FBD_DATA_DIR": str(empty), "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run([sys.executable, "-m", "france_business_dynamics.quality_gate"],
                            capture_output=True, env=env, cwd=PROJECT_ROOT, timeout=120)
    text = result.stderr.decode("cp1252", errors="replace")
    assert result.returncode == 2 and "Action" in text and "UnicodeEncodeError" not in text
