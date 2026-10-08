"""Télécharge les sources officielles SIRENE / COG de façon robuste et reprenable."""

from __future__ import annotations

import argparse

from france_business_dynamics.ingestion.downloader import (
    download_file,
    human_bytes,
    probe_remote,
)
from france_business_dynamics.ingestion.verify import make_csv_verifier, verify_parquet
from france_business_dynamics.utils.config import (
    SIRENE_KEYS,
    SOURCE_FILES,
    ProjectPaths,
    SourceFile,
)
from france_business_dynamics.utils.errors import PipelineError, PreconditionError, run_cli

COG_REQUIRED_COLUMNS = ("TYPECOM", "COM", "DEP", "REG", "LIBELLE")


def verifier_for(source: SourceFile):
    if source.kind == "parquet":
        return verify_parquet
    return make_csv_verifier(COG_REQUIRED_COLUMNS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=list(SOURCE_FILES), action="append",
                        help="Clé de source (répétable).")
    parser.add_argument("--all-sirene", action="store_true", help="Les quatre Parquet SIRENE.")
    parser.add_argument("--all", action="store_true", help="Toutes les sources (SIRENE + COG).")
    parser.add_argument("--resume", action="store_true",
                        help="Reprendre un téléchargement partiel (.part) après vérification.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Repartir de zéro : supprime le partiel et remplace le fichier final.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Interroge les sources (taille, Range, espace disque) sans télécharger.")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def select_datasets(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(SOURCE_FILES)
    if args.all_sirene:
        return list(SIRENE_KEYS)
    if args.dataset:
        return list(dict.fromkeys(args.dataset))
    raise PreconditionError(
        "Aucune source choisie.",
        hint="Utiliser --dataset <clé> (répétable), --all-sirene ou --all. "
        f"Clés : {', '.join(SOURCE_FILES)}.",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.resume and args.overwrite:
        raise PreconditionError("--resume et --overwrite sont incompatibles.")
    paths = ProjectPaths.from_env()
    selected = select_datasets(args)

    if args.dry_run:
        return _dry_run(selected, paths)

    failures: list[str] = []
    for key in selected:
        source = SOURCE_FILES[key]
        destination = source.local_path(paths)
        print(f"\nTéléchargement : {key}")
        print(f"Destination : {destination}")
        try:
            result = download_file(
                source.stable_url,
                destination,
                key=key,
                resume=args.resume,
                overwrite=args.overwrite,
                verify=verifier_for(source),
                max_retries=args.max_retries,
                show_progress=not args.no_progress,
            )
        except PipelineError as exc:
            print(exc.render())
            failures.append(key)
            continue
        verification = result.manifest.get("verification", {})
        extra = f" — {verification['rows']:,} lignes" if "rows" in verification else ""
        print(
            f"Terminé ({result.status}) : {human_bytes(result.bytes)}{extra}\n"
            f"SHA-256 : {result.sha256}\nManifeste : {result.manifest_path}"
        )

    print()
    if failures:
        print(f"Résultat : {len(failures)} échec(s) : {', '.join(failures)}.")
        return 1
    print("Résultat : tous les téléchargements sont terminés et vérifiés.")
    return 0


def _dry_run(selected: list[str], paths: ProjectPaths) -> int:
    import shutil

    total_needed = 0
    problems = 0
    for key in selected:
        source = SOURCE_FILES[key]
        destination = source.local_path(paths)
        try:
            info = probe_remote(source.stable_url)
        except PipelineError as exc:
            print(f"{key:35s} ERREUR — {exc.message}")
            problems += 1
            continue
        already = destination.exists()
        if not already:
            total_needed += info.total_bytes or 0
        print(
            f"{key:35s} {human_bytes(info.total_bytes):>12s}  "
            f"Range={'oui' if info.accept_ranges else 'NON'}  "
            f"{'déjà présent' if already else 'à télécharger'}"
        )
    paths.data.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(paths.data).free
    print(f"\nEspace requis ≈ {human_bytes(total_needed)} ; disponible : {human_bytes(free)}.")
    if total_needed * 1.05 > free:
        print("ATTENTION : espace disque probablement insuffisant.")
        problems += 1
    return 1 if problems else 0


if __name__ == "__main__":
    run_cli(main)
