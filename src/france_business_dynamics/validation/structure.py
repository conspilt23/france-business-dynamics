"""Validation de la structure du dépôt (utilisée par la CI et par `tests/test_project_artifacts.py`).

    python -m france_business_dynamics.validation.structure

Contrôle : arborescence attendue, documentation, README en français (sections et ordre), absence de hacks
`sys.path`, de secrets et de gros fichiers, `.gitignore`, cohérence `pyproject.toml` ↔ `requirements.txt`,
et que la CI n'exécute AUCUN téléchargement de données.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from france_business_dynamics.utils.config import PROJECT_ROOT
from france_business_dynamics.utils.errors import run_cli

REQUIRED_PATHS = [
    "data/raw", "data/processed", "data/reference", "notebooks", "sql", "dashboard", "docs", "tests",
    ".github/workflows/ci.yml", "pyproject.toml", "requirements.txt", ".env.example", "README.md", "LICENSE",
    "src/france_business_dynamics/ingestion", "src/france_business_dynamics/transformation",
    "src/france_business_dynamics/validation", "src/france_business_dynamics/utils",
    "notebooks/01_source_profiling.ipynb", "notebooks/02_business_analysis.ipynb",
    "notebooks/03_quality_control.ipynb",
    "sql/01_create_database.sql", "sql/02_create_schema.sql", "sql/03_create_dimensions.sql",
    "sql/04_create_facts.sql", "sql/05_analytical_views.sql", "sql/06_load_mart.sql", "sql/07_quality_checks.sql",
    "tests/fixtures",
]
REQUIRED_DOCS = [
    "architecture.md", "data_dictionary.md", "data_quality.md", "methodology.md", "execution_checklist.md",
    "roadmap.md", "source_reference.md", "json_artifacts.md", "sql_server.md", "FINAL_VALIDATION.md",
]
README_HEADINGS = [
    "Contexte", "Objectifs", "Sources", "Architecture", "Structure du projet", "Notebooks",
    "Installation", "Vérification", "Méthodologie", "Contrôle qualité", "Téléchargement des données officielles",
    "Inspection du schéma", "ETL", "Quality Gate", "SQL Server", "Power BI", "Qualité et reproductibilité",
    "Limites méthodologiques", "NAF", "Reproductibilité", "Validation initiale", "État du projet",
]
ENGLISH_HEADINGS = ["Overview", "Getting Started", "Quick Start", "Prerequisites", "Project Structure",
                    "Usage", "Features", "Contributing", "Table of Contents"]
JSON_ARTIFACTS = ["schema_snapshot", "quality_report", "pipeline_manifest", "manifeste", "source_snapshot"]
MAX_FILE_MB = 5
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ipynb_checkpoints", "node_modules",
             ".mypy_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".pbix", ".parquet"}
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.!+\-]+)$")


@dataclass
class Finding:
    check: str
    ok: bool
    detail: str = ""


def _files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
            yield path


def parse_requirements(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = PIN.match(line)
        if match:
            pins[match.group(1).lower().replace("_", "-")] = match.group(2)
    return pins


def pyproject_pins(data: dict) -> dict[str, str]:
    specs = list(data["project"].get("dependencies", []))
    for group in data["project"].get("optional-dependencies", {}).values():
        specs += group
    return {m.group(1).lower().replace("_", "-"): m.group(2) for s in specs if (m := PIN.match(s.strip()))}


def check_structure(root: Path = PROJECT_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    add = lambda check, ok, detail="": findings.append(Finding(check, bool(ok), detail))  # noqa: E731

    missing = [p for p in REQUIRED_PATHS if not (root / p).exists()]
    add("arborescence requise", not missing, f"manquants : {missing}" if missing else "")
    missing_docs = [d for d in REQUIRED_DOCS if not (root / "docs" / d).exists()]
    add("documentation requise (docs/)", not missing_docs, f"manquants : {missing_docs}" if missing_docs else "")

    # --- aucun hack sys.path --------------------------------------------------------------------
    hack = re.compile(r"sys\.path\.(append|insert)\s*\(")
    offenders = []
    for path in _files(root):
        if path.suffix == ".py" and hack.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(path.relative_to(root)))
        if path.suffix == ".ipynb":
            nb = json.loads(path.read_text(encoding="utf-8"))
            if any(hack.search("".join(c.get("source", []))) for c in nb["cells"] if c["cell_type"] == "code"):
                offenders.append(str(path.relative_to(root)))
    add("aucun sys.path.append/insert", not offenders, f"{offenders}" if offenders else "")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")) if (root / "pyproject.toml").exists() else {}
    add("pas de `pythonpath` pytest", "pythonpath" not in pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {}))

    # --- secrets et fichiers lourds ---------------------------------------------------------------
    example = (root / ".env.example").read_text(encoding="utf-8") if (root / ".env.example").exists() else ""
    secret_lines = [l for l in example.splitlines()
                    if re.match(r"\s*(SQL_PASSWORD|SQL_USER)\s*=\s*\S+", l) and not l.lstrip().startswith("#")]
    add(".env.example sans secret actif", not secret_lines, f"{secret_lines}" if secret_lines else "")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").exists() else ""
    wanted = [".env", "data/raw/", "data/processed/", "data/reference/", "*.part"]
    absent = [w for w in wanted if w not in gitignore]
    add(".gitignore protège secrets et données", not absent, f"absents : {absent}" if absent else "")
    heavy = [str(p.relative_to(root)) for p in _files(root)
             if p.suffix not in SKIP_SUFFIXES and p.stat().st_size > MAX_FILE_MB * 1024 * 1024]
    add(f"aucun fichier > {MAX_FILE_MB} Mo", not heavy, f"{heavy}" if heavy else "")
    add("aucun .env réel dans le dépôt versionné", (root / ".env").exists() is False or ".env" in gitignore)

    # --- README en français ------------------------------------------------------------------------
    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    headings = [re.sub(r"^#+\s*(\d+[.)]\s*)?", "", l).strip() for l in readme.splitlines() if l.startswith("#")]

    def position(name: str) -> int:
        return next((i for i, h in enumerate(headings) if h.lower().startswith(name.lower())), -1)

    absent_headings = [h for h in README_HEADINGS if position(h) < 0]
    add("README : sections obligatoires (français)", not absent_headings, f"absentes : {absent_headings}" if absent_headings else "")
    english = [h for h in ENGLISH_HEADINGS if any(x.lower() == h.lower() for x in headings)]
    add("README : aucun titre en anglais", not english, f"{english}" if english else "")
    order_ok = all(position(a) < position(b) for a, b in
                   [("Installation", "Vérification"), ("Vérification", "Téléchargement"),
                    ("Téléchargement", "ETL"), ("ETL", "Quality Gate"), ("Quality Gate", "SQL Server"),
                    ("SQL Server", "Power BI")]) and position("Installation") >= 0
    add("README : tests (Vérification) AVANT les téléchargements", order_ok)
    first_pytest, first_download = readme.find("python -m pytest"), readme.find("download_sources")
    add("README : `pytest` apparaît avant `download_sources`", 0 <= first_pytest < first_download)
    add("README : section JSON documente chaque artefact", all(k in readme for k in ("schema_snapshot", "quality_report", "pipeline_manifest")))

    # --- CI --------------------------------------------------------------------------------------------
    ci_path = root / ".github" / "workflows" / "ci.yml"
    ci = ci_path.read_text(encoding="utf-8") if ci_path.exists() else ""
    for label, needle in [("installation", "pip install -r requirements.txt"), ("tests", "pytest"),
                          ("validation de structure", "validation.structure"), ("compilation", "compileall"),
                          ("import du paquet", "import france_business_dynamics")]:
        add(f"CI : {label}", needle in ci)
    add("CI : aucun téléchargement SIRENE", not re.search(r"download_sources|data\.gouv\.fr|insee\.fr", ci))

    # --- dépendances ---------------------------------------------------------------------------------
    if pyproject and (root / "requirements.txt").exists():
        declared = pyproject_pins(pyproject)
        installed = parse_requirements((root / "requirements.txt").read_text(encoding="utf-8"))
        build = [s for s in pyproject.get("build-system", {}).get("requires", [])]
        unpinned = [s for s in pyproject["project"].get("dependencies", []) if not PIN.match(s.strip())]
        add("pyproject : toutes les dépendances sont fixées (==)", not unpinned, f"{unpinned}" if unpinned else "")
        add("build-system fixé (==)", all(PIN.match(s.strip()) for s in build))
        diff = {k: (declared.get(k), installed.get(k)) for k in set(declared) | set(installed)
                if declared.get(k) != installed.get(k)}
        add("pyproject.toml ↔ requirements.txt identiques", not diff, f"écarts : {diff}" if diff else "")
        add("requires-python déclaré", bool(pyproject["project"].get("requires-python")))

    # --- JSON documentés -------------------------------------------------------------------------------
    doc = (root / "docs" / "json_artifacts.md").read_text(encoding="utf-8") if (root / "docs" / "json_artifacts.md").exists() else ""
    add("docs/json_artifacts.md documente chaque JSON", all(k in doc.lower() for k in JSON_ARTIFACTS))
    return findings


def main(argv: list[str] | None = None) -> int:
    findings = check_structure()
    width = max(len(f.check) for f in findings)
    for finding in findings:
        mark = "PASS" if finding.ok else "FAIL"
        print(f"[{mark}] {finding.check:{width}s}  {finding.detail}".rstrip())
    failed = [f for f in findings if not f.ok]
    print(f"\nStructure : {len(findings) - len(failed)}/{len(findings)} contrôles réussis.")
    return 1 if failed else 0


if __name__ == "__main__":
    run_cli(main)
