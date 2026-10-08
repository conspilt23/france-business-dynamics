"""Nomenclature d'activités : sections NAF Rev.2 UNIQUEMENT pour la nomenclature `NAFRev2`.

Règle méthodologique (docs/methodology.md) : les codes `NAFRev1`, `NAF1993`, `NAP` et
`NAF2025` ne sont jamais mappés vers des sections NAF Rev.2 — leurs divisions n'ont pas
la même signification (ex. la division 72 est « Activités informatiques » en NAF Rev.1
mais « Recherche-développement » en NAF Rev.2). Aucune table de conversion non fournie
par les données n'est inventée : la section reste nulle hors NAFRev2.
"""

from __future__ import annotations

import polars as pl

from france_business_dynamics.utils.config import NAF_REV2

# Division NAF Rev.2 (2 chiffres) → section (21 sections officielles).
SECTION_RANGES: dict[str, tuple[int, int]] = {
    "A": (1, 3), "B": (5, 9), "C": (10, 33), "D": (35, 35), "E": (36, 39),
    "F": (41, 43), "G": (45, 47), "H": (49, 53), "I": (55, 56), "J": (58, 63),
    "K": (64, 66), "L": (68, 68), "M": (69, 75), "N": (77, 82), "O": (84, 84),
    "P": (85, 85), "Q": (86, 88), "R": (90, 93), "S": (94, 96), "T": (97, 98),
    "U": (99, 99),
}

SECTION_LABELS: dict[str, str] = {
    "A": "Agriculture, sylviculture et pêche",
    "B": "Industries extractives",
    "C": "Industrie manufacturière",
    "D": "Production et distribution d'électricité, de gaz, de vapeur et d'air conditionné",
    "E": "Eau, assainissement, gestion des déchets et dépollution",
    "F": "Construction",
    "G": "Commerce ; réparation d'automobiles et de motocycles",
    "H": "Transports et entreposage",
    "I": "Hébergement et restauration",
    "J": "Information et communication",
    "K": "Activités financières et d'assurance",
    "L": "Activités immobilières",
    "M": "Activités spécialisées, scientifiques et techniques",
    "N": "Activités de services administratifs et de soutien",
    "O": "Administration publique",
    "P": "Enseignement",
    "Q": "Santé humaine et action sociale",
    "R": "Arts, spectacles et activités récréatives",
    "S": "Autres activités de services",
    "T": "Activités des ménages en tant qu'employeurs",
    "U": "Activités extra-territoriales",
}

# Format d'un code NAF Rev.2 : 2 chiffres, point, 2 chiffres, 1 lettre (ex. 47.11A).
NAF_REV2_PATTERN = r"^\d{2}\.\d{2}[A-Z]$"

# Code SIRENE utilisé lorsque l'activité principale n'est pas encore classée.
# Il respecte formellement le motif NAF Rev.2 mais ne correspond à aucune section économique.
NAF_REV2_UNCLASSIFIED_CODE = "00.00Z"


def section_for_division(division: int) -> str | None:
    """Fonction pure (utilisée par les tests et la documentation)."""
    for section, (low, high) in SECTION_RANGES.items():
        if low <= division <= high:
            return section
    return None


def is_naf_rev2_expr(code_column: str, nomenclature_column: str | None) -> pl.Expr:
    """Vrai si la nomenclature déclarée est NAFRev2 ET que le code a le format Rev.2.

    Sans colonne de nomenclature (ex. tests unitaires d'une expression isolée), seul le
    format du code est contrôlé.
    """
    well_formed = pl.col(code_column).cast(pl.String).str.contains(NAF_REV2_PATTERN)
    if nomenclature_column is None:
        return well_formed.fill_null(False)
    declared = pl.col(nomenclature_column).cast(pl.String).str.strip_chars() == NAF_REV2
    return (declared & well_formed).fill_null(False)


def naf_section_expression(code_column: str, nomenclature_column: str | None = None) -> pl.Expr:
    """Expression Polars : section NAF Rev.2, ou nulle si hors NAFRev2 / code mal formé."""
    division = pl.col(code_column).cast(pl.String).str.slice(0, 2).cast(pl.Int32, strict=False)
    section = pl.lit(None, dtype=pl.String)
    for letter, (low, high) in SECTION_RANGES.items():
        section = pl.when(division.is_between(low, high)).then(pl.lit(letter)).otherwise(section)
    return (
        pl.when(is_naf_rev2_expr(code_column, nomenclature_column))
        .then(section)
        .otherwise(pl.lit(None, dtype=pl.String))
    )


def add_naf_section(
    frame: pl.LazyFrame,
    source_column: str,
    output_column: str = "naf_section",
    nomenclature_column: str | None = None,
) -> pl.LazyFrame:
    """Ajoute `output_column` (section) et `is_naf_rev2` à la couche."""
    return frame.with_columns(
        naf_section_expression(source_column, nomenclature_column).alias(output_column),
        is_naf_rev2_expr(source_column, nomenclature_column).alias("is_naf_rev2"),
    )
