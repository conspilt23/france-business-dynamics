"""Configuration centrale : chemins, sources officielles, constantes d'analyse.

Les chemins sont résolus à l'exécution (`ProjectPaths.from_env()`), ce qui permet
de relocaliser les données (`FBD_DATA_DIR`) et de tester sans toucher au dépôt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
def _default_root() -> Path:
    # <racine>/src/france_business_dynamics/utils/config.py → racine = parents[3]
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    data: Path

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def processed(self) -> Path:
        return self.data / "processed"

    @property
    def reference(self) -> Path:
        return self.data / "reference"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @classmethod
    def from_env(cls) -> ProjectPaths:
        """`FBD_PROJECT_ROOT` et `FBD_DATA_DIR` permettent de relocaliser le projet."""
        root = Path(os.environ.get("FBD_PROJECT_ROOT") or _default_root()).resolve()
        data = Path(os.environ.get("FBD_DATA_DIR") or (root / "data")).resolve()
        return cls(root=root, data=data)

    def relative(self, path: Path) -> str:
        """Chemin logique et portable pour les logs et artefacts JSON.

        Les données peuvent être déplacées hors de la racine du dépôt via
        ``FBD_DATA_DIR`` (notamment pendant les tests). Dans ce cas, on
        conserve quand même un chemin relatif à la couche ``data`` afin de
        ne jamais exposer un chemin absolu propre à la machine dans les
        rapports versionnables.
        """
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            try:
                return (Path("data") / resolved.relative_to(self.data)).as_posix()
            except ValueError:
                return resolved.name


# Constantes de compatibilité (notebooks, scripts) — résolues à l'import.
_PATHS = ProjectPaths.from_env()
PROJECT_ROOT = _PATHS.root
DATA_DIR = _PATHS.data
RAW_DIR = _PATHS.raw
PROCESSED_DIR = _PATHS.processed
REFERENCE_DIR = _PATHS.reference

# --------------------------------------------------------------------------- #
# Période d'analyse et snapshot
# --------------------------------------------------------------------------- #
SNAPSHOT_DATE = "2026-10-01"  # date du stock SIRENE utilisé pour la vue courante
ANALYSIS_START_YEAR = 2021
ANALYSIS_END_YEAR = 2025
ANALYSIS_YEARS = tuple(range(ANALYSIS_START_YEAR, ANALYSIS_END_YEAR + 1))


def parse_snapshot_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Date de snapshot invalide : {value!r} (format attendu AAAA-MM-JJ)."
        ) from exc


def snapshot_date_key(value: str = SNAPSHOT_DATE) -> int:
    """Clé entière AAAAMMJJ utilisée comme `DateKey` dans SQL Server."""
    return int(parse_snapshot_date(value).strftime("%Y%m%d"))


# --------------------------------------------------------------------------- #
# Sentinelles des marts (documentées dans docs/data_dictionary.md)
# --------------------------------------------------------------------------- #
UNKNOWN_CODE = "UNKNOWN"  # code absent / non apparié ; 7 caractères → largeur SQL ≥ 10
UNKNOWN_STATUS = "U"
UNKNOWN_LABEL = "Non renseigné"

# Valeurs de `nomenclatureActivitePrincipale...` reconnues. Toute autre valeur est signalée.
NAF_REV2 = "NAFRev2"
KNOWN_NAF_NOMENCLATURES = ("NAFRev2", "NAFRev1", "NAF1993", "NAP", "NAF2025")


# --------------------------------------------------------------------------- #
# Sources officielles
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceFile:
    key: str
    stable_url: str
    filename: str
    kind: str = "parquet"  # parquet | csv
    expected_status: int = 302  # statut HTTP attendu sur l'URL stable (sans suivre la redirection)
    target: str = "raw"  # raw | reference
    description: str = ""

    def local_path(self, paths: ProjectPaths) -> Path:
        base = paths.reference if self.target == "reference" else paths.raw
        return base / self.filename


_DATA_GOUV = "https://www.data.gouv.fr/api/1/datasets/r/"

SOURCE_FILES: dict[str, SourceFile] = {
    "stock_unite_legale": SourceFile(
        "stock_unite_legale",
        _DATA_GOUV + "350182c9-148a-46e0-8389-76c2ec1374a3",
        "stock_unite_legale.parquet",
        description="SIRENE — stock des unités légales (état courant)",
    ),
    "stock_etablissement": SourceFile(
        "stock_etablissement",
        _DATA_GOUV + "a29c1297-1f92-4e2a-8f6b-8c902ce96c5f",
        "stock_etablissement.parquet",
        description="SIRENE — stock des établissements (état courant)",
    ),
    "stock_unite_legale_historique": SourceFile(
        "stock_unite_legale_historique",
        _DATA_GOUV + "1b9290ed-d0bc-461f-ba31-0250a99cc140",
        "stock_unite_legale_historique.parquet",
        description="SIRENE — historique des périodes des unités légales",
    ),
    "stock_etablissement_historique": SourceFile(
        "stock_etablissement_historique",
        _DATA_GOUV + "2b3a0c79-f97b-46b8-ac02-8be6c1f01a8c",
        "stock_etablissement_historique.parquet",
        description="SIRENE — historique des périodes des établissements",
    ),
    "cog_communes_2026": SourceFile(
        "cog_communes_2026",
        "https://www.insee.fr/fr/statistiques/fichier/8740222/v_commune_2026.csv",
        "v_commune_2026.csv",
        kind="csv",
        expected_status=200,
        target="reference",
        description="COG 2026 — communes (COM, DEP, REG)",
    ),
}

# Vue simple `clé → URL stable` conservée pour compatibilité.
SOURCES: dict[str, str] = {key: source.stable_url for key, source in SOURCE_FILES.items()}

SIRENE_KEYS = tuple(key for key in SOURCE_FILES if key.startswith("stock_"))
