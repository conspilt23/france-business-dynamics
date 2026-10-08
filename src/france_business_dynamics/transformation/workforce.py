"""Tranches d'effectifs SIRENE : code → libellé (nomenclature officielle Insee)."""

from __future__ import annotations

import polars as pl

from france_business_dynamics.utils.config import UNKNOWN_CODE, UNKNOWN_LABEL

# Ordre croissant de taille : sert de clé de tri dans les visuels Power BI.
WORKFORCE_LABELS: dict[str, str] = {
    "NN": "Non employeur",
    "00": "0 salarié",
    "01": "1–2 salariés",
    "02": "3–5 salariés",
    "03": "6–9 salariés",
    "11": "10–19 salariés",
    "12": "20–49 salariés",
    "21": "50–99 salariés",
    "22": "100–199 salariés",
    "31": "200–249 salariés",
    "32": "250–499 salariés",
    "41": "500–999 salariés",
    "42": "1 000–1 999 salariés",
    "51": "2 000–4 999 salariés",
    "52": "5 000–9 999 salariés",
    "53": "10 000 salariés et plus",
}
WORKFORCE_SORT_ORDER: dict[str, int] = {code: i for i, code in enumerate(WORKFORCE_LABELS, start=1)}
WORKFORCE_SORT_ORDER[UNKNOWN_CODE] = 99


def workforce_label_expression(source_column: str) -> pl.Expr:
    """Libellé de tranche, sous forme d'une chaîne `when/then`.

    Volontairement PAS `replace_strict(dict)` : mesuré sur 6 M de lignes, `replace_strict` n'est pas exécuté
    en streaming par Polars 1.44 et ajoutait ~900 Mo de mémoire pic (croissance linéaire avec le volume).
    Une chaîne `when/then` est évaluée ligne à ligne (voir docs/FINAL_VALIDATION.md, test d'échelle).
    """
    code = pl.col(source_column).cast(pl.String)
    chain: pl.Expr | pl.Then = pl.lit(None, dtype=pl.String)
    for key, label in reversed(list(WORKFORCE_LABELS.items())):
        chain = pl.when(code == key).then(pl.lit(label)).otherwise(chain)
    return chain


def add_workforce_label(
    frame: pl.LazyFrame, source_column: str, output_column: str = "workforce_label"
) -> pl.LazyFrame:
    """Libellé de tranche ; code inconnu ou absent → libellé nul (rendu `Non renseigné` dans les marts)."""
    return frame.with_columns(workforce_label_expression(source_column).alias(output_column))


def label_or_unknown(code: str) -> str:
    return WORKFORCE_LABELS.get(code, UNKNOWN_LABEL)
