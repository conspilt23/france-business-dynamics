"""Étape ETL : stocks bruts (data/raw) → couches courantes (data/processed).

    python -m france_business_dynamics.transform --dataset etablissement --limit 10000
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path

from france_business_dynamics.ingestion.verify import verify_parquet
from france_business_dynamics.transformation.pipeline import (
    build_etablissement_current,
    build_unite_legale_current,
)
from france_business_dynamics.utils.config import SOURCE_FILES, ProjectPaths
from france_business_dynamics.utils.errors import PipelineError, PreconditionError, run_cli
from france_business_dynamics.utils.manifest import describe_input, register_output, sink_atomic

DATASETS = {
    "unite-legale": ("stock_unite_legale", "unite_legale_current"),
    "etablissement": ("stock_etablissement", "etablissement_current"),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=[*DATASETS, "both"], default="both")
    parser.add_argument("--limit", type=int, help="Lignes lues (smoke test rapide).")
    parser.add_argument("--without-cog", action="store_true", help="Ne pas enrichir avec le COG 2026.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def require_raw(paths: ProjectPaths, key: str) -> Path:
    """Vérifie l'existence ET l'intégrité du Parquet brut avant tout traitement."""
    source = SOURCE_FILES[key]
    path = source.local_path(paths)
    part = path.with_name(path.name + ".part")
    if not path.exists():
        if part.exists():
            raise PreconditionError(
                f"{path.name} est incomplet : un téléchargement partiel existe ({part.name}).",
                hint=f"python -m france_business_dynamics.ingestion.download_sources --dataset {key} --resume",
            )
        raise PreconditionError(
            f"Source brute absente : {paths.relative(path)}",
            hint=f"python -m france_business_dynamics.ingestion.download_sources --dataset {key}",
        )
    verify_parquet(path)  # lève une erreur claire si le fichier est tronqué
    return path


def run_transform(
    paths: ProjectPaths,
    dataset: str = "both",
    *,
    limit: int | None = None,
    without_cog: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, dict]:
    if limit is not None and limit <= 0:
        raise PreconditionError("--limit doit être un entier strictement positif.")
    selected = list(DATASETS) if dataset == "both" else [dataset]

    # Toutes les préconditions d'abord : on n'écrit rien si une entrée manque.
    raw_paths = {name: require_raw(paths, DATASETS[name][0]) for name in selected}
    cog_path: Path | None = None
    if "etablissement" in selected and not without_cog:
        cog_path = SOURCE_FILES["cog_communes_2026"].local_path(paths)
        if not cog_path.exists():
            raise PreconditionError(
                f"Référence COG introuvable : {paths.relative(cog_path)}",
                hint="python -m france_business_dynamics.ingestion.download_sources --dataset "
                "cog_communes_2026  (ou relancer avec --without-cog).",
            )

    results: dict[str, dict] = {}
    for name in selected:
        key, output_name = DATASETS[name]
        output = paths.processed / f"{output_name}.parquet"
        started = time.monotonic()
        log(f"\n[{name}] {paths.relative(raw_paths[name])}" + (f"  (limit={limit:,})" if limit else ""))
        if name == "unite-legale":
            frame = build_unite_legale_current(raw_paths[name], limit=limit)
        else:
            frame = build_etablissement_current(raw_paths[name], cog_path=cog_path, limit=limit)
        try:
            sink_atomic(frame, output)
        except PipelineError:
            raise
        except Exception as exc:  # noqa: BLE001 - message actionnable plutôt qu'un traceback Polars
            raise PipelineError(
                f"Échec de l'écriture de {paths.relative(output)} ({type(exc).__name__}: {exc}).",
                hint="Vérifier l'espace disque et la mémoire ; relancer avec --debug pour le détail.",
            ) from exc

        inputs = {key: describe_input(paths, raw_paths[name])}
        if cog_path is not None and name == "etablissement":
            inputs["cog_communes_2026"] = describe_input(paths, cog_path)
        entry = register_output(
            paths, output_name, output, limit=limit, inputs=inputs,
            extra={"cog_enriched": cog_path is not None and name == "etablissement"},
        )
        results[output_name] = entry
        log(f"Écrit : {paths.relative(output)} — {entry['rows']:,} lignes en {time.monotonic() - started:.1f}s")
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_transform(
        ProjectPaths.from_env(), args.dataset, limit=args.limit, without_cog=args.without_cog
    )
    return 0


if __name__ == "__main__":
    run_cli(main)
