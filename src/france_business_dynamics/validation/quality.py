"""Contrôles de qualité : moteur de vérifications réutilisable (quality gate, notebooks, tests).

Un contrôle (`Check`) a une sévérité :
* `error`   → un échec fait échouer le quality gate (intégrité : clés, formats, réconciliation, largeurs SQL) ;
* `warning` → un échec est signalé mais ne bloque pas (qualité statistique : appariement COG, formats atypiques).

Les seuils sont des constantes nommées, documentées dans docs/data_quality.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import polars as pl

from france_business_dynamics.transformation.geography import (
    GEO_STATUS_NOT_ENRICHED,
    GEO_STATUS_UNMATCHED,
)
from france_business_dynamics.transformation.workforce import WORKFORCE_LABELS
from france_business_dynamics.utils.exprs import equals_any, equals_none
from france_business_dynamics.utils.config import (
    KNOWN_NAF_NOMENCLATURES,
    NAF_REV2,
    SNAPSHOT_DATE,
    UNKNOWN_CODE,
    parse_snapshot_date,
)
from france_business_dynamics.transformation.naf import NAF_REV2_UNCLASSIFIED_CODE

# --- Seuils d'avertissement (part des lignes) -------------------------------------------------
UNMATCHED_GEO_WARN_RATE = 0.005  # > 0,5 % de codes commune absents du COG 2026
UNKNOWN_COMMUNE_WARN_RATE = 0.01  # > 1 % d'établissements sans commune dans le mart
INVALID_DATE_WARN_RATE = 0.0001  # > 0,01 % de dates de création invalides

COMMUNE_CODE_PATTERN = r"^(\d{5}|2[AB]\d{3})$"


@dataclass
class Check:
    id: str
    description: str
    status: str  # PASS | FAIL | WARN
    severity: str  # error | warning
    observed: Any
    expected: str

    def to_dict(self) -> dict:
        return asdict(self)


def make_check(
    check_id: str, description: str, ok: bool, *, severity: str, observed: Any, expected: str
) -> Check:
    status = "PASS" if ok else ("FAIL" if severity == "error" else "WARN")
    return Check(check_id, description, status, severity, observed, expected)


def count_check(
    check_id: str, description: str, observed: int | None, *, severity: str = "error",
    maximum: int = 0, expected: str | None = None,
) -> Check:
    observed = int(observed or 0)
    return make_check(
        check_id, description, observed <= maximum, severity=severity, observed=observed,
        expected=expected or f"≤ {maximum}",
    )


def rate_check(
    check_id: str, description: str, numerator: int | None, denominator: int, threshold: float,
    *, severity: str = "warning",
) -> Check:
    numerator = int(numerator or 0)
    rate = numerator / denominator if denominator else 0.0
    return make_check(
        check_id, description, rate <= threshold, severity=severity,
        observed={"count": numerator, "rate": round(rate, 6)}, expected=f"taux ≤ {threshold:.4%}",
    )


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def quality_report(frame: pl.LazyFrame, key_columns: list[str]) -> pl.DataFrame:
    """Métriques compactes (nulls et valeurs distinctes) — conservé pour compatibilité."""
    schema = frame.collect_schema()
    available = [column for column in key_columns if column in schema.names()]
    expressions = [pl.len().alias("row_count")]
    for column in available:
        expressions.append(pl.col(column).null_count().alias(f"{column}_nulls"))
        expressions.append(pl.col(column).n_unique().alias(f"{column}_distinct"))
    return frame.select(expressions).collect(engine="streaming")


DEFAULT_ROWS_PER_UNIQUENESS_PASS = 8_000_000


def duplicate_key_count(
    frame: pl.LazyFrame, key_columns: list[str], *, max_rows_per_pass: int = DEFAULT_ROWS_PER_UNIQUENESS_PASS
) -> int:
    """Nombre de LIGNES en excès par rapport à des clés uniques (0 = clé unique).

    Mémoire bornée : au-delà de `max_rows_per_pass` lignes, la clé est hachée et répartie en `k` paquets contrôlés
    l'un après l'autre (un paquet = au plus ~`max_rows_per_pass` clés en mémoire). Résultat identique à une passe
    unique : deux clés égales ont le même hachage, donc tombent dans le même paquet.
    """
    key = pl.col(key_columns[0]) if len(key_columns) == 1 else pl.struct(key_columns)
    rows = int(frame.select(pl.len()).collect(engine="streaming").item())
    passes = max(1, -(-rows // max_rows_per_pass))
    if passes == 1:
        row = frame.select(pl.len().alias("n"), key.n_unique().alias("u")).collect(engine="streaming").row(0)
        return int(row[0] - row[1])
    bucket = key.hash(seed=0) % passes
    excess = 0
    for index in range(passes):
        row = (
            frame.filter(bucket == index)
            .select(pl.len().alias("n"), key.n_unique().alias("u"))
            .collect(engine="streaming")
            .row(0)
        )
        excess += int(row[0] - row[1])
    return excess


def assert_unique_key(frame: pl.LazyFrame, key_columns: list[str]) -> None:
    duplicates = (
        frame.group_by(key_columns)
        .len(name="duplicate_count")
        .filter(pl.col("duplicate_count") > 1)
        .limit(1)
        .collect(engine="streaming")
    )
    if duplicates.height:
        raise ValueError(f"Duplicate key detected for {key_columns}: {duplicates}")


def _flag(expr: pl.Expr) -> pl.Expr:
    """Somme d'un booléen en traitant null comme faux."""
    return expr.fill_null(False).sum()


def _rx_invalid(column: str, pattern: str) -> pl.Expr:
    """Vrai si la valeur est absente ou ne respecte pas le motif."""
    return pl.col(column).str.contains(pattern).fill_null(False).not_()


# --------------------------------------------------------------------------- #
# Couches courantes
# --------------------------------------------------------------------------- #
ESTABLISHMENT_CURATED_REQUIRED = [
    "siren", "nic", "siret", "etatAdministratifEtablissement", "naf_nomenclature", "naf_section",
    "is_naf_rev2", "activitePrincipaleEtablissement", "dateCreationEtablissement", "date_creation_invalid",
    "codeCommuneEtablissement", "geo_status", "trancheEffectifsEtablissement",
]
LEGAL_UNIT_CURATED_REQUIRED = [
    "siren", "etatAdministratifUniteLegale", "naf_nomenclature", "naf_section", "is_naf_rev2",
    "activitePrincipaleUniteLegale", "dateCreationUniteLegale", "date_creation_invalid",
]


def _naf_checks(row: dict, rows: int, prefix: str) -> list[Check]:
    return [
        count_check(f"{prefix}.naf_section_consistency",
                    "Aucune section NAF sans nomenclature NAFRev2 bien formée",
                    row["naf_section_inconsistent"]),
        count_check(f"{prefix}.rev2_section_unmapped",
                    "Code NAFRev2 bien formé sans section officielle (hors 00.00Z)",
                    row["rev2_unmapped"], severity="warning"),
        count_check(f"{prefix}.no_section_outside_rev2",
                    "Aucune section NAF Rev.2 attribuée à NAFRev1 / NAF1993 / NAP / NAF2025",
                    row["section_outside_rev2"]),
        count_check(f"{prefix}.naf_nomenclature_known",
                    f"Nomenclature NAF dans {list(KNOWN_NAF_NOMENCLATURES)}",
                    row["nomenclature_unknown"], severity="warning"),
        count_check(f"{prefix}.rev2_code_format",
                    "Code NAFRev2 au format 99.99A (sinon section nulle)",
                    row["rev2_malformed"], severity="warning"),
    ]


def check_establishment_curated(frame: pl.LazyFrame) -> tuple[int, list[Check]]:
    schema = frame.collect_schema().names()
    missing = [c for c in ESTABLISHMENT_CURATED_REQUIRED if c not in schema]
    if missing:
        return 0, [make_check("etablissement.columns", "Colonnes de la couche courante", False,
                              severity="error", observed=missing, expected="colonnes présentes")]

    row = frame.select(
        pl.len().alias("rows"),
        _flag(_rx_invalid("siret", r"^\d{14}$")).alias("siret_invalid"),
        _flag(_rx_invalid("siren", r"^\d{9}$")).alias("siren_invalid"),
        _flag(_rx_invalid("nic", r"^\d{5}$")).alias("nic_invalid"),
        _flag(pl.col("siret") != (pl.col("siren") + pl.col("nic"))).alias("siret_mismatch"),
        pl.col("etatAdministratifEtablissement").is_null().sum().alias("etat_null"),
        _flag(equals_none(pl.col("etatAdministratifEtablissement"), ["A", "F"])
              & pl.col("etatAdministratifEtablissement").is_not_null()).alias("etat_unexpected"),
        _flag(pl.col("date_creation_invalid")).alias("date_invalid"),
        _flag(pl.col("dateCreationEtablissement").is_not_null()
              & (pl.col("dateCreationEtablissement") < pl.lit(parse_snapshot_date("1900-01-01")))).alias("date_before_1900"),
        _flag(pl.col("dateCreationEtablissement").is_not_null()
              & (pl.col("dateCreationEtablissement") > pl.lit(parse_snapshot_date(SNAPSHOT_DATE)))).alias("date_after_snapshot"),
        _flag(pl.col("naf_section").is_not_null() & pl.col("is_naf_rev2").not_()).alias("naf_section_inconsistent"),
        _flag(pl.col("is_naf_rev2") & pl.col("naf_section").is_null()
              & (pl.col("activitePrincipaleEtablissement") != NAF_REV2_UNCLASSIFIED_CODE)).alias("rev2_unmapped"),
        _flag(pl.col("naf_section").is_not_null() & (pl.col("naf_nomenclature") != NAF_REV2)).alias("section_outside_rev2"),
        _flag(pl.col("naf_nomenclature").is_not_null()
              & equals_none(pl.col("naf_nomenclature"), list(KNOWN_NAF_NOMENCLATURES))).alias("nomenclature_unknown"),
        _flag((pl.col("naf_nomenclature") == NAF_REV2) & pl.col("is_naf_rev2").not_()).alias("rev2_malformed"),
        _flag(pl.col("codeCommuneEtablissement").is_not_null()
              & pl.col("codeCommuneEtablissement").str.contains(COMMUNE_CODE_PATTERN).not_()).alias("commune_format"),
        _flag(pl.col("geo_status") == GEO_STATUS_UNMATCHED).alias("geo_unmatched"),
        _flag(pl.col("geo_status") == GEO_STATUS_NOT_ENRICHED).alias("geo_not_enriched"),
        _flag(pl.col("trancheEffectifsEtablissement").is_not_null()
              & equals_none(pl.col("trancheEffectifsEtablissement"), list(WORKFORCE_LABELS))).alias("workforce_unexpected"),
        _flag(pl.col("codeCommuneEtablissement") == UNKNOWN_CODE).alias("sentinel_leak"),
    ).collect(engine="streaming").row(0, named=True)

    rows = int(row["rows"])
    checks = [
        make_check("etablissement.not_empty", "La couche contient des lignes", rows > 0,
                   severity="error", observed=rows, expected="> 0"),
        count_check("etablissement.siret_unique", "SIRET unique (une ligne par établissement)",
                    duplicate_key_count(frame, ["siret"])),
        count_check("etablissement.siret_format", "SIRET = 14 chiffres", row["siret_invalid"]),
        count_check("etablissement.siren_format", "SIREN = 9 chiffres", row["siren_invalid"]),
        count_check("etablissement.nic_format", "NIC = 5 chiffres", row["nic_invalid"]),
        count_check("etablissement.siret_consistency", "SIRET = SIREN + NIC", row["siret_mismatch"]),
        count_check("etablissement.etat_values", "État administratif ∈ {A, F} (ou nul)", row["etat_unexpected"]),
        rate_check("etablissement.etat_null", "État administratif manquant", row["etat_null"], rows, 0.0),
        rate_check("etablissement.date_creation", "Dates de création invalides (renseignées mais illisibles)",
                   row["date_invalid"], rows, INVALID_DATE_WARN_RATE),
        count_check("etablissement.date_creation_before_1900",
                    "Dates de création antérieures au 01/01/1900", row["date_before_1900"], severity="warning"),
        count_check("etablissement.date_creation_after_snapshot",
                    f"Dates de création postérieures au snapshot ({SNAPSHOT_DATE})",
                    row["date_after_snapshot"], severity="warning"),
        count_check("etablissement.commune_format", "Code commune = 5 chiffres ou 2A/2B + 3 chiffres",
                    row["commune_format"], severity="warning"),
        count_check("etablissement.workforce_codes", "Tranche d'effectifs dans la nomenclature officielle",
                    row["workforce_unexpected"], severity="warning"),
        count_check("etablissement.no_sentinel", "Aucune sentinelle UNKNOWN dans la couche courante",
                    row["sentinel_leak"]),
        rate_check("etablissement.geo_unmatched", "Codes commune absents du COG 2026",
                   row["geo_unmatched"], rows, UNMATCHED_GEO_WARN_RATE),
        make_check("etablissement.geo_enriched", "Couche enrichie avec le COG 2026",
                   int(row["geo_not_enriched"] or 0) == 0, severity="warning",
                   observed=int(row["geo_not_enriched"] or 0), expected="0 ligne NOT_ENRICHED"),
        *_naf_checks(row, rows, "etablissement"),
    ]
    return rows, checks


def check_legal_unit_curated(frame: pl.LazyFrame) -> tuple[int, list[Check]]:
    schema = frame.collect_schema().names()
    missing = [c for c in LEGAL_UNIT_CURATED_REQUIRED if c not in schema]
    if missing:
        return 0, [make_check("unite_legale.columns", "Colonnes de la couche courante", False,
                              severity="error", observed=missing, expected="colonnes présentes")]
    row = frame.select(
        pl.len().alias("rows"),
        _flag(_rx_invalid("siren", r"^\d{9}$")).alias("siren_invalid"),
        pl.col("etatAdministratifUniteLegale").is_null().sum().alias("etat_null"),
        _flag(equals_none(pl.col("etatAdministratifUniteLegale"), ["A", "C"])
              & pl.col("etatAdministratifUniteLegale").is_not_null()).alias("etat_unexpected"),
        _flag(pl.col("date_creation_invalid")).alias("date_invalid"),
        _flag(pl.col("dateCreationUniteLegale").is_not_null()
              & (pl.col("dateCreationUniteLegale") < pl.lit(parse_snapshot_date("1900-01-01")))).alias("date_before_1900"),
        _flag(pl.col("dateCreationUniteLegale").is_not_null()
              & (pl.col("dateCreationUniteLegale") > pl.lit(parse_snapshot_date(SNAPSHOT_DATE)))).alias("date_after_snapshot"),
        _flag(pl.col("naf_section").is_not_null() & pl.col("is_naf_rev2").not_()).alias("naf_section_inconsistent"),
        _flag(pl.col("is_naf_rev2") & pl.col("naf_section").is_null()
              & (pl.col("activitePrincipaleUniteLegale") != NAF_REV2_UNCLASSIFIED_CODE)).alias("rev2_unmapped"),
        _flag(pl.col("naf_section").is_not_null() & (pl.col("naf_nomenclature") != NAF_REV2)).alias("section_outside_rev2"),
        _flag(pl.col("naf_nomenclature").is_not_null()
              & equals_none(pl.col("naf_nomenclature"), list(KNOWN_NAF_NOMENCLATURES))).alias("nomenclature_unknown"),
        _flag((pl.col("naf_nomenclature") == NAF_REV2) & pl.col("is_naf_rev2").not_()).alias("rev2_malformed"),
    ).collect(engine="streaming").row(0, named=True)
    rows = int(row["rows"])
    checks = [
        make_check("unite_legale.not_empty", "La couche contient des lignes", rows > 0,
                   severity="error", observed=rows, expected="> 0"),
        count_check("unite_legale.siren_unique", "SIREN unique (une ligne par unité légale)",
                    duplicate_key_count(frame, ["siren"])),
        count_check("unite_legale.siren_format", "SIREN = 9 chiffres", row["siren_invalid"]),
        count_check("unite_legale.etat_values", "État administratif ∈ {A, C} (ou nul)", row["etat_unexpected"]),
        rate_check("unite_legale.etat_null", "État administratif manquant", row["etat_null"], rows, 0.0),
        rate_check("unite_legale.date_creation", "Dates de création invalides",
                   row["date_invalid"], rows, INVALID_DATE_WARN_RATE),
        count_check("unite_legale.date_creation_before_1900",
                    "Dates de création antérieures au 01/01/1900", row["date_before_1900"], severity="warning"),
        count_check("unite_legale.date_creation_after_snapshot",
                    f"Dates de création postérieures au snapshot ({SNAPSHOT_DATE})",
                    row["date_after_snapshot"], severity="warning"),
        *_naf_checks(row, rows, "unite_legale"),
    ]
    return rows, checks


# --------------------------------------------------------------------------- #
# Marts (tables de faits)
# --------------------------------------------------------------------------- #
def contract_check(frame: pl.LazyFrame, contract, check_id: str) -> Check:
    """Le mart respecte-t-il le contrat SQL (colonnes, types, largeurs, nulls, ASCII) ?"""
    from france_business_dynamics.sql_contract import check_summary, summarize

    problems = check_summary(summarize(frame, contract), contract)
    return make_check(
        check_id,
        f"Conformité au contrat SQL {contract.qualified} (colonnes, types, largeurs, nulls)",
        not problems, severity="error",
        observed=[str(problem) for problem in problems[:10]] or "conforme",
        expected="aucune incompatibilité (sinon troncature / échec d'insertion SQL Server)",
    )


def check_mart(
    frame: pl.LazyFrame,
    *,
    prefix: str,
    contract,
    grain: list[str],
    count_column: str,
    status_values: list[str] | None = None,
    expected_total: int | None = None,
    expected_total_label: str = "",
    extra: list[tuple[str, str, pl.Expr, str, int]] | None = None,
) -> tuple[int, int, list[Check]]:
    """Contrôles communs aux marts. `extra` : (id, description, expression booléenne, sévérité, max)."""
    names = frame.collect_schema().names()
    required = [*grain, count_column, "naf_section", "naf_nomenclature"]
    missing = [c for c in required if c not in names]
    if missing:
        return 0, 0, [make_check(f"{prefix}.columns", "Colonnes du mart", False, severity="error",
                                 observed=missing, expected="colonnes présentes")]

    selects = [
        pl.len().alias("rows"),
        pl.col(count_column).sum().alias("total"),
        _flag(pl.col(count_column) <= 0).alias("non_positive"),
        _flag(pl.col("naf_section").is_not_null() & (pl.col("naf_nomenclature") != NAF_REV2)).alias("section_outside_rev2"),
    ]
    if status_values is not None:
        selects.append(_flag(equals_none(pl.col("status_code"), status_values)).alias("status_unexpected"))
    for index, (_, _, expr, _, _) in enumerate(extra or []):
        selects.append(_flag(expr).alias(f"extra_{index}"))
    row = frame.select(selects).collect(engine="streaming").row(0, named=True)

    rows, total = int(row["rows"]), int(row["total"] or 0)
    checks = [
        make_check(f"{prefix}.not_empty", "Le mart contient des lignes", rows > 0, severity="error",
                   observed=rows, expected="> 0"),
        count_check(f"{prefix}.grain_unique", f"Grain unique {grain}", duplicate_key_count(frame, grain)),
        count_check(f"{prefix}.positive_counts", "Mesures strictement positives", row["non_positive"]),
        count_check(f"{prefix}.no_section_outside_rev2",
                    "Aucune section NAF Rev.2 attribuée à une autre nomenclature", row["section_outside_rev2"]),
    ]
    if status_values is not None:
        checks.append(count_check(f"{prefix}.status_values", f"État ∈ {status_values}", row["status_unexpected"]))
    if expected_total is not None:
        checks.append(make_check(
            f"{prefix}.reconciliation", f"Réconciliation : somme des mesures = {expected_total_label}",
            total == expected_total, severity="error", observed=total, expected=str(expected_total)))
    for index, (check_id, description, _, severity, maximum) in enumerate(extra or []):
        checks.append(count_check(f"{prefix}.{check_id}", description, row[f"extra_{index}"],
                                  severity=severity, maximum=maximum))
    checks.append(contract_check(frame, contract, f"{prefix}.sql_contract"))
    return rows, total, checks
