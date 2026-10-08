"""Vérifications d'intégrité d'un fichier téléchargé (sans le charger en mémoire)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path

from france_business_dynamics.utils.errors import PipelineError

PARQUET_MAGIC = b"PAR1"


def sha256_file(
    path: Path,
    *,
    chunk_size: int = 8 * 1024 * 1024,
    progress: Callable[[int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            if progress:
                progress(len(chunk))
    return digest.hexdigest()


def verify_parquet(path: Path) -> dict:
    """Vérifie les octets magiques `PAR1` (début et fin) puis lit le pied de page Parquet.

    Un fichier tronqué n'a pas de pied de page valide : il est refusé ici même si
    le serveur a annoncé une taille cohérente.
    """
    import pyarrow.parquet as pq

    size = path.stat().st_size
    if size < 12:
        raise PipelineError(f"{path.name} : fichier trop petit pour être un Parquet ({size} octets).")
    with path.open("rb") as handle:
        head = handle.read(4)
        handle.seek(-4, 2)
        tail = handle.read(4)
    if head != PARQUET_MAGIC or tail != PARQUET_MAGIC:
        raise PipelineError(
            f"{path.name} : octets magiques Parquet absents (début={head!r}, fin={tail!r}) "
            "— fichier incomplet ou page d'erreur HTML.",
            hint="Relancer le téléchargement avec --overwrite.",
        )
    try:
        metadata = pq.ParquetFile(path).metadata
    except Exception as exc:  # pyarrow lève plusieurs types selon la corruption
        raise PipelineError(
            f"{path.name} : pied de page Parquet illisible ({exc}).",
            hint="Relancer le téléchargement avec --overwrite.",
        ) from exc
    return {
        "kind": "parquet",
        "rows": metadata.num_rows,
        "row_groups": metadata.num_row_groups,
        "columns": metadata.num_columns,
    }


def make_csv_verifier(required_columns: Iterable[str], *, separator: str = ",") -> Callable[[Path], dict]:
    required = tuple(required_columns)

    def verify_csv(path: Path) -> dict:
        with path.open("rb") as handle:
            first = handle.readline()
        text = first.decode("utf-8-sig", errors="replace").strip()
        if not text or text.startswith("<"):
            raise PipelineError(
                f"{path.name} : en-tête CSV absent ou page HTML reçue à la place du fichier.",
                hint="Relancer le téléchargement avec --overwrite.",
            )
        header = [name.strip().strip('"') for name in text.split(separator)]
        missing = [name for name in required if name not in header]
        if missing:
            raise PipelineError(
                f"{path.name} : colonnes attendues absentes {missing} (trouvées : {header[:12]}…).",
                hint="Vérifier que l'URL pointe bien vers le fichier officiel.",
            )
        return {"kind": "csv", "columns": header}

    return verify_csv
