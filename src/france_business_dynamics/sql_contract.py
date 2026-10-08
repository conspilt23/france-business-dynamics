"""Contrat de staging SQL Server : source unique de vérité des colonnes, types, largeurs, nullabilité.

Trois usages, une seule définition :
1. **pré-contrôle hors ligne** avant tout INSERT (largeurs, nulls, types, ASCII des `varchar`) ;
2. **contrôle du schéma vivant** (`INFORMATION_SCHEMA`) : détecte une ancienne table trop étroite ;
3. **test d'alignement** avec `sql/04_create_facts.sql` : le DDL et ce contrat ne peuvent pas diverger.

Pourquoi : l'erreur historique `String data, right truncation: length 14 buffer 6` venait d'une
colonne `varchar` trop étroite. Le correctif durable n'est pas d'élargir UNE colonne mais de rendre
toute incompatibilité détectable AVANT l'insertion, avec le nom de la table et de la colonne.

Règle de largeur : les colonnes de staging sont volontairement LARGES et permissives (le contrôle
métier vit dans le quality gate et dans les contraintes `analytics.*`). Les largeurs réellement
observables sont documentées dans docs/data_dictionary.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from france_business_dynamics.transformation.geo_labels import DEPARTMENT_LABELS, REGION_LABELS
from france_business_dynamics.transformation.legal import LEGAL_CATEGORY_LEVEL1
from france_business_dynamics.transformation.naf import SECTION_LABELS
from france_business_dynamics.transformation.workforce import WORKFORCE_LABELS, WORKFORCE_SORT_ORDER

STRING_TYPES = ("varchar", "nvarchar", "char")
INT_RANGES = {
    "smallint": (-(2**15), 2**15 - 1),
    "int": (-(2**31), 2**31 - 1),
    "bigint": (-(2**63), 2**63 - 1),
}


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str  # varchar | nvarchar | char | smallint | int | bigint
    length: int | None = None
    nullable: bool = False

    @property
    def is_string(self) -> bool:
        return self.sql_type in STRING_TYPES

    def ddl(self) -> str:
        base = f"{self.sql_type}({self.length})" if self.length else self.sql_type
        return f"{self.name} {base} {'NULL' if self.nullable else 'NOT NULL'}"


@dataclass(frozen=True)
class TableContract:
    name: str  # nom dans le schéma stg
    columns: tuple[Column, ...]
    parquet: str | None = None  # fichier du mart (data/processed) ; None = référentiel Python
    description: str = ""
    reference_loader: str | None = None  # clé de `reference_frames()`

    @property
    def qualified(self) -> str:
        return f"stg.{self.name}"

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)


@dataclass(frozen=True)
class Problem:
    table: str
    column: str | None
    kind: str  # missing_column | unexpected_column | type | width | null | range | non_ascii | ...
    message: str

    def __str__(self) -> str:  # pragma: no cover - mise en forme
        where = f"{self.table}.{self.column}" if self.column else self.table
        return f"[{self.kind}] {where} : {self.message}"


# --------------------------------------------------------------------------- #
# Définitions des tables de staging
# --------------------------------------------------------------------------- #
def _c(name, sql_type, length=None, nullable=False) -> Column:
    return Column(name, sql_type, length, nullable)


STAGING_FACTS: tuple[TableContract, ...] = (
    TableContract(
        "EstablishmentSnapshot",
        (
            _c("date_key", "int"),
            _c("commune_code", "varchar", 10),  # COG : 5 ; sentinelle UNKNOWN : 7
            _c("commune_name", "nvarchar", 200),
            _c("department_code", "varchar", 10),  # 2-3 caractères ; UNKNOWN : 7
            _c("region_code", "varchar", 10),
            _c("geo_status", "varchar", 20),  # MATCHED | UNMATCHED | NO_COMMUNE | NOT_ENRICHED
            _c("naf_nomenclature", "varchar", 20),  # NAFRev2, NAFRev1, NAF1993, NAP, UNKNOWN…
            _c("naf_code", "varchar", 20),  # NAF Rev.2 : 6 (47.11A) ; UNKNOWN : 7
            _c("naf_section", "char", 1, nullable=True),  # nulle hors NAFRev2
            _c("workforce_code", "varchar", 10),
            _c("workforce_label", "nvarchar", 100),
            _c("status_code", "varchar", 5),  # A | F | U (contrôlé en analytics)
            _c("establishment_count", "bigint"),
        ),
        parquet="fact_establishment_snapshot.parquet",
        description="Établissements par commune × activité × tranche d'effectifs × état.",
    ),
    TableContract(
        "LegalUnitSnapshot",
        (
            _c("date_key", "int"),
            _c("naf_nomenclature", "varchar", 20),
            _c("naf_code", "varchar", 20),
            _c("naf_section", "char", 1, nullable=True),
            _c("legal_category_code", "varchar", 10),  # 4 chiffres ; UNKNOWN : 7
            _c("enterprise_category", "varchar", 10),  # PME | ETI | GE | UNKNOWN
            _c("status_code", "varchar", 5),
            _c("legal_unit_count", "bigint"),
        ),
        parquet="fact_legal_unit_snapshot.parquet",
        description="Unités légales par activité × catégorie juridique × catégorie d'entreprise × état.",
    ),
    TableContract(
        "AdministrativeEvents",
        (
            _c("event_year", "smallint"),
            _c("entity_type", "nvarchar", 40),
            _c("event_type", "nvarchar", 60),
            _c("naf_nomenclature", "varchar", 20),
            _c("naf_code", "varchar", 20),
            _c("naf_section", "char", 1, nullable=True),
            _c("event_count", "bigint"),
        ),
        parquet="fact_administrative_events.parquet",
        description="Changements d'état administratif et dates de création enregistrées, par année.",
    ),
)

STAGING_REFERENCES: tuple[TableContract, ...] = (
    TableContract(
        "RefRegion",
        (_c("region_code", "varchar", 10), _c("region_name", "nvarchar", 100)),
        reference_loader="region",
        description="Libellés des régions (référentiel statique du dépôt).",
    ),
    TableContract(
        "RefDepartment",
        (_c("department_code", "varchar", 10), _c("department_name", "nvarchar", 100)),
        reference_loader="department",
        description="Libellés des départements.",
    ),
    TableContract(
        "RefNafSection",
        (_c("naf_section", "char", 1), _c("naf_section_label", "nvarchar", 150)),
        reference_loader="naf_section",
        description="Libellés des 21 sections NAF Rev.2.",
    ),
    TableContract(
        "RefLegalCategoryLevel1",
        (_c("level1_code", "char", 1), _c("level1_label", "nvarchar", 100)),
        reference_loader="legal_level1",
        description="Catégories juridiques — niveau I.",
    ),
    TableContract(
        "RefWorkforce",
        (
            _c("workforce_code", "varchar", 10),
            _c("workforce_label", "nvarchar", 100),
            _c("sort_order", "smallint"),
        ),
        reference_loader="workforce",
        description="Tranches d'effectifs et ordre de tri.",
    ),
)

ALL_STAGING: tuple[TableContract, ...] = STAGING_FACTS + STAGING_REFERENCES
BY_NAME: dict[str, TableContract] = {t.name: t for t in ALL_STAGING}


def reference_frames() -> dict[str, pl.DataFrame]:
    """Référentiels construits depuis les constantes Python (source unique des libellés)."""
    return {
        "region": pl.DataFrame(
            {"region_code": list(REGION_LABELS), "region_name": list(REGION_LABELS.values())}
        ),
        "department": pl.DataFrame(
            {"department_code": list(DEPARTMENT_LABELS), "department_name": list(DEPARTMENT_LABELS.values())}
        ),
        "naf_section": pl.DataFrame(
            {"naf_section": list(SECTION_LABELS), "naf_section_label": list(SECTION_LABELS.values())}
        ),
        "legal_level1": pl.DataFrame(
            {"level1_code": list(LEGAL_CATEGORY_LEVEL1), "level1_label": list(LEGAL_CATEGORY_LEVEL1.values())}
        ),
        "workforce": pl.DataFrame(
            {
                "workforce_code": list(WORKFORCE_LABELS),
                "workforce_label": list(WORKFORCE_LABELS.values()),
                "sort_order": [WORKFORCE_SORT_ORDER[c] for c in WORKFORCE_LABELS],
            },
            schema_overrides={"sort_order": pl.Int16},
        ),
    }


# --------------------------------------------------------------------------- #
# Pré-contrôle d'un jeu de données par rapport au contrat (hors ligne)
# --------------------------------------------------------------------------- #
@dataclass
class ColumnStats:
    nulls: int = 0
    max_len: int | None = None
    non_ascii: int = 0
    min_value: int | None = None
    max_value: int | None = None


@dataclass
class FrameSummary:
    rows: int
    dtypes: dict[str, pl.DataType]
    stats: dict[str, ColumnStats] = field(default_factory=dict)


def summarize(frame: pl.LazyFrame, contract: TableContract) -> FrameSummary:
    """Une seule passe (lazy) : lignes, nulls, longueur max, non-ASCII, min/max entiers."""
    schema = frame.collect_schema()
    present = [c for c in contract.columns if c.name in schema.names()]
    exprs: list[pl.Expr] = [pl.len().alias("__rows")]
    for col in present:
        exprs.append(pl.col(col.name).null_count().alias(f"{col.name}__nulls"))
        dtype = schema[col.name]
        if col.is_string and dtype == pl.String:
            exprs.append(pl.col(col.name).str.len_chars().max().alias(f"{col.name}__maxlen"))
            if col.sql_type in ("varchar", "char"):
                exprs.append(
                    pl.col(col.name).str.contains(r"[^\x00-\x7F]").sum().alias(f"{col.name}__nonascii")
                )
        elif col.sql_type in INT_RANGES and dtype.is_integer():
            exprs.append(pl.col(col.name).min().alias(f"{col.name}__min"))
            exprs.append(pl.col(col.name).max().alias(f"{col.name}__max"))
    row = frame.select(exprs).collect(engine="streaming").row(0, named=True)
    summary = FrameSummary(rows=int(row["__rows"]), dtypes={n: schema[n] for n in schema.names()})
    for col in present:
        stats = ColumnStats(nulls=int(row[f"{col.name}__nulls"] or 0))
        stats.max_len = row.get(f"{col.name}__maxlen")
        stats.non_ascii = int(row.get(f"{col.name}__nonascii") or 0)
        stats.min_value = row.get(f"{col.name}__min")
        stats.max_value = row.get(f"{col.name}__max")
        summary.stats[col.name] = stats
    return summary


def check_summary(summary: FrameSummary, contract: TableContract) -> list[Problem]:
    """Compare un résumé de données au contrat : colonnes, types, nulls, largeurs, plages, ASCII."""
    problems: list[Problem] = []
    table = contract.qualified
    expected = {c.name: c for c in contract.columns}
    actual_names = list(summary.dtypes)

    for name in expected:
        if name not in summary.dtypes:
            problems.append(Problem(table, name, "missing_column", "colonne attendue absente des données"))
    for name in actual_names:
        if name not in expected:
            problems.append(
                Problem(table, name, "unexpected_column",
                        "colonne absente du contrat SQL (ajouter à sql_contract.py et au DDL, ou la retirer du mart)")
            )

    for name, col in expected.items():
        if name not in summary.dtypes:
            continue
        dtype = summary.dtypes[name]
        stats = summary.stats.get(name, ColumnStats())

        if dtype == pl.Null:  # colonne entièrement nulle
            if not col.nullable:
                problems.append(Problem(table, name, "null",
                                        f"colonne entièrement nulle ({summary.rows:,} ligne(s)) dans une colonne NOT NULL"))
            continue  # anulable : sans risque d'insertion
        if col.is_string and dtype != pl.String:
            problems.append(Problem(table, name, "type", f"chaîne attendue, {dtype} reçu"))
            continue
        if col.sql_type in INT_RANGES and not dtype.is_integer():
            problems.append(Problem(table, name, "type", f"entier attendu, {dtype} reçu"))
            continue

        if not col.nullable and stats.nulls:
            problems.append(
                Problem(table, name, "null", f"{stats.nulls:,} valeur(s) nulle(s) dans une colonne NOT NULL")
            )
        if col.is_string and stats.max_len is not None and col.length and stats.max_len > col.length:
            problems.append(
                Problem(table, name, "width",
                        f"longueur max observée {stats.max_len} > largeur SQL {col.length} "
                        f"({col.sql_type}({col.length})) — troncature garantie")
            )
        if col.sql_type in ("varchar", "char") and stats.non_ascii:
            problems.append(
                Problem(table, name, "non_ascii",
                        f"{stats.non_ascii:,} valeur(s) non ASCII dans une colonne {col.sql_type} (utiliser nvarchar)")
            )
        if col.sql_type in INT_RANGES and stats.min_value is not None:
            low, high = INT_RANGES[col.sql_type]
            if stats.min_value < low or stats.max_value > high:
                problems.append(
                    Problem(table, name, "range",
                            f"valeurs [{stats.min_value}, {stats.max_value}] hors plage {col.sql_type} [{low}, {high}]")
                )
    return problems


def preflight_parquet(path: Path, contract: TableContract) -> tuple[FrameSummary, list[Problem]]:
    summary = summarize(pl.scan_parquet(path), contract)
    return summary, check_summary(summary, contract)


def preflight_reference(contract: TableContract) -> tuple[FrameSummary, list[Problem]]:
    frame = reference_frames()[contract.reference_loader].lazy()
    summary = summarize(frame, contract)
    return summary, check_summary(summary, contract)


# --------------------------------------------------------------------------- #
# Contrôle du schéma vivant (INFORMATION_SCHEMA.COLUMNS)
# --------------------------------------------------------------------------- #
def check_live_schema(
    contract: TableContract,
    live_columns: list[tuple[str, str, int | None, str]],
) -> list[Problem]:
    """`live_columns` : (COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE).

    * type exact attendu ; largeur vivante ≥ largeur du contrat ; nullabilité identique.
    * `CHARACTER_MAXIMUM_LENGTH = -1` (max) est toujours accepté.
    """
    table = contract.qualified
    if not live_columns:
        return [Problem(table, None, "missing_table",
                        "table absente — exécuter sql/04_create_facts.sql avant le chargement")]
    live = {name: (dtype, length, nullable) for name, dtype, length, nullable in live_columns}
    problems: list[Problem] = []
    for col in contract.columns:
        if col.name not in live:
            problems.append(Problem(table, col.name, "missing_column",
                                    "colonne absente de la table SQL (ancien schéma ?)"))
            continue
        dtype, length, nullable = live[col.name]
        if dtype.lower() != col.sql_type:
            problems.append(Problem(table, col.name, "type", f"type SQL {dtype} ≠ {col.sql_type} attendu"))
            continue
        if col.is_string and col.length and length not in (None, -1) and length < col.length:
            problems.append(
                Problem(table, col.name, "width",
                        f"{dtype}({length}) trop étroit : {col.length} requis (ancien schéma ?)")
            )
        if (nullable.upper() == "YES") != col.nullable:
            problems.append(
                Problem(table, col.name, "nullability",
                        f"nullabilité SQL ({nullable}) ≠ contrat ({'NULL' if col.nullable else 'NOT NULL'})")
            )
    for name in live:
        if name not in {c.name for c in contract.columns}:
            problems.append(Problem(table, name, "unexpected_column", "colonne SQL absente du contrat"))
    return problems
