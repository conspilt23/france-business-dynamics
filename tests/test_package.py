from __future__ import annotations

import tomllib

import pytest

from france_business_dynamics import __version__
from france_business_dynamics.utils.config import PROJECT_ROOT

pytestmark = pytest.mark.layer("A-unit")


def test_package_version_matches_pyproject():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"]


def test_package_is_imported_from_src_layout():
    import france_business_dynamics

    assert "src" in france_business_dynamics.__file__.replace("\\", "/")
