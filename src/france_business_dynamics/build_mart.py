"""Étape marts : couches courantes (+ historiques) → tables de faits analytiques Parquet.

    python -m france_business_dynamics.build_mart --limit 10000 --skip-events --skip-legal-units
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable

from france_business_dynamics.ingestion.verify import verify_parquet
from france_business_dynamics.transformation.marts import (
    administrative_events,
    current_establishment_snapshot_from_curated,
    legal_unit_current_snapshot,
)
from france_business_dynamics.utils.config import (
    ANALYSIS_END_YEAR,
    ANALYSIS_START_YEAR,
    SNAPSHOT_DATE,
    SOURCE_FILES,
    ProjectPaths,
)
from france_business_dynamics.utils.errors import PreconditionError, run_cli
from france_business_dynamics.utils.manifest import describe_input, register_output, sink_atomic

TRANSFORM_HINT = "python -m france_business_dynamics.transform --dataset {dataset}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="Smoke test des marts courants uniquement.")
    parser.add_argument("--skip-events", action="store_true", help="Ne pas construire les événements (historiques).")
    parser.add_argument("--skip-legal-units", action="store_true", help="Ne pas construire le mart des unités légales.")
    parser.add_argument("--skip-establishments", action="store_true")
    parser.add_argument("--snapshot-date", default=SNAPSHOT_DATE, help="AAAA-MM-JJ du stock SIRENE utilisé.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def run_build_mart(
    paths: ProjectPaths,
    *,
    limit: int | None = None,
    skip_events: bool = False,
    skip_legal_units: bool = False,
    skip_establishments: bool = False,
    snapshot_date: str = SNAPSHOT_DATE,
    log: Callable[[str], None] = print,
) -> dict[str, dict]:
    if limit is not None and not skip_events:
        raise PreconditionError(
            "--limit est réservé au smoke test des marts courants.",
            hint="Ajouter --skip-events (les historiques ne se traitent pas sur un échantillon).",
        )
    if limit is not None and limit <= 0:
        raise PreconditionError("--limit doit être un entier strictement positif.")

    est_curated = paths.processed / "etablissement_current.parquet"
    ul_curated = paths.processed / "unite_legale_current.parquet"

    # --- préconditions : tout vérifier AVANT d'écrire quoi que ce soit -------------------
    needed: list[tuple[str, object]] = []
    if not skip_establishments or not skip_events:
        needed.append(("etablissement", est_curated))
    if not skip_legal_units:
        needed.append(("unite-legale", ul_curated))
    for dataset, path in needed:
        if not path.exists():
            raise PreconditionError(
                f"Couche requise absente : {paths.relative(path)}",
                hint=TRANSFORM_HINT.format(dataset=dataset),
            )
    historical: dict[str, object] = {}
    if not skip_events:
        for key in ("stock_unite_legale_historique", "stock_etablissement_historique"):
            path = SOURCE_FILES[key].local_path(paths)
            if not path.exists():
                raise PreconditionError(
                    f"Historique requis absent : {paths.relative(path)}",
                    hint=f"python -m france_business_dynamics.ingestion.download_sources --dataset {key} "
                    "(ou ajouter --skip-events).",
                )
            verify_parquet(path)
            historical[key] = path

    results: dict[str, dict] = {}

    def build(name: str, frame, inputs: dict[str, dict]) -> None:
        output = paths.processed / f"{name}.parquet"
        started = time.monotonic()
        sink_atomic(frame, output)
        entry = register_output(
            paths, name, output, limit=limit, inputs=inputs,
            extra={"snapshot_date": snapshot_date, "analysis_years": [ANALYSIS_START_YEAR, ANALYSIS_END_YEAR]},
        )
        results[name] = entry
        log(f"Écrit : {paths.relative(output)} — {entry['rows']:,} lignes en {time.monotonic() - started:.1f}s")

    if not skip_establishments:
        build(
            "fact_establishment_snapshot",
            current_establishment_snapshot_from_curated(est_curated, limit=limit, snapshot_date=snapshot_date),
            {"etablissement_current": describe_input(paths, est_curated)},
        )
    if not skip_legal_units:
        build(
            "fact_legal_unit_snapshot",
            legal_unit_current_snapshot(ul_curated, limit=limit, snapshot_date=snapshot_date),
            {"unite_legale_current": describe_input(paths, ul_curated)},
        )
    if not skip_events:
        build(
            "fact_administrative_events",
            administrative_events(
                historical["stock_unite_legale_historique"],
                historical["stock_etablissement_historique"],
                est_curated,
            ),
            {
                "stock_unite_legale_historique": describe_input(paths, historical["stock_unite_legale_historique"]),
                "stock_etablissement_historique": describe_input(paths, historical["stock_etablissement_historique"]),
                "etablissement_current": describe_input(paths, est_curated),
            },
        )
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_build_mart(
        ProjectPaths.from_env(),
        limit=args.limit,
        skip_events=args.skip_events,
        skip_legal_units=args.skip_legal_units,
        skip_establishments=args.skip_establishments,
        snapshot_date=args.snapshot_date,
    )
    return 0


if __name__ == "__main__":
    run_cli(main)
