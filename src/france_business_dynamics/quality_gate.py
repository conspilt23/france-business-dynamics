"""Quality gate : contrôles par couche, avec dépendances explicites et rapport JSON.

    python -m france_business_dynamics.quality_gate                 # contrôle ce qui existe
    python -m france_business_dynamics.quality_gate --stage curated # uniquement les couches courantes
    python -m france_business_dynamics.quality_gate --strict        # toute couche manquante = échec

Comportement : une couche absente est `SKIPPED` avec la commande qui la produit (jamais un crash
obscur). `--strict` exige les 5 couches (à utiliser avant le chargement SQL). Un échantillon
(`--limit`) est signalé comme tel : un PASS sur 10 000 lignes n'est pas un PASS complet.

Codes de sortie : 0 = PASS · 1 = au moins un contrôle d'erreur en échec · 2 = rien à contrôler.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from france_business_dynamics import __version__
from france_business_dynamics.sql_contract import BY_NAME
from france_business_dynamics.transformation.historical import (
    ENTITY_ESTABLISHMENT,
    ENTITY_LEGAL_UNIT,
    EVENT_CESSATION,
    EVENT_CLOSURE,
    EVENT_CREATION_DATE,
)
from france_business_dynamics.transformation.marts import (
    ESTABLISHMENT_GRAIN,
    EVENTS_GRAIN,
    LEGAL_UNIT_GRAIN,
)
from france_business_dynamics.utils.config import (
    ANALYSIS_END_YEAR,
    ANALYSIS_START_YEAR,
    SNAPSHOT_DATE,
    UNKNOWN_CODE,
    ProjectPaths,
    snapshot_date_key,
)
from france_business_dynamics.utils.errors import PreconditionError, run_cli
from france_business_dynamics.utils.exprs import equals_none
from france_business_dynamics.utils.jsonio import write_json_atomic
from france_business_dynamics.utils.manifest import parquet_rows, read_manifest
from france_business_dynamics.validation.quality import (
    UNKNOWN_COMMUNE_WARN_RATE,
    Check,
    check_establishment_curated,
    check_legal_unit_curated,
    check_mart,
    make_check,
)


@dataclass(frozen=True)
class Layer:
    name: str
    stage: str  # curated | marts
    filename: str
    produced_by: str


LAYERS: tuple[Layer, ...] = (
    Layer("etablissement_current", "curated", "etablissement_current.parquet",
          "python -m france_business_dynamics.transform --dataset etablissement"),
    Layer("unite_legale_current", "curated", "unite_legale_current.parquet",
          "python -m france_business_dynamics.transform --dataset unite-legale"),
    Layer("fact_establishment_snapshot", "marts", "fact_establishment_snapshot.parquet",
          "python -m france_business_dynamics.build_mart --skip-events --skip-legal-units"),
    Layer("fact_legal_unit_snapshot", "marts", "fact_legal_unit_snapshot.parquet",
          "python -m france_business_dynamics.build_mart --skip-events --skip-establishments"),
    Layer("fact_administrative_events", "marts", "fact_administrative_events.parquet",
          "python -m france_business_dynamics.build_mart --skip-legal-units --skip-establishments"),
)
LAYER_BY_NAME = {layer.name: layer for layer in LAYERS}


def _scope(entry: dict | None) -> str:
    if not entry:
        return "INCONNU (manifeste absent)"
    if entry.get("limit") is not None:
        return f"ECHANTILLON (limit={entry['limit']:,})"
    return "COMPLET"


def _curated_rows(paths: ProjectPaths, name: str) -> int | None:
    path = paths.processed / LAYER_BY_NAME[name].filename
    return parquet_rows(path) if path.exists() else None


def _expected_total(paths: ProjectPaths, curated_name: str, mart_entry: dict | None) -> tuple[int | None, str]:
    """Total attendu d'un mart = lignes de sa couche courante (limitées si le mart est un échantillon)."""
    curated_rows = _curated_rows(paths, curated_name)
    if curated_rows is None:
        return None, ""
    limit = (mart_entry or {}).get("limit")
    expected = min(curated_rows, limit) if limit is not None else curated_rows
    return expected, f"lignes de {curated_name} ({expected:,})"


def check_layer(paths: ProjectPaths, layer: Layer, manifest: dict) -> tuple[int, list[Check]]:
    path = paths.processed / layer.filename
    frame = pl.scan_parquet(path)
    entry = manifest.get("datasets", {}).get(layer.name)

    if layer.name == "etablissement_current":
        return check_establishment_curated(frame)
    if layer.name == "unite_legale_current":
        return check_legal_unit_curated(frame)

    if layer.name == "fact_establishment_snapshot":
        total, label = _expected_total(paths, "etablissement_current", entry)
        rows, _, checks = check_mart(
            frame, prefix="mart_etablissement", contract=BY_NAME["EstablishmentSnapshot"],
            grain=ESTABLISHMENT_GRAIN, count_column="establishment_count",
            status_values=["A", "F", "U"], expected_total=total, expected_total_label=label,
            extra=[
                ("date_key", f"date_key = {snapshot_date_key()} (snapshot {SNAPSHOT_DATE})",
                 pl.col("date_key") != snapshot_date_key(), "error", 0),
            ],
        )
        if rows:
            unknown = frame.filter(pl.col("commune_code") == UNKNOWN_CODE).select(
                pl.col("establishment_count").sum()).collect(engine="streaming").item() or 0
            grand = frame.select(pl.col("establishment_count").sum()).collect(engine="streaming").item() or 0
            rate = unknown / grand if grand else 0.0
            checks.append(make_check(
                "mart_etablissement.unknown_commune", "Établissements sans commune (sentinelle UNKNOWN)",
                rate <= UNKNOWN_COMMUNE_WARN_RATE, severity="warning",
                observed={"count": int(unknown), "rate": round(rate, 6)},
                expected=f"taux ≤ {UNKNOWN_COMMUNE_WARN_RATE:.2%}"))
        if total is None:
            checks.append(make_check(
                "mart_etablissement.reconciliation", "Réconciliation avec la couche courante", False,
                severity="warning", observed="etablissement_current.parquet absent",
                expected="couche courante présente pour réconcilier"))
        return rows, checks

    if layer.name == "fact_legal_unit_snapshot":
        total, label = _expected_total(paths, "unite_legale_current", entry)
        rows, _, checks = check_mart(
            frame, prefix="mart_unite_legale", contract=BY_NAME["LegalUnitSnapshot"],
            grain=LEGAL_UNIT_GRAIN, count_column="legal_unit_count",
            status_values=["A", "C", "U"], expected_total=total, expected_total_label=label,
            extra=[("date_key", f"date_key = {snapshot_date_key()}",
                    pl.col("date_key") != snapshot_date_key(), "error", 0)],
        )
        return rows, checks

    # fact_administrative_events
    rows, _, checks = check_mart(
        frame, prefix="mart_evenements", contract=BY_NAME["AdministrativeEvents"],
        grain=EVENTS_GRAIN, count_column="event_count",
        extra=[
            ("year_window", f"Année d'événement dans {ANALYSIS_START_YEAR}–{ANALYSIS_END_YEAR}",
             ~pl.col("event_year").is_between(ANALYSIS_START_YEAR, ANALYSIS_END_YEAR), "error", 0),
            ("event_types", "Types d'événement et d'entité connus",
             equals_none(pl.col("event_type"), [EVENT_CESSATION, EVENT_CLOSURE, EVENT_CREATION_DATE])
             | equals_none(pl.col("entity_type"), [ENTITY_LEGAL_UNIT, ENTITY_ESTABLISHMENT]), "error", 0),
            ("event_entity_pairing", "Cessation ↔ unité légale ; fermeture/création ↔ établissement",
             ((pl.col("event_type") == EVENT_CESSATION) & (pl.col("entity_type") != ENTITY_LEGAL_UNIT))
             | ((pl.col("event_type") != EVENT_CESSATION) & (pl.col("entity_type") != ENTITY_ESTABLISHMENT)),
             "error", 0),
        ],
    )
    return rows, checks


def run_quality_gate(
    paths: ProjectPaths,
    *,
    stage: str = "all",
    strict: bool = False,
    output: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    selected = [layer for layer in LAYERS if stage == "all" or layer.stage == stage]
    manifest = read_manifest(paths)

    layers_report: dict[str, dict] = {}
    n_checks = n_pass = n_fail = n_warn = 0
    skipped: list[str] = []
    executed = 0

    for layer in selected:
        path = paths.processed / layer.filename
        entry = manifest.get("datasets", {}).get(layer.name)
        if not path.exists():
            skipped.append(layer.name)
            status = "FAIL" if strict else "SKIPPED"
            layers_report[layer.name] = {
                "status": status,
                "path": paths.relative(path),
                "reason": "couche absente",
                "produce_with": layer.produced_by,
            }
            log(f"[{status:7s}] {layer.name} — couche absente → {layer.produced_by}")
            if strict:
                n_fail += 1
            continue

        executed += 1
        try:
            rows, checks = check_layer(paths, layer, manifest)
        except Exception as exc:  # noqa: BLE001 - un fichier illisible doit être un FAIL explicite
            checks = [make_check(f"{layer.name}.readable", "Couche lisible", False, severity="error",
                                 observed=f"{type(exc).__name__}: {exc}", expected="Parquet lisible")]
            rows = 0
        failed = [c for c in checks if c.status == "FAIL"]
        warned = [c for c in checks if c.status == "WARN"]
        n_checks += len(checks)
        n_pass += sum(c.status == "PASS" for c in checks)
        n_fail += len(failed)
        n_warn += len(warned)
        layers_report[layer.name] = {
            "status": "FAIL" if failed else "PASS",
            "path": paths.relative(path),
            "rows": rows,
            "scope": _scope(entry),
            "warnings": len(warned),
            "checks": [c.to_dict() for c in checks],
        }
        log(f"[{'FAIL' if failed else 'PASS':7s}] {layer.name} — {rows:,} lignes — {_scope(entry)} — "
            f"{len(checks)} contrôles, {len(failed)} échec(s), {len(warned)} avertissement(s)")
        for check in failed + warned:
            log(f"           {check.status}  {check.id} : {check.description}\n"
                f"                  observé = {check.observed}  |  attendu : {check.expected}")

    if executed == 0 and not strict:
        raise PreconditionError(
            f"Aucune couche à contrôler dans {paths.processed}.",
            hint="Exécuter d'abord : python -m france_business_dynamics.transform puis build_mart "
            "(voir docs/execution_checklist.md).",
        )

    overall = "FAIL" if n_fail else "PASS"
    sample_layers = [n for n, r in layers_report.items() if str(r.get("scope", "")).startswith("ECHANTILLON")]
    payload = {
        "status": overall,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "mode": "strict" if strict else "partial",
        "stage": stage,
        "complete": not skipped,
        "contains_samples": bool(sample_layers),
        "summary": {
            "checks": n_checks, "passed": n_pass, "failed": n_fail, "warnings": n_warn,
            "layers_checked": executed, "layers_skipped": skipped, "sample_layers": sample_layers,
        },
        "layers": layers_report,
    }
    target = output or (paths.processed / "quality_report.json")
    write_json_atomic(target, payload)

    log("")
    log(f"Quality gate : {overall}  ({n_pass}/{n_checks} contrôles réussis, {n_fail} échec(s), {n_warn} avertissement(s))")
    if skipped:
        log(f"Couches non contrôlées : {', '.join(skipped)}")
    if sample_layers:
        log(f"Attention : échantillon(s) — {', '.join(sample_layers)} ; ce résultat ne vaut pas validation complète.")
    log(f"Rapport : {target}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["curated", "marts", "all"], default="all")
    parser.add_argument("--strict", action="store_true", help="Toute couche manquante est un échec.")
    parser.add_argument("--output", type=Path, help="Chemin du quality_report.json.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    report = run_quality_gate(ProjectPaths.from_env(), stage=args.stage, strict=args.strict, output=args.output)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    run_cli(main)
