"""Libellés des catégories juridiques (niveau I) et des catégories d'entreprise."""

from __future__ import annotations

# Nomenclature des catégories juridiques — niveau I (1er chiffre du code).
# Les libellés détaillés (niveaux II/III) ne sont pas embarqués : ils exigent le fichier officiel Insee.
LEGAL_CATEGORY_LEVEL1: dict[str, str] = {
    "1": "Entrepreneur individuel",
    "2": "Groupement de droit privé non doté de la personnalité morale",
    "3": "Personne morale de droit étranger",
    "4": "Personne morale de droit public soumise au droit commercial",
    "5": "Société commerciale",
    "6": "Autre personne morale immatriculée au RCS",
    "7": "Personne morale et organisme soumis au droit administratif",
    "8": "Organisme privé spécialisé",
    "9": "Groupement de droit privé",
}

ENTERPRISE_CATEGORY_LABELS: dict[str, str] = {
    "PME": "Petite ou moyenne entreprise",
    "ETI": "Entreprise de taille intermédiaire",
    "GE": "Grande entreprise",
}


def legal_category_level1(code: str | None) -> str | None:
    return code[0] if code and code[0].isdigit() else None
