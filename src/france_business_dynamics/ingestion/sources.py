"""Accès aux sources officielles : métadonnées HTTP et schémas Parquet (sans télécharger les corps)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests

from france_business_dynamics import __version__
from france_business_dynamics.ingestion.downloader import USER_AGENT
from france_business_dynamics.utils.errors import PipelineError

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def head_stable_url(
    stable_url: str,
    timeout: int | tuple[float, float] = 60,
    session: requests.Session | None = None,
) -> dict[str, str | int | None]:
    """Interroge une URL stable SANS suivre la redirection.

    Permet de distinguer : SIRENE → redirection (302) vers le stockage ; COG → fichier direct (200).
    """
    client = session or requests
    headers = {"User-Agent": USER_AGENT}
    response = client.head(stable_url, allow_redirects=False, timeout=timeout, headers=headers)
    if response.status_code in {405, 501}:
        response.close()
        response = client.get(
            stable_url, allow_redirects=False, stream=True, timeout=timeout, headers=headers
        )
    result = {
        "status_code": response.status_code,
        "content_length": response.headers.get("Content-Length"),
        "content_type": response.headers.get("Content-Type"),
        "location": response.headers.get("Location"),
    }
    response.close()
    return result


def resolve_url(stable_url: str, timeout: int = 60) -> str:
    """Suit les redirections et retourne l'URL finale (sans télécharger le corps)."""
    response = requests.get(
        stable_url,
        allow_redirects=True,
        stream=True,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
    )
    try:
        response.raise_for_status()
        return response.url
    finally:
        response.close()


def inspect_remote_parquet(stable_url: str) -> dict:
    """Lit uniquement le pied de page Parquet distant via requêtes HTTP Range."""
    import fsspec
    import pyarrow.parquet as pq

    try:
        final_url = resolve_url(stable_url)
        with fsspec.open(
            final_url, "rb", block_size=8 * 1024 * 1024, cache_type="readahead"
        ) as file_obj:
            parquet_file = pq.ParquetFile(file_obj)
            return _describe(parquet_file, resolved_url=final_url)
    except requests.RequestException as exc:
        raise PipelineError(
            f"Inspection distante impossible pour {stable_url} ({type(exc).__name__}: {exc}).",
            hint="Vérifier la connexion Internet, puis relancer.",
        ) from exc


def inspect_local_parquet(path: Path) -> dict:
    """Même description que `inspect_remote_parquet`, pour un fichier local."""
    import pyarrow.parquet as pq

    if not path.exists():
        raise PipelineError(
            f"Fichier introuvable : {path}",
            hint="Télécharger la source (python -m france_business_dynamics.ingestion.download_sources).",
        )
    result = _describe(pq.ParquetFile(path), resolved_url=None)
    result["file_bytes"] = path.stat().st_size
    return result


def _describe(parquet_file, *, resolved_url: str | None) -> dict:
    metadata = parquet_file.metadata
    schema = parquet_file.schema_arrow
    return {
        "resolved_url": resolved_url,
        "rows": metadata.num_rows,
        "row_groups": metadata.num_row_groups,
        "column_count": len(schema),
        "columns": [{"name": field.name, "type": str(field.type)} for field in schema],
        "created_by": metadata.created_by,
    }


def build_snapshot(datasets: dict[str, dict], stable_urls: dict[str, str]) -> dict:
    """Structure documentée dans docs/json_artifacts.md (schema_snapshot.json)."""
    return {
        "snapshot_generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "datasets": {
            key: {"stable_url": stable_urls.get(key), **meta} for key, meta in datasets.items()
        },
    }


def _columns_of(entry: dict) -> dict[str, str | None] | None:
    """nom → type, quel que soit le format du snapshot.

    * format actuel : `columns` = liste de {name, type} ;
    * ancien format (docs/schema_snapshot_2026-10-01.json) : `column_names` = liste de noms (types inconnus) ;
    * historiques de l'ancien format : seulement un nombre de colonnes → `None` (comparaison impossible).
    """
    columns = entry.get("columns")
    if isinstance(columns, list):
        return {c["name"]: c.get("type") for c in columns}
    names = entry.get("column_names")
    if isinstance(names, list):
        return dict.fromkeys(names)
    return None


def diff_snapshots(old: dict, new: dict) -> dict[str, dict]:
    """Compare deux snapshots : colonnes ajoutées/supprimées/changées de type + variation de lignes."""
    report: dict[str, dict] = {}
    old_sets, new_sets = old.get("datasets", {}), new.get("datasets", {})
    for key in sorted(set(old_sets) | set(new_sets)):
        before, after = old_sets.get(key), new_sets.get(key)
        if before is None or after is None:
            report[key] = {"status": "AJOUTÉ" if before is None else "SUPPRIMÉ"}
            continue
        rows_delta = (after.get("rows") or 0) - (before.get("rows") or 0)
        old_cols, new_cols = _columns_of(before), _columns_of(after)
        if old_cols is None or new_cols is None:
            count_before = before.get("columns") if isinstance(before.get("columns"), int) else None
            count_after = len(new_cols) if new_cols is not None else None
            report[key] = {
                "status": "COLONNES NON COMPARABLES",
                "note": "la référence ne contient que le nombre de colonnes"
                + (f" ({count_before} → {count_after})" if count_before is not None else ""),
                "added_columns": [], "removed_columns": [], "type_changes": {},
                "rows_delta": rows_delta,
                "column_count_changed": bool(count_before and count_after and count_before != count_after),
            }
            continue
        added = sorted(set(new_cols) - set(old_cols))
        removed = sorted(set(old_cols) - set(new_cols))
        retyped = sorted(
            c for c in set(old_cols) & set(new_cols)
            if old_cols[c] is not None and new_cols[c] is not None and old_cols[c] != new_cols[c]
        )
        drift = bool(added or removed or retyped)
        report[key] = {
            "status": "DÉRIVE DE SCHÉMA" if drift else "IDENTIQUE",
            "added_columns": added,
            "removed_columns": removed,
            "type_changes": {c: [old_cols[c], new_cols[c]] for c in retyped},
            "rows_delta": rows_delta,
        }
    return report
