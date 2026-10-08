"""Chargement du staging SQL Server (`stg.*`) depuis les marts Parquet — par lots, avec contrôle de schéma.

    python -m france_business_dynamics.load_to_db --dry-run     # contrôles hors ligne, sans connexion
    python -m france_business_dynamics.load_to_db --check-only  # + comparaison avec le schéma SQL réel
    python -m france_business_dynamics.load_to_db               # chargement

Ordre des garde-fous (le premier qui échoue arrête TOUT avant la moindre insertion) :
1. fichiers présents ; 2. colonnes, types, largeurs, nulls, ASCII conformes au contrat (`sql_contract.py`) ;
3. connexion ; 4. schéma vivant (`INFORMATION_SCHEMA`) conforme au contrat ; 5. insertion par lots,
une transaction par table (échec = ROLLBACK de la table, contenu précédent intact) ;
6. comptage des lignes insérées = lignes du Parquet.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterator
from typing import Any

import polars as pl

from france_business_dynamics.sql_contract import (
    ALL_STAGING,
    BY_NAME,
    FrameSummary,
    Problem,
    TableContract,
    check_live_schema,
    preflight_parquet,
    preflight_reference,
    reference_frames,
)
from france_business_dynamics.sqlconn import SqlSettings, _short, connect
from france_business_dynamics.utils.config import ProjectPaths
from france_business_dynamics.utils.errors import PipelineError, PreconditionError, SchemaError, run_cli

DEFAULT_BATCH_SIZE = 50_000
SCHEMA_HINT = (
    "Reconstruire les tables : exécuter sql/03_create_dimensions.sql puis sql/04_create_facts.sql "
    "(ou : python -m france_business_dynamics.sql_runner --stage create --yes), puis relancer."
)
LIVE_SCHEMA_QUERY = (
    "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE "
    "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'stg' AND TABLE_NAME = ? "
    "ORDER BY ORDINAL_POSITION"
)


# --------------------------------------------------------------------------- #
# 1-2. Contrôles hors ligne
# --------------------------------------------------------------------------- #
def select_tables(names: list[str] | None) -> list[TableContract]:
    if not names:
        return list(ALL_STAGING)
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        raise PreconditionError(
            f"Table(s) inconnue(s) : {unknown}.", hint=f"Tables disponibles : {', '.join(BY_NAME)}."
        )
    return [BY_NAME[n] for n in names]


def preflight(paths: ProjectPaths, tables: list[TableContract]) -> dict[str, FrameSummary]:
    """Vérifie fichiers + conformité au contrat. Lève une erreur unique listant TOUS les problèmes."""
    summaries: dict[str, FrameSummary] = {}
    problems: list[Problem] = []
    missing: list[str] = []
    for table in tables:
        if table.parquet:
            path = paths.processed / table.parquet
            if not path.exists():
                missing.append(f"{paths.relative(path)}  (pour {table.qualified})")
                continue
            summary, found = preflight_parquet(path, table)
        else:
            summary, found = preflight_reference(table)
        summaries[table.name] = summary
        problems.extend(found)

    if missing:
        raise PreconditionError(
            "Marts introuvables :\n    " + "\n    ".join(missing),
            hint="python -m france_business_dynamics.build_mart  (puis le quality gate : "
            "python -m france_business_dynamics.quality_gate --strict). "
            "Pour ne charger qu'une partie : --table EstablishmentSnapshot --table RefRegion …",
        )
    if problems:
        shown = "\n    ".join(str(p) for p in problems[:20])
        more = f"\n    … et {len(problems) - 20} autre(s)" if len(problems) > 20 else ""
        raise SchemaError(
            f"{len(problems)} incompatibilité(s) entre les données et le contrat SQL — aucune insertion tentée :"
            f"\n    {shown}{more}",
            hint="Corriger le mart (build_mart) ou, si la nouvelle largeur est légitime, mettre à jour "
            "sql_contract.py ET sql/04_create_facts.sql (un test vérifie qu'ils restent identiques).",
        )
    return summaries


# --------------------------------------------------------------------------- #
# 4. Schéma vivant
# --------------------------------------------------------------------------- #
def check_live(connection, tables: list[TableContract]) -> None:
    cursor = connection.cursor()
    problems: list[Problem] = []
    for table in tables:
        cursor.execute(LIVE_SCHEMA_QUERY, table.name)
        live = [(r[0], r[1], r[2], r[3]) for r in cursor.fetchall()]
        problems.extend(check_live_schema(table, live))
    if problems:
        shown = "\n    ".join(str(p) for p in problems[:20])
        raise SchemaError(
            f"Le schéma SQL existant ne correspond pas au contrat ({len(problems)} écart(s)) :\n    {shown}",
            hint=SCHEMA_HINT,
        )


# --------------------------------------------------------------------------- #
# 5-6. Insertion par lots
# --------------------------------------------------------------------------- #
def iter_batches(paths: ProjectPaths, table: TableContract, batch_size: int) -> Iterator[list[tuple]]:
    """Lots de tuples, dans l'ordre des colonnes du contrat (jamais tout le fichier en mémoire)."""
    if table.parquet:
        import pyarrow.parquet as pq

        reader = pq.ParquetFile(paths.processed / table.parquet)
        for batch in reader.iter_batches(batch_size=batch_size, columns=table.column_names):
            columns = [batch.column(name).to_pylist() for name in table.column_names]
            yield list(zip(*columns, strict=True))
    else:
        frame = reference_frames()[table.reference_loader].select(table.column_names)
        for offset in range(0, frame.height, batch_size):
            yield frame.slice(offset, batch_size).rows()


def _diagnose(message: str) -> str:
    lowered = message.lower()
    if "truncation" in lowered or "22001" in lowered:
        return ("Une valeur dépasse la largeur de sa colonne SQL (troncature). Le pré-contrôle aurait dû l'attraper : "
                "la table SQL est probablement un ancien schéma. " + SCHEMA_HINT)
    if "permission" in lowered or "denied" in lowered:
        return "Droits insuffisants sur le schéma stg : vérifier les permissions du compte."
    if "invalid object name" in lowered:
        return "Table absente : exécuter sql/02_create_schema.sql puis sql/04_create_facts.sql."
    if "log" in lowered and "full" in lowered:
        return "Journal de transactions plein : sauvegarder/réduire le journal ou charger en plusieurs fois (--table)."
    return "Consulter le détail ci-dessus ; relancer avec --debug pour la trace technique."


def load_table(
    connection, paths: ProjectPaths, table: TableContract, expected_rows: int, batch_size: int,
    log: Callable[[str], None],
) -> int:
    placeholders = ", ".join("?" for _ in table.columns)
    insert = f"INSERT INTO {table.qualified} ({', '.join(table.column_names)}) VALUES ({placeholders})"
    cursor = connection.cursor()
    if hasattr(cursor, "fast_executemany"):
        cursor.fast_executemany = True

    attempted = 0
    started = time.monotonic()
    try:
        cursor.execute(f"TRUNCATE TABLE {table.qualified}")
        for batch in iter_batches(paths, table, batch_size):
            first = attempted + 1
            attempted += len(batch)
            try:
                cursor.executemany(insert, batch)
            except Exception as exc:  # noqa: BLE001 - ré-emballé avec le contexte complet
                raise PipelineError(
                    f"Échec du chargement de {table.qualified} : lignes {first:,} à {attempted:,} "
                    f"(sur {expected_rows:,}). Détail SQL : {_short(exc)}",
                    hint=_diagnose(str(exc)) + " La table a été remise dans son état précédent (ROLLBACK).",
                ) from exc
            pct = attempted / expected_rows * 100 if expected_rows else 100.0
            log(f"  {table.qualified} : {attempted:>12,} / {expected_rows:,} ({pct:5.1f} %)")
        cursor.execute(f"SELECT COUNT(*) FROM {table.qualified}")
        inserted = int(cursor.fetchone()[0])
        if inserted != expected_rows:
            raise PipelineError(
                f"{table.qualified} : {inserted:,} lignes en base ≠ {expected_rows:,} attendues.",
                hint="Relancer le chargement ; si le problème persiste, reconstruire avec sql/04_create_facts.sql.",
            )
        connection.commit()
    except PipelineError:
        connection.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        raise PipelineError(
            f"Échec du chargement de {table.qualified} après {attempted:,} ligne(s) tentée(s). "
            f"Détail : {_short(exc)}",
            hint=_diagnose(str(exc)) + " La table a été remise dans son état précédent (ROLLBACK).",
        ) from exc
    log(f"  {table.qualified} : terminé — {inserted:,} lignes en {time.monotonic() - started:.1f}s")
    return inserted


def run_load(
    paths: ProjectPaths,
    *,
    table_names: list[str] | None = None,
    dry_run: bool = False,
    check_only: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    settings: SqlSettings | None = None,
    connection_factory: Callable[[], Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    if batch_size <= 0:
        raise PreconditionError("--batch-size doit être un entier strictement positif.")
    tables = select_tables(table_names)

    log("1/4  Contrôle des données par rapport au contrat SQL (hors ligne)…")
    summaries = preflight(paths, tables)
    for table in tables:
        log(f"     OK  {table.qualified:32s} {summaries[table.name].rows:>12,} lignes")
    if dry_run:
        log("\nDry-run terminé : données conformes au contrat. Aucune connexion SQL n'a été ouverte.")
        return {t.name: summaries[t.name].rows for t in tables}

    log("2/4  Connexion…")
    owns_connection = connection_factory is None
    if connection_factory is None:
        settings = settings or SqlSettings.from_env()
        log(f"     {settings.describe()}")
        connection = connect(settings)
    else:
        connection = connection_factory()
    try:
        log("3/4  Contrôle du schéma SQL existant…")
        check_live(connection, tables)
        log("     OK  schéma SQL conforme au contrat")
        if check_only:
            log("\nCheck-only terminé : le schéma SQL est prêt. Aucune donnée insérée.")
            return {}

        log("4/4  Chargement par lots…")
        loaded: dict[str, int] = {}
        for table in tables:
            log(f"\n{table.qualified}")
            loaded[table.name] = load_table(
                connection, paths, table, summaries[table.name].rows, batch_size, log
            )
        log(f"\nChargement terminé : {sum(loaded.values()):,} lignes dans {len(loaded)} table(s) stg.*.")
        log("Étape suivante : sql/06_load_mart.sql puis sql/07_quality_checks.sql "
            "(ou : python -m france_business_dynamics.sql_runner --stage finalize).")
        return loaded
    finally:
        if owns_connection:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--table", action="append", dest="tables", metavar="NOM",
                        help=f"Charger seulement cette table (répétable). Choix : {', '.join(BY_NAME)}.")
    parser.add_argument("--dry-run", action="store_true", help="Contrôles hors ligne uniquement (aucune connexion).")
    parser.add_argument("--check-only", action="store_true", help="Contrôler aussi le schéma SQL réel, sans insérer.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    run_load(
        ProjectPaths.from_env(), table_names=args.tables, dry_run=args.dry_run,
        check_only=args.check_only, batch_size=args.batch_size,
    )
    return 0


if __name__ == "__main__":
    run_cli(main)
