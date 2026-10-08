"""Inspecte les schémas Parquet (distants ou locaux) et produit `schema_snapshot.json`.

Aucun téléchargement complet : lecture du pied de page via HTTP Range.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from france_business_dynamics.ingestion.sources import (
    build_snapshot,
    diff_snapshots,
    inspect_local_parquet,
    inspect_remote_parquet,
)
from france_business_dynamics.utils.config import SIRENE_KEYS, SOURCE_FILES, ProjectPaths
from france_business_dynamics.utils.errors import run_cli
from france_business_dynamics.utils.jsonio import read_json, write_json_atomic


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=SIRENE_KEYS, help="Inspecter un seul Parquet.")
    parser.add_argument("--json-output", type=Path, help="Chemin du snapshot JSON à écrire.")
    parser.add_argument(
        "--compare", type=Path, help="Snapshot JSON précédent : affiche la dérive de schéma."
    )
    parser.add_argument(
        "--local", action="store_true", help="Inspecter data/raw/*.parquet au lieu des URL distantes."
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = ProjectPaths.from_env()
    keys = [args.dataset] if args.dataset else list(SIRENE_KEYS)

    results: dict[str, dict] = {}
    for key in keys:
        source = SOURCE_FILES[key]
        print("\n" + "=" * 70)
        print(key + ("  [local]" if args.local else "  [distant]"))
        print("=" * 70)
        if args.local:
            meta = inspect_local_parquet(source.local_path(paths))
        else:
            meta = inspect_remote_parquet(source.stable_url)
        results[key] = meta

        print(f"URL stable : {source.stable_url}")
        if meta.get("resolved_url"):
            print(f"URL résolue : {meta['resolved_url']}")
        print(f"Lignes : {meta['rows']:,}")
        print(f"Groupes de lignes : {meta['row_groups']:,}")
        print(f"Colonnes : {meta['column_count']}")
        for index, column in enumerate(meta["columns"], start=1):
            print(f"{index:>3}. {column['name']:<60} {column['type']}")

    snapshot = build_snapshot(results, {k: SOURCE_FILES[k].stable_url for k in keys})

    exit_code = 0
    if args.compare:
        report = diff_snapshots(read_json(args.compare), snapshot)
        print("\nComparaison avec", args.compare)
        for key, entry in report.items():
            print(f"- {key} : {entry['status']}")
            if entry["status"] == "DÉRIVE DE SCHÉMA" or entry.get("column_count_changed"):
                exit_code = 1
                for label, field in (("ajoutées", "added_columns"), ("supprimées", "removed_columns")):
                    if entry[field]:
                        print(f"    colonnes {label} : {entry[field]}")
                for column, (before, after) in entry["type_changes"].items():
                    print(f"    type modifié : {column} {before} → {after}")
            if entry.get("note"):
                print(f"    note : {entry['note']}")
            if "rows_delta" in entry:
                print(f"    variation du nombre de lignes : {entry['rows_delta']:+,}")

    if args.json_output:
        write_json_atomic(args.json_output, snapshot)
        print(f"\nSnapshot de schéma écrit : {args.json_output}")
    return exit_code


if __name__ == "__main__":
    run_cli(main)
