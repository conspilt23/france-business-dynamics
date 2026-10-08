"""Colonnes retenues par couche (contrat d'entrée) et contrôle de présence avant lecture.

Les colonnes sont volontairement limitées : aucune colonne source n'est conservée « au cas où ».
Justification par colonne : docs/data_dictionary.md.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from france_business_dynamics.utils.errors import PreconditionError, SchemaError

UNITE_LEGALE_COLUMNS = [
    "siren",
    "dateCreationUniteLegale",
    "categorieEntreprise",
    "categorieJuridiqueUniteLegale",
    "etatAdministratifUniteLegale",
    "trancheEffectifsUniteLegale",
    "anneeEffectifsUniteLegale",
    "activitePrincipaleUniteLegale",
    "nomenclatureActivitePrincipaleUniteLegale",
    "activitePrincipaleNAF25UniteLegale",
    "dateDebut",
    "nombrePeriodesUniteLegale",
    "caractereEmployeurUniteLegale",
    "economieSocialeSolidaireUniteLegale",
    "societeMissionUniteLegale",
]

ETABLISSEMENT_COLUMNS = [
    "siren",
    "nic",
    "siret",
    "dateCreationEtablissement",
    "trancheEffectifsEtablissement",
    "anneeEffectifsEtablissement",
    "etablissementSiege",
    "codePostalEtablissement",
    "libelleCommuneEtablissement",
    "codeCommuneEtablissement",
    "dateDebut",
    "etatAdministratifEtablissement",
    "activitePrincipaleEtablissement",
    "nomenclatureActivitePrincipaleEtablissement",
    "caractereEmployeurEtablissement",
    "activitePrincipaleNAF25Etablissement",
]

# Historiques : on ne lit que ce qui sert à reconstruire les changements d'état administratif.
UNIT_LEGALE_HISTORICAL_COLUMNS = [
    "siren",
    "dateFin",
    "dateDebut",
    "etatAdministratifUniteLegale",
    "changementEtatAdministratifUniteLegale",
    "activitePrincipaleUniteLegale",
    "nomenclatureActivitePrincipaleUniteLegale",
]

ETABLISSEMENT_HISTORICAL_COLUMNS = [
    "siren",
    "nic",
    "siret",
    "dateFin",
    "dateDebut",
    "etatAdministratifEtablissement",
    "changementEtatAdministratifEtablissement",
    "activitePrincipaleEtablissement",
    "nomenclatureActivitePrincipaleEtablissement",
]


def require_columns(path: Path, required: list[str], *, dataset: str) -> None:
    """Échoue avant tout calcul si le Parquet ne contient pas les colonnes attendues.

    Protège contre une évolution du schéma Insee : message clair plutôt que `ColumnNotFoundError`.
    """
    if not path.exists():
        raise PreconditionError(
            f"Couche requise absente : {path}",
            hint="Consulter docs/execution_checklist.md pour l'ordre des étapes.",
        )
    available = pl.scan_parquet(path).collect_schema().names()
    missing = [column for column in required if column not in available]
    if missing:
        raise SchemaError(
            f"{dataset} : colonnes attendues absentes de {path.name} : {missing}.",
            hint="Comparer avec le schéma officiel : "
            "python -m france_business_dynamics.ingestion.inspect_schema --local --compare "
            "docs/schema_snapshot_2026-10-01.json",
        )
