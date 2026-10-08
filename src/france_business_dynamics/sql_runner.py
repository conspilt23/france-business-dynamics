"""Ejecuta los scripts SQL del proyecto en el orden documentado (alternative automatisée à SSMS).

    python -m france_business_dynamics.sql_runner --list
    python -m france_business_dynamics.sql_runner --stage create   --yes   # 01 → 05
    python -m france_business_dynamics.load_to_db                          # Python : remplit stg.*
    python -m france_business_dynamics.sql_runner --stage finalize         # 06 → 07
    python -m france_business_dynamics.sql_runner --stage all      --yes   # tout, dans l'ordre

Ordre garanti : 01 → 02 → 03 → 04 → 05 → load_to_db → 06 → 07. `all` commence par les contrôles
hors ligne de load_to_db : aucune table n'est reconstruite si les données ne sont pas conformes.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from france_business_dynamics.load_to_db import _short, run_load
from france_business_dynamics.sqlconn import SqlSettings, connect
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths
from france_business_dynamics.utils.errors import PipelineError, PreconditionError, run_cli

SQL_DIR = PROJECT_ROOT / "sql"
CREATE_SCRIPTS = [
    "01_create_database.sql", "02_create_schema.sql", "03_create_dimensions.sql",
    "04_create_facts.sql", "05_analytical_views.sql",
]
FINALIZE_SCRIPTS = ["06_load_mart.sql", "07_quality_checks.sql"]
GO_PATTERN = re.compile(r"(?im)^\s*GO\s*;?\s*$")


def split_batches(text: str) -> list[str]:
    """Découpe sur les lignes `GO` (séparateur de lots de sqlcmd / SSMS)."""
    return [batch.strip() for batch in GO_PATTERN.split(text) if batch.strip()]


def evaluate_quality_rows(rows: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(échecs, avertissements) d'après le jeu de résultats de 07_quality_checks.sql."""
    return (
        [r for r in rows if str(r[1]).upper() == "FAIL"],
        [r for r in rows if str(r[1]).upper() == "WARN"],
    )


def _hint_for(message: str) -> str:
    if "50001" in message:
        return "Le staging est vide : exécuter d'abord `python -m france_business_dynamics.load_to_db`."
    if "Invalid object name" in message and "stg." in message:
        return "Les tables de staging n'existent pas : exécuter --stage create (scripts 01 à 05)."
    if "Cannot drop" in message or "FOREIGN KEY" in message:
        return "Une contrainte bloque la reconstruction : rejouer 03_create_dimensions.sql (supprime d'abord les faits)."
    return "Corriger la cause indiquée puis relancer ; les scripts de reconstruction (03, 04) sont rejouables."


def run_script(connection, path: Path, log: Callable[[str], None]) -> list[tuple] | None:
    """Exécute un script lot par lot. Retourne les lignes du dernier SELECT, s'il y en a un."""
    batches = split_batches(path.read_text(encoding="utf-8"))
    last_rows: list[tuple] | None = None
    for index, batch in enumerate(batches, start=1):
        cursor = connection.cursor()
        try:
            cursor.execute(batch)
            if cursor.description:
                last_rows = [tuple(row) for row in cursor.fetchall()]
            # pyodbc : une erreur dans une instruction ultérieure du lot n'est levée qu'en parcourant les jeux de résultats
            while cursor.nextset():
                if cursor.description:
                    last_rows = [tuple(row) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001 - message actionnable avec nom de script et numéro de lot
            raise PipelineError(
                f"Échec de {path.name} (lot {index}/{len(batches)}) : {_short(exc)}",
                hint=_hint_for(str(exc)),
            ) from exc
    log(f"  OK  {path.name}  ({len(batches)} lot(s))")
    return last_rows


def _session(connection) -> None:
    """Pas de compteurs de lignes parasites : un seul jeu de résultats par SELECT."""
    cursor = connection.cursor()
    cursor.execute("SET NOCOUNT ON")


def run_sql_pipeline(
    paths: ProjectPaths,
    *,
    stage: str,
    yes: bool = False,
    dry_run: bool = False,
    settings: SqlSettings | None = None,
    connection_factory: Callable[[str | None], Any] | None = None,
    load: Callable[..., Any] = run_load,
    log: Callable[[str], None] = print,
) -> int:
    """Retourne 0 si tout est conforme, 1 si 07_quality_checks.sql contient un FAIL."""
    if stage in {"create", "all"} and not (yes or dry_run):
        raise PreconditionError(
            "Les scripts 03 et 04 RECONSTRUISENT les schémas analytics.* et stg.* (DROP puis CREATE).",
            hint="Ajouter --yes pour confirmer (les données sont régénérables depuis les Parquet), "
            "ou --dry-run pour voir le plan sans rien exécuter.",
        )

    plan: list[str] = []
    if stage in {"create", "all"}:
        plan += CREATE_SCRIPTS
    if stage in {"load", "all"}:
        plan.append("python: load_to_db")
    if stage in {"finalize", "all"}:
        plan += FINALIZE_SCRIPTS
    log("Plan : " + " → ".join(plan))

    if stage == "all" or dry_run:
        log("\nContrôles hors ligne (avant toute modification de la base)…")
        load(paths, dry_run=True, log=log)
    if dry_run:
        log("\nDry-run terminé : aucune connexion ouverte, aucune base modifiée.")
        return 0

    if connection_factory is None:
        settings = settings or SqlSettings.from_env()
        log(f"\nCible : {settings.describe()}")

        def connection_factory(database: str | None = None):
            return connect(settings, database, autocommit=True)

    if stage in {"create", "all"}:
        log("\n[create] 01 sur master, puis 02 → 05 sur la base du projet")
        master = connection_factory("master")
        try:
            _session(master)
            run_script(master, SQL_DIR / CREATE_SCRIPTS[0], log)
        finally:
            master.close()
        project = connection_factory(None)
        try:
            _session(project)
            for name in CREATE_SCRIPTS[1:]:
                run_script(project, SQL_DIR / name, log)
        finally:
            project.close()

    if stage in {"load", "all"}:
        log("\n[load] chargement Python du staging")
        load(paths, settings=settings, log=log)

    exit_code = 0
    if stage in {"finalize", "all"}:
        log("\n[finalize] 06 (chargement du modèle) puis 07 (contrôles)")
        project = connection_factory(None)
        try:
            _session(project)
            run_script(project, SQL_DIR / FINALIZE_SCRIPTS[0], log)
            rows = run_script(project, SQL_DIR / FINALIZE_SCRIPTS[1], log)
        finally:
            project.close()
        if not rows:
            raise PipelineError("07_quality_checks.sql n'a retourné aucun résultat.",
                                hint="Vérifier que le script n'a pas été modifié (il doit se terminer par un SELECT).")
        fails, warns = evaluate_quality_rows(rows)
        width = max(len(str(r[0])) for r in rows)
        log("\nContrôles SQL :")
        for name, status, observed, expected in rows:
            log(f"  [{str(status):4s}] {str(name):{width}s}  observé={observed}  attendu={expected}")
        log(f"\nRésultat SQL : {len(rows) - len(fails) - len(warns)} PASS, {len(warns)} WARN, {len(fails)} FAIL")
        exit_code = 1 if fails else 0
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["create", "load", "finalize", "all"])
    parser.add_argument("--list", action="store_true", help="Afficher l'ordre d'exécution documenté.")
    parser.add_argument("--yes", action="store_true", help="Confirmer la reconstruction de analytics.* / stg.*.")
    parser.add_argument("--dry-run", action="store_true", help="Plan + contrôles hors ligne, sans connexion.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if args.list or not args.stage:
        print("Ordre d'exécution :")
        for number, name in enumerate(CREATE_SCRIPTS, start=1):
            print(f"  {number}. sql/{name}")
        print("  6. python -m france_business_dynamics.load_to_db")
        for number, name in enumerate(FINALIZE_SCRIPTS, start=7):
            print(f"  {number}. sql/{name}")
        return 0
    return run_sql_pipeline(
        ProjectPaths.from_env(), stage=args.stage, yes=args.yes, dry_run=args.dry_run
    )


if __name__ == "__main__":
    run_cli(main)
