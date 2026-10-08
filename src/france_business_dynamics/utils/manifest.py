"""Manifeste du pipeline (`data/processed/pipeline_manifest.json`).

Enregistre, pour chaque couche produite : fichier, nombre de lignes, `limit` éventuel (échantillon),
entrées utilisées (lignes + SHA-256 du téléchargement) et horodatage. Le quality gate s'en sert pour
distinguer un échantillon de 10 000 lignes d'un traitement complet.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from france_business_dynamics import __version__
from france_business_dynamics.utils.config import ProjectPaths
from france_business_dynamics.utils.jsonio import read_json, write_json_atomic

MANIFEST_NAME = "pipeline_manifest.json"


def manifest_path(paths: ProjectPaths) -> Path:
    return paths.processed / MANIFEST_NAME


def parquet_rows(path: Path) -> int:
    """Nombre de lignes via les métadonnées Parquet (lecture du pied de page uniquement)."""
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).metadata.num_rows


def read_manifest(paths: ProjectPaths) -> dict:
    target = manifest_path(paths)
    if target.exists():
        return read_json(target)
    return {"datasets": {}}


def describe_input(paths: ProjectPaths, path: Path) -> dict:
    """Entrée d'une couche : chemin, lignes (Parquet) et SHA-256 du téléchargement si connu."""
    info: dict = {"path": paths.relative(path)}
    if path.suffix == ".parquet" and path.exists():
        info["rows"] = parquet_rows(path)
    sidecar = path.with_name(path.name + ".json")
    if sidecar.exists():
        meta = read_json(sidecar)
        info["sha256"] = meta.get("sha256")
        info["downloaded_at_utc"] = meta.get("downloaded_at_utc")
    return info


def register_output(
    paths: ProjectPaths,
    name: str,
    output: Path,
    *,
    limit: int | None,
    inputs: dict[str, dict] | None = None,
    extra: dict | None = None,
) -> dict:
    entry = {
        "path": paths.relative(output),
        "rows": parquet_rows(output),
        "limit": limit,
        "scope": "ECHANTILLON" if limit is not None else "COMPLET",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "inputs": inputs or {},
        **(extra or {}),
    }
    manifest = read_manifest(paths)
    manifest.setdefault("datasets", {})[name] = entry
    manifest["updated_at_utc"] = entry["built_at_utc"]
    write_json_atomic(manifest_path(paths), manifest)
    return entry


def sink_atomic(frame: pl.LazyFrame, destination: Path) -> Path:
    """Écrit un Parquet via un fichier temporaire : jamais de fichier final à moitié écrit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        frame.sink_parquet(tmp, compression="zstd")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination
