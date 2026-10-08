"""Expressions Polars sûres pour le streaming.

`Expr.is_in(liste)` sur des chaînes fait basculer TOUTE la requête Polars 1.44 vers le moteur en mémoire
(mesuré : 2 255 Mo au lieu de 438 Mo sur 12 M de lignes, croissance linéaire avec le volume). Ces helpers
construisent des chaînes d'égalités, évaluées ligne à ligne, avec exactement la même sémantique (null → faux).
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl


def equals_any(column: pl.Expr, values: Iterable[str]) -> pl.Expr:
    """Équivalent streaming-safe de `column.is_in(values)` ; null → faux."""
    expression: pl.Expr | None = None
    for value in values:
        term = column == value
        expression = term if expression is None else (expression | term)
    if expression is None:  # liste vide : toujours faux, mais vectoriel (longueur de la colonne)
        return column.is_null() & column.is_not_null()
    return expression.fill_null(False)


def equals_none(column: pl.Expr, values: Iterable[str]) -> pl.Expr:
    """Équivalent de `~column.is_in(values)` ; null → vrai (comme `is_in(...).not_()` avec `fill_null(False)`)."""
    return equals_any(column, values).not_()
