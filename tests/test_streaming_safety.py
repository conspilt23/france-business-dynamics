"""Garde-fous de performance : le pipeline doit rester en mémoire BORNÉE sur les stocks (44 M / 96 M de lignes).

Mesures à l'origine de ces règles (12 M d'établissements synthétiques, Polars 1.44, 1 CPU) — voir docs/FINAL_VALIDATION.md :
* `is_in(liste de chaînes)` fait basculer toute la requête vers le moteur en mémoire (2 255 Mo vs 438 Mo) ;
* `replace_strict(dict)` ajoutait ~900 Mo (non streamable) ;
* `join` sans `maintain_order="left"` met en tampon le côté gauche (1,8 Go vs 0,9 Go) ;
* `collect()` n'utilise pas le streaming par défaut : il faut `collect(engine="streaming")`.
"""

from __future__ import annotations

import re

import polars as pl
import pytest

from france_business_dynamics.utils.config import PROJECT_ROOT
from france_business_dynamics.utils.exprs import equals_any, equals_none

pytestmark = pytest.mark.layer("A-unit")
SRC = PROJECT_ROOT / "src" / "france_business_dynamics"


@pytest.mark.parametrize("values", [["A", "F"], ["A"], ["x", "y", "z"], []])
def test_equals_any_and_none_match_is_in_semantics_including_nulls(values):
    frame = pl.DataFrame({"c": ["A", "F", "B", None, "x", "A"]})
    got_any = frame.select(equals_any(pl.col("c"), values)).to_series().to_list()
    expected_any = frame.select(pl.col("c").is_in(values).fill_null(False)).to_series().to_list()
    assert got_any == expected_any
    got_none = frame.select(equals_none(pl.col("c"), values)).to_series().to_list()
    assert got_none == [not v for v in expected_any]


def python_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_is_in_on_hot_paths():
    """`is_in` est interdit hors `analysis/` (petits agrégats) et `utils/exprs.py` (helper de remplacement)."""
    offenders = [str(p.relative_to(SRC)) for p in python_files()
                 if "analysis" not in p.parts and p.name != "exprs.py" and re.search(r"\.is_in\(", p.read_text(encoding="utf-8"))]
    assert offenders == [], f"is_in casse le streaming : utiliser utils.exprs.equals_any / equals_none ({offenders})"


def test_no_replace_strict_in_the_pipeline_over_large_frames():
    allowed = {"geography.py", "queries.py"}  # petites tables : COG (~35 000 lignes) et agrégats déjà réduits
    offenders = [str(p.relative_to(SRC)) for p in python_files()
                 if p.name not in allowed and re.search(r"\.replace_strict\(", p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders


def test_big_collects_use_the_streaming_engine():
    """Les modules qui agrègent de gros frames doivent appeler `collect(engine="streaming")`."""
    for relative in ("validation/quality.py", "validation/profile.py", "sql_contract.py", "quality_gate.py"):
        text = (SRC / relative).read_text(encoding="utf-8")
        assert not re.search(r"\.collect\(\)", text), f"{relative} : collect() sans moteur streaming"


def test_cog_join_keeps_the_left_order_and_materialises_the_small_reference():
    text = (SRC / "transformation" / "geography.py").read_text(encoding="utf-8")
    assert 'maintain_order="left"' in text and ".collect().lazy()" in text
