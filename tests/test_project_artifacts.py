"""Couche H — artefacts et documentation : structure, README français, CI, dépendances, liens, DAX ↔ modèle SQL."""

from __future__ import annotations

import json
import re
import tomllib

import pytest

from france_business_dynamics.sql_contract import ALL_STAGING
from france_business_dynamics.utils.config import PROJECT_ROOT, SOURCE_FILES
from france_business_dynamics.validation import structure as S
from tests.support.ddl import parse_tables

pytestmark = pytest.mark.layer("H-artifacts")
ROOT = PROJECT_ROOT
DOCS = ROOT / "docs"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# ---- intentions des anciens tests, conservées ------------------------------------------------------------
def test_documentation_references_official_sources():
    text = read("docs/source_reference.md")
    assert "data.gouv.fr" in text and "insee.fr" in text and "COG" in text
    for source in SOURCE_FILES.values():  # chaque URL du code est documentée à l'identique
        assert source.stable_url in text, source.key


def test_quality_gate_command_exists():
    assert (ROOT / "src" / "france_business_dynamics" / "quality_gate.py").exists()
    assert (ROOT / "notebooks" / "03_quality_control.ipynb").exists()


# ---- structure du dépôt ---------------------------------------------------------------------------------------
def test_repository_structure_validator_passes_on_this_repo():
    failures = [f for f in S.check_structure(ROOT) if not f.ok]
    assert failures == [], [(f.check, f.detail) for f in failures]


def test_structure_validator_detects_problems_in_a_broken_tree(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="1"\ndependencies=["polars>=1"]\n', encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("polars==1.0\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# T\n## Overview\n## Usage\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SQL_PASSWORD=hunter2\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hack.py").write_text("import sys\n" + "sys.path" + ".append('x')\n", encoding="utf-8")  # concaténé : ce fichier ne doit pas se déclarer lui-même
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("run: python -m france_business_dynamics.ingestion.download_sources\n", encoding="utf-8")
    failed = {f.check for f in S.check_structure(tmp_path) if not f.ok}
    assert {"arborescence requise", "aucun sys.path.append/insert", ".env.example sans secret actif",
            "README : sections obligatoires (français)", "README : aucun titre en anglais",
            "CI : aucun téléchargement SIRENE", "pyproject : toutes les dépendances sont fixées (==)",
            ".gitignore protège secrets et données"} <= failed


def test_requirements_parser_ignores_comments_and_editable_installs():
    assert S.parse_requirements("# c\npolars==1.2  # x\n-e .\npytest==9.0\n") == {"polars": "1.2", "pytest": "9.0"}


# ---- dépendances ----------------------------------------------------------------------------------------------
def test_pyproject_and_requirements_pin_the_same_versions_without_contradiction():
    data = tomllib.loads(read("pyproject.toml"))
    declared, installed = S.pyproject_pins(data), S.parse_requirements(read("requirements.txt"))
    assert declared == installed and len(declared) == 15
    assert data["project"]["requires-python"] == ">=3.13,<3.15"


def test_lock_file_pins_every_direct_dependency_to_the_same_version():
    lock = S.parse_requirements(read("requirements-lock.txt").replace(" ; ", "  # "))
    for name, version in S.pyproject_pins(tomllib.loads(read("pyproject.toml"))).items():
        assert lock.get(name) == version, name


def test_software_versions_doc_lists_the_pinned_versions():
    doc = read("docs/software_versions.md")
    for name in ("polars", "pyarrow", "requests", "pyodbc", "pytest", "sqlglot", "matplotlib"):
        version = S.pyproject_pins(tomllib.loads(read("pyproject.toml")))[name]
        assert version in doc, (name, version)


def test_removed_dependencies_are_really_unused():
    forbidden = re.compile(r"^\s*(import|from)\s+(pandas|sqlalchemy)\b", re.MULTILINE)
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path


# ---- README ---------------------------------------------------------------------------------------------------
def test_readme_is_written_in_french_with_all_required_sections_in_order():
    text = read("README.md")
    headings = [re.sub(r"^#+\s*", "", line).strip() for line in text.splitlines() if line.startswith("## ")]
    positions = [next(i for i, h in enumerate(headings) if h.lower().startswith(name.lower())) for name in S.README_HEADINGS]
    assert positions == sorted(positions)  # même ordre que la liste du cahier des charges


def test_readme_puts_tests_before_downloads_and_claims_only_verified_things():
    text = read("README.md")
    assert text.index("python -m pytest") < text.index("download_sources")
    assert "{{" not in text  # aucun gabarit non rempli
    assert "FINAL_VALIDATION" in text
    assert "download_sources --all" in text
    assert ("data/" + "demo") not in text


def test_readme_numbers_match_the_versioned_schema_snapshot():
    snapshot = json.loads(read("docs/schema_snapshot_2026-10-01.json"))["datasets"]
    text = read("README.md") + read("docs/source_reference.md")
    for key in ("stock_unite_legale", "stock_etablissement", "stock_unite_legale_historique", "stock_etablissement_historique"):
        assert f"{snapshot[key]['rows']:,}".replace(",", " ") in text, key


# ---- documentation --------------------------------------------------------------------------------------------
def test_required_docs_exist_and_are_not_stubs():
    for name in S.REQUIRED_DOCS:
        path = DOCS / name
        assert path.exists(), name
        assert len(path.read_text(encoding="utf-8").splitlines()) >= 20, f"{name} trop court"


def test_every_markdown_relative_link_resolves():
    broken = []
    files = [ROOT / "README.md", *DOCS.glob("*.md"), *(ROOT / "dashboard").glob("*.md"), ROOT / "data" / "README.md"]
    for path in files:
        for _, target in re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (path.parent / target.split("#")[0]).exists():
                broken.append((path.name, target))
    assert broken == []


def test_data_dictionary_documents_every_sql_column_and_mart_column():
    text = read("docs/data_dictionary.md")
    for table in ALL_STAGING:
        assert f"`{table.qualified}`" in text, table.qualified
        for column in table.columns:
            assert f"`{column.name}`" in text, (table.name, column.name)


def test_data_dictionary_documents_every_curated_column(fixture_string):
    import polars as pl

    text = read("docs/data_dictionary.md")
    for name in ("etablissement_current", "unite_legale_current"):
        for column in pl.read_parquet(fixture_string.processed / f"{name}.parquet").columns:
            assert f"`{column}`" in text, (name, column)


def test_data_quality_catalog_covers_every_check_the_gate_produces(fixture_string):
    text = read("docs/data_quality.md")
    report = json.loads((fixture_string.processed / "quality_report.json").read_text(encoding="utf-8"))
    for layer in report["layers"].values():
        for check in layer["checks"]:
            assert f"`{check['id']}`" in text, check["id"]


def test_quality_report_generated_by_test_fixtures_is_structured_and_portable(fixture_string):
    report = json.loads((fixture_string.processed / "quality_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS" and report["summary"]["failed"] == 0
    raw = json.dumps(report, ensure_ascii=False)
    assert "/tmp/" not in raw and "C:\\" not in raw


def test_json_artifacts_doc_covers_manifest_fields_the_downloader_really_writes():
    from tests.support.http_server import FakeSource
    from france_business_dynamics.ingestion.downloader import download_file
    import tempfile
    from pathlib import Path

    fake = FakeSource(b"x" * 5000).start()
    try:
        result = download_file(fake.stable_url, Path(tempfile.mkdtemp()) / "d.bin", show_progress=False, log=lambda _m: None)
    finally:
        fake.stop()
    doc = read("docs/json_artifacts.md")
    for field in ("stable_url", "resolved_url", "bytes", "expected_bytes", "sha256", "downloaded_at_utc", "attempts",
                  "resumed", "accept_ranges", "etag", "last_modified"):
        assert field in result.manifest and field in doc, field


# ---- CI -------------------------------------------------------------------------------------------------------
def test_ci_runs_install_tests_structure_compile_and_import_without_downloading_data():
    ci = read(".github/workflows/ci.yml")
    for needle in ("pip install -r requirements.txt", "python -m pytest", "validation.structure", "compileall",
                   "import france_business_dynamics", "3.13", "3.14", "windows-latest", "ubuntu-latest"):
        assert needle in ci, needle
    assert not re.search(r"download_sources|data\.gouv\.fr|insee\.fr|sqlcmd|docker", ci)


# ---- Power BI -------------------------------------------------------------------------------------------------
def test_dashboard_kit_is_complete_and_pbix_is_publishable():
    for name in ("README.md", "DAX_MEASURES.md", "power_query.md", "fbd_theme.json"):
        assert (ROOT / "dashboard" / name).exists(), name
    assert (ROOT / "dashboard" / "screenshots").is_dir()
    gitignore = read(".gitignore")
    assert "*.pbix" not in gitignore and "dashboard/*.pbix" not in gitignore
    assert "versionné" in read("dashboard/README.md")
    assert "france-business-dynamics.pbix" in read("README.md")


def test_theme_is_valid_power_bi_theme_json():
    theme = json.loads(read("dashboard/fbd_theme.json"))
    assert theme["name"] and len(theme["dataColors"]) >= 6
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", c) for c in theme["dataColors"] + [theme["background"], theme["foreground"]])


def test_dax_only_references_tables_and_columns_that_exist_in_the_sql_model():
    """Garde-fou : le DAX reste aligné sur SQL, sauf table BI locale explicitement documentée."""
    model = {**parse_tables("03_create_dimensions.sql"), **parse_tables("04_create_facts.sql")}
    columns = {name.split(".")[1]: set(cols) for name, cols in model.items() if name.startswith("analytics.")}
    columns["DimRegionMap"] = {"RegionCode", "RegionName", "Latitude", "Longitude", "IsValidRegion"}
    dax = read("dashboard/DAX_MEASURES.md")
    references = set(re.findall(r"\b([A-Z][A-Za-z]+)\[([A-Za-z0-9_]+)\]", dax))
    assert references, "aucune référence Table[Colonne] trouvée"
    unknown = [(table, column) for table, column in references if table not in columns or column not in columns[table]]
    assert unknown == [], unknown
    for measure in ("Établissements actifs", "Unités légales actives", "Fermetures administratives", "Créations 2025"):
        assert re.search(rf"^{re.escape(measure)}\s*=", dax, re.MULTILINE), measure

def test_power_query_items_are_the_eight_analytics_tables():
    text = read("dashboard/power_query.md")
    tables = {name.split(".")[1] for name in {**parse_tables("03_create_dimensions.sql"), **parse_tables("04_create_facts.sql")}
              if name.startswith("analytics.")}
    assert len(tables) == 8 and all(t in text for t in tables)


def test_dax_event_measures_always_filter_on_event_type_and_never_sum_series():
    dax = read("dashboard/DAX_MEASURES.md")
    for measure in ("Cessations administratives", "Fermetures administratives", "Créations enregistrées"):
        block = re.search(rf"{measure} =\s*(.*?)```", dax, re.DOTALL).group(1)
        assert "EventType" in block, measure
    assert "ne doivent jamais être additionnées" in dax or "ne doivent pas être additionnés" in dax


# ---- secrets --------------------------------------------------------------------------------------------------
def test_no_secrets_or_machine_specific_paths_are_committed():
    pattern = re.compile(r"(PWD=[^*;\s{'\"]+|SQL_PASSWORD\s*=\s*\S+|C:\\Users\\|/home/claude|/root/)")
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.json")) + list(ROOT.rglob("*.sql")):
        if set(path.relative_to(ROOT).parts) & S.SKIP_DIRS or path.name == "test_project_artifacts.py" or path.name == "test_load_to_db.py":
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            if match.group(0).startswith("SQL_PASSWORD") and path.name in ("sql_server.md", ".env.example"):
                continue
            pytest.fail(f"{path.relative_to(ROOT)} : {match.group(0)!r}")
