"""Oracle INDÉPENDANT : recalcule en Python pur (module `csv`, aucun Polars) les résultats attendus.

Sert à vérifier le pipeline Polars contre une seconde implémentation écrite à la main, à partir
des mêmes CSV de fixtures. Les sections NAF attendues sont codées en dur d'après la structure
officielle de la NAF Rev.2 (et non importées du code testé).
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import date
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
WINDOW = (2021, 2025)

# Sections attendues pour les codes NAF Rev.2 présents dans les fixtures (écrites à la main).
EXPECTED_SECTION = {
    "01.11Z": "A", "47.11A": "G", "47.71Z": "G", "46.90Z": "G", "56.10A": "I", "56.30Z": "I",
    "62.01Z": "J", "70.10Z": "M", "84.11Z": "O", "94.99Z": "S", "99.00Z": "U",
}


def rows(name: str) -> list[dict]:
    with (FIXTURES / name).open(encoding="utf-8", newline="") as handle:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(handle)]


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def section(nomenclature: str | None, code: str | None) -> str | None:
    """Section attendue : seulement NAFRev2 et seulement pour un code connu des fixtures."""
    if nomenclature != "NAFRev2" or code is None:
        return None
    return EXPECTED_SECTION.get(code)


def cog() -> dict[str, dict]:
    """code commune → {name, dep, reg} avec la règle : COM > ARM > COMA > COMD, héritage du parent."""
    priority = {"COM": 0, "ARM": 1, "COMA": 2, "COMD": 3}
    raw = rows("cog_communes_sample.csv")
    parents = {r["COM"]: r for r in raw if r["TYPECOM"] == "COM"}
    best: dict[str, dict] = {}
    for r in sorted(raw, key=lambda r: (r["COM"], priority.get(r["TYPECOM"], 9))):
        if r["COM"] in best:
            continue
        parent = parents.get(r["COMPARENT"]) if r["COMPARENT"] else None
        best[r["COM"]] = {
            "name": r["LIBELLE"],
            "dep": r["DEP"] or (parent["DEP"] if parent else None),
            "reg": r["REG"] or (parent["REG"] if parent else None),
        }
    return best


def establishment_mart() -> dict[tuple, int]:
    """(commune_code, naf_nomenclature, naf_code, workforce_code, status_code) → nombre d'établissements."""
    counter: Counter = Counter()
    for r in rows("etablissement_sample.csv"):
        counter[(
            r["codeCommuneEtablissement"] or "UNKNOWN",
            r["nomenclatureActivitePrincipaleEtablissement"] or "UNKNOWN",
            r["activitePrincipaleEtablissement"] or "UNKNOWN",
            r["trancheEffectifsEtablissement"] or "UNKNOWN",
            r["etatAdministratifEtablissement"] or "U",
        )] += 1
    return dict(counter)


def legal_unit_mart() -> dict[tuple, int]:
    """(naf_nomenclature, naf_code, legal_category, enterprise_category, status) → nombre d'unités légales."""
    counter: Counter = Counter()
    for r in rows("unite_legale_sample.csv"):
        counter[(
            r["nomenclatureActivitePrincipaleUniteLegale"] or "UNKNOWN",
            r["activitePrincipaleUniteLegale"] or "UNKNOWN",
            r["categorieJuridiqueUniteLegale"] or "UNKNOWN",
            r["categorieEntreprise"] or "UNKNOWN",
            r["etatAdministratifUniteLegale"] or "U",
        )] += 1
    return dict(counter)


def _in_window(d: date | None) -> bool:
    return d is not None and WINDOW[0] <= d.year <= WINDOW[1]


def events() -> dict[tuple, int]:
    """(année, type d'événement, nomenclature, code) → nombre d'événements."""
    counter: Counter = Counter()
    for r in rows("unite_legale_historique_sample.csv"):
        d = parse_date(r["dateDebut"])
        if r["changementEtatAdministratifUniteLegale"] == "true" and r["etatAdministratifUniteLegale"] == "C" and _in_window(d):
            counter[(d.year, "Cessation administrative",
                     r["nomenclatureActivitePrincipaleUniteLegale"] or "UNKNOWN",
                     r["activitePrincipaleUniteLegale"] or "UNKNOWN")] += 1
    for r in rows("etablissement_historique_sample.csv"):
        d = parse_date(r["dateDebut"])
        if r["changementEtatAdministratifEtablissement"] == "true" and r["etatAdministratifEtablissement"] == "F" and _in_window(d):
            counter[(d.year, "Fermeture administrative",
                     r["nomenclatureActivitePrincipaleEtablissement"] or "UNKNOWN",
                     r["activitePrincipaleEtablissement"] or "UNKNOWN")] += 1
    for r in rows("etablissement_sample.csv"):
        d = parse_date(r["dateCreationEtablissement"])
        if _in_window(d):
            counter[(d.year, "Date de création enregistrée",
                     r["nomenclatureActivitePrincipaleEtablissement"] or "UNKNOWN",
                     r["activitePrincipaleEtablissement"] or "UNKNOWN")] += 1
    return dict(counter)
