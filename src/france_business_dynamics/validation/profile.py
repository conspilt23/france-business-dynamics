"""Perfilage des sources (notebook 01) : schéma, nulls, doublons, distributions, dates, géographie.

Toutes les fonctions acceptent un `LazyFrame` et ne calculent que ce qui est demandé : sur le stock complet
(44 M de lignes) on sélectionne les colonnes utiles et, si besoin, on limite aux `n` premières lignes (`head`).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.utils.config import SNAPSHOT_DATE, parse_snapshot_date

from france_business_dynamics.transformation.geography import GEO_STATUS_UNMATCHED
from france_business_dynamics.validation.quality import COMMUNE_CODE_PATTERN


def parquet_overview(path: Path) -> dict:
    """Taille, lignes, groupes de lignes et schéma, lus dans le pied de page Parquet (sans charger les données)."""
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(path)
    metadata = parquet_file.metadata
    schema = parquet_file.schema_arrow
    return {
        "path": str(path),
        "file_mb": round(path.stat().st_size / 1024**2, 2),
        "rows": metadata.num_rows,
        "row_groups": metadata.num_row_groups,
        "columns": [{"name": f.name, "type": str(f.type)} for f in schema],
    }


def schema_comparison(frame: pl.LazyFrame, expected: list[str]) -> pl.DataFrame:
    """Colonnes attendues vs présentes : `statut` ∈ {présente, MANQUANTE}."""
    available = frame.collect_schema()
    return pl.DataFrame({
        "colonne": expected,
        "statut": ["présente" if c in available else "MANQUANTE" for c in expected],
        "type": [str(available[c]) if c in available else None for c in expected],
    })


def null_profile(frame: pl.LazyFrame, columns: list[str] | None = None) -> pl.DataFrame:
    """Nulls et chaînes vides par colonne, triés du plus au moins incomplet."""
    schema = frame.collect_schema()
    columns = [c for c in (columns or schema.names()) if c in schema]
    expressions = [pl.len().alias("__rows")]
    for column in columns:
        expressions.append(pl.col(column).null_count().alias(f"{column}__null"))
        if schema[column] == pl.String:
            expressions.append((pl.col(column).str.strip_chars() == "").sum().alias(f"{column}__blank"))
    row = frame.select(expressions).collect(engine="streaming").row(0, named=True)
    rows = row["__rows"]
    records = []
    for column in columns:
        nulls = int(row[f"{column}__null"] or 0)
        blanks = int(row.get(f"{column}__blank") or 0)
        records.append({
            "colonne": column, "type": str(schema[column]), "nulls": nulls, "vides": blanks,
            "pct_manquant": round((nulls + blanks) / rows * 100, 3) if rows else 0.0,
        })
    return pl.DataFrame(records).sort("pct_manquant", descending=True)


def duplicate_profile(frame: pl.LazyFrame, key: str) -> dict:
    """Lignes, valeurs distinctes de la clé et lignes en excès (0 = clé unique)."""
    row = frame.select(pl.len().alias("rows"), pl.col(key).n_unique().alias("distinct")).collect(engine="streaming").row(0, named=True)
    return {"key": key, "rows": row["rows"], "distinct": row["distinct"], "excess_rows": row["rows"] - row["distinct"]}


def value_distribution(frame: pl.LazyFrame, column: str, top_n: int = 20) -> pl.DataFrame:
    """Fréquences d'une colonne (null inclus), avec pourcentage."""
    counts = frame.group_by(column).len(name="lignes").sort("lignes", descending=True).collect(engine="streaming")
    total = counts["lignes"].sum()
    return counts.head(top_n).with_columns((pl.col("lignes") / total * 100).round(3).alias("pct"))


def date_profile(frame: pl.LazyFrame, column: str) -> dict:
    """Bornes, valeurs nulles et valeurs illisibles d'une colonne de dates (texte ISO ou type date)."""
    schema = frame.collect_schema()
    if schema[column] == pl.Date:
        parsed = pl.col(column)
        raw_present = pl.col(column).is_not_null()
    else:
        text = pl.col(column).cast(pl.String).str.strip_chars()
        parsed = text.str.strptime(pl.Date, "%Y-%m-%d", strict=False)
        raw_present = text.is_not_null() & (text != "")
    row = frame.select(
        pl.len().alias("rows"),
        (~raw_present).sum().alias("absent"),
        (raw_present & parsed.is_null()).sum().alias("invalid"),
        parsed.min().alias("min"),
        parsed.max().alias("max"),
        (parsed < pl.lit(parse_snapshot_date("1900-01-01"))).fill_null(False).sum().alias("before_1900"),
        (parsed > pl.lit(parse_snapshot_date(SNAPSHOT_DATE))).fill_null(False).sum().alias("after_snapshot"),
    ).collect(engine="streaming").row(0, named=True)
    return {"column": column, **row}


def geography_profile(frame: pl.LazyFrame, commune_column: str = "codeCommuneEtablissement") -> dict:
    """Format des codes commune, absents, Corse (2A/2B) et — si disponible — appariement COG."""
    schema = frame.collect_schema().names()
    expressions = [
        pl.len().alias("rows"),
        pl.col(commune_column).is_null().sum().alias("no_commune"),
        (pl.col(commune_column).is_not_null() & ~pl.col(commune_column).str.contains(COMMUNE_CODE_PATTERN))
        .sum().alias("bad_format"),
        pl.col(commune_column).str.starts_with("2A").sum().alias("corse_2a"),
        pl.col(commune_column).str.starts_with("2B").sum().alias("corse_2b"),
        pl.col(commune_column).str.starts_with("0").sum().alias("leading_zero"),
    ]
    if "geo_status" in schema:
        expressions.append((pl.col("geo_status") == GEO_STATUS_UNMATCHED).sum().alias("unmatched_cog"))
    return frame.select(expressions).collect(engine="streaming").row(0, named=True)


def sample_rows(frame: pl.LazyFrame, predicate: pl.Expr, n: int = 5, columns: list[str] | None = None) -> pl.DataFrame:
    """Exemples concrets (cas difficiles) : les `n` premières lignes qui satisfont `predicate`."""
    selected = frame.filter(predicate)
    if columns:
        selected = selected.select(columns)
    return selected.head(n).collect(engine="streaming")
