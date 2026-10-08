"""Utilitaires internes aux tests pour construire des entrées synthétiques cohérentes avec SIRENE.

Ces données servent uniquement aux tests automatisés du dépôt. Elles ne font pas partie du pipeline
utilisateur et ne sont jamais téléchargées ni publiées comme données du projet.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.utils.config import PROJECT_ROOT, SOURCE_FILES, ProjectPaths
from france_business_dynamics.utils.errors import PreconditionError

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# Colonnes complètes des schémas réels (stock : docs/schema_snapshot_2026-10-01.json ; historiques : 28 / 18).
UNITE_LEGALE_FULL = [
    "siren", "statutDiffusionUniteLegale", "unitePurgeeUniteLegale", "dateCreationUniteLegale",
    "sigleUniteLegale", "sexeUniteLegale", "prenom1UniteLegale", "prenom2UniteLegale",
    "prenom3UniteLegale", "prenom4UniteLegale", "prenomUsuelUniteLegale", "pseudonymeUniteLegale",
    "identifiantAssociationUniteLegale", "trancheEffectifsUniteLegale", "anneeEffectifsUniteLegale",
    "dateDernierTraitementUniteLegale", "nombrePeriodesUniteLegale", "categorieEntreprise",
    "anneeCategorieEntreprise", "dateDebut", "etatAdministratifUniteLegale", "nomUniteLegale",
    "nomUsageUniteLegale", "denominationUniteLegale", "denominationUsuelle1UniteLegale",
    "denominationUsuelle2UniteLegale", "denominationUsuelle3UniteLegale",
    "categorieJuridiqueUniteLegale", "activitePrincipaleUniteLegale",
    "nomenclatureActivitePrincipaleUniteLegale", "nicSiegeUniteLegale",
    "economieSocialeSolidaireUniteLegale", "societeMissionUniteLegale",
    "caractereEmployeurUniteLegale", "activitePrincipaleNAF25UniteLegale",
]
ETABLISSEMENT_FULL = [
    "siren", "nic", "siret", "statutDiffusionEtablissement", "dateCreationEtablissement",
    "trancheEffectifsEtablissement", "anneeEffectifsEtablissement",
    "activitePrincipaleRegistreMetiersEtablissement", "dateDernierTraitementEtablissement",
    "etablissementSiege", "nombrePeriodesEtablissement", "complementAdresseEtablissement",
    "numeroVoieEtablissement", "indiceRepetitionEtablissement", "dernierNumeroVoieEtablissement",
    "indiceRepetitionDernierNumeroVoieEtablissement", "typeVoieEtablissement",
    "libelleVoieEtablissement", "codePostalEtablissement", "libelleCommuneEtablissement",
    "libelleCommuneEtrangerEtablissement", "distributionSpecialeEtablissement",
    "codeCommuneEtablissement", "codeCedexEtablissement", "libelleCedexEtablissement",
    "codePaysEtrangerEtablissement", "libellePaysEtrangerEtablissement",
    "identifiantAdresseEtablissement", "coordonneeLambertAbscisseEtablissement",
    "coordonneeLambertOrdonneeEtablissement", "complementAdresse2Etablissement",
    "numeroVoie2Etablissement", "indiceRepetition2Etablissement", "typeVoie2Etablissement",
    "libelleVoie2Etablissement", "codePostal2Etablissement", "libelleCommune2Etablissement",
    "libelleCommuneEtranger2Etablissement", "distributionSpeciale2Etablissement",
    "codeCommune2Etablissement", "codeCedex2Etablissement", "libelleCedex2Etablissement",
    "codePaysEtranger2Etablissement", "libellePaysEtranger2Etablissement", "dateDebut",
    "etatAdministratifEtablissement", "enseigne1Etablissement", "enseigne2Etablissement",
    "enseigne3Etablissement", "denominationUsuelleEtablissement", "activitePrincipaleEtablissement",
    "nomenclatureActivitePrincipaleEtablissement", "caractereEmployeurEtablissement",
    "activitePrincipaleNAF25Etablissement",
]
UNITE_LEGALE_HIST_FULL = [
    "siren", "dateFin", "dateDebut", "etatAdministratifUniteLegale",
    "changementEtatAdministratifUniteLegale", "nomUniteLegale", "changementNomUniteLegale",
    "nomUsageUniteLegale", "changementNomUsageUniteLegale", "denominationUniteLegale",
    "changementDenominationUniteLegale", "denominationUsuelle1UniteLegale",
    "denominationUsuelle2UniteLegale", "denominationUsuelle3UniteLegale",
    "changementDenominationUsuelleUniteLegale", "categorieJuridiqueUniteLegale",
    "changementCategorieJuridiqueUniteLegale", "activitePrincipaleUniteLegale",
    "nomenclatureActivitePrincipaleUniteLegale", "changementActivitePrincipaleUniteLegale",
    "nicSiegeUniteLegale", "changementNicSiegeUniteLegale", "economieSocialeSolidaireUniteLegale",
    "changementEconomieSocialeSolidaireUniteLegale", "societeMissionUniteLegale",
    "changementSocieteMissionUniteLegale", "caractereEmployeurUniteLegale",
    "changementCaractereEmployeurUniteLegale",
]
ETABLISSEMENT_HIST_FULL = [
    "siren", "nic", "siret", "dateFin", "dateDebut", "etatAdministratifEtablissement",
    "changementEtatAdministratifEtablissement", "enseigne1Etablissement", "enseigne2Etablissement",
    "enseigne3Etablissement", "changementEnseigneEtablissement", "denominationUsuelleEtablissement",
    "changementDenominationUsuelleEtablissement", "activitePrincipaleEtablissement",
    "nomenclatureActivitePrincipaleEtablissement", "changementActivitePrincipaleEtablissement",
    "caractereEmployeurEtablissement", "changementCaractereEmployeurEtablissement",
]

INT_COLUMNS = {
    "anneeEffectifsUniteLegale", "nombrePeriodesUniteLegale", "anneeCategorieEntreprise",
    "anneeEffectifsEtablissement", "nombrePeriodesEtablissement",
}
BOOL_COLUMNS = {
    "unitePurgeeUniteLegale", "etablissementSiege",
    *(c for c in UNITE_LEGALE_HIST_FULL + ETABLISSEMENT_HIST_FULL if c.startswith("changement")),
}
DATE_COLUMNS = {"dateCreationUniteLegale", "dateCreationEtablissement", "dateDebut", "dateFin"}

SPECS = {
    "stock_unite_legale": ("unite_legale_sample.csv", UNITE_LEGALE_FULL),
    "stock_etablissement": ("etablissement_sample.csv", ETABLISSEMENT_FULL),
    "stock_unite_legale_historique": ("unite_legale_historique_sample.csv", UNITE_LEGALE_HIST_FULL),
    "stock_etablissement_historique": ("etablissement_historique_sample.csv", ETABLISSEMENT_HIST_FULL),
}


def _typed(column: str, dtype_mode: str) -> pl.Expr:
    col = pl.col(column)
    if column in BOOL_COLUMNS:
        return (
            pl.when(col.is_null()).then(None).otherwise(col.str.to_lowercase() == "true").alias(column)
        )
    if column in INT_COLUMNS:
        return col.cast(pl.Int64, strict=False).alias(column)
    if column in DATE_COLUMNS and dtype_mode == "date":
        return col.str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias(column)
    return col.alias(column)


def load_fixture_frame(csv_name: str, full_columns: list[str], date_format: str = "string") -> pl.DataFrame:
    """CSV de fixture → DataFrame au schéma COMPLET (colonnes non fournies = null)."""
    path = FIXTURES_DIR / csv_name
    if not path.exists():
        raise PreconditionError(
            f"Fixture introuvable : {path}",
            hint="Exécuter depuis une copie complète du dépôt (le dossier tests/fixtures est requis).",
        )
    sample = pl.read_csv(path, infer_schema=False, null_values=[""])
    expressions = []
    for column in full_columns:
        if column in sample.columns:
            expressions.append(_typed(column, date_format))
        elif column in BOOL_COLUMNS:
            expressions.append(pl.lit(None, dtype=pl.Boolean).alias(column))
        elif column in INT_COLUMNS:
            expressions.append(pl.lit(None, dtype=pl.Int64).alias(column))
        elif column in DATE_COLUMNS and date_format == "date":
            expressions.append(pl.lit(None, dtype=pl.Date).alias(column))
        else:
            expressions.append(pl.lit(None, dtype=pl.String).alias(column))
    unknown = [c for c in sample.columns if c not in full_columns]
    if unknown:
        raise PreconditionError(f"{csv_name} : colonnes hors schéma réel {unknown}.")
    return sample.select(expressions)


def build_test_inputs(paths: ProjectPaths, *, date_format: str = "string") -> dict[str, Path]:
    """Écrit les 4 Parquet bruts + le COG dans `paths`. `date_format` : `string` ou `date`."""
    if date_format not in {"string", "date"}:
        raise ValueError("date_format doit valoir 'string' ou 'date'.")
    written: dict[str, Path] = {}
    paths.raw.mkdir(parents=True, exist_ok=True)
    paths.reference.mkdir(parents=True, exist_ok=True)
    for key, (csv_name, columns) in SPECS.items():
        target = SOURCE_FILES[key].local_path(paths)
        load_fixture_frame(csv_name, columns, date_format).write_parquet(target, compression="zstd")
        written[key] = target
    cog_target = SOURCE_FILES["cog_communes_2026"].local_path(paths)
    cog_target.write_bytes((FIXTURES_DIR / "cog_communes_sample.csv").read_bytes())
    written["cog_communes_2026"] = cog_target
    return written


def run_test_pipeline(paths: ProjectPaths, *, date_format: str = "string", log=print) -> dict:
    """Entrées synthétiques de test → transform → marts → quality gate (strict)."""
    from france_business_dynamics.build_mart import run_build_mart
    from france_business_dynamics.quality_gate import run_quality_gate
    from france_business_dynamics.transform import run_transform

    build_test_inputs(paths, date_format=date_format)
    run_transform(paths, "both", log=log)
    run_build_mart(paths, log=log)
    return run_quality_gate(paths, strict=True, log=log)
