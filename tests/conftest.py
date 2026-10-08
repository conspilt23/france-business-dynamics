"""Configuration pytest : visibilité de la progression, sans dépendance supplémentaire.

Affiche, pour chaque test :  [007/052 |  13.5%] PASS  [A-unit] tests/test_x.py::test_y
Aucun `sys.path` : le paquet doit être installé (`python -m pip install -e .`).
"""

from __future__ import annotations

import pytest


class ProgressPlugin:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.total = 0
        self.done = 0
        self.layers: dict[str, str] = {}

    # --- collecte -------------------------------------------------------------
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.total = len(session.items)
        self.done = 0
        self.layers = {}
        for item in session.items:
            marker = item.get_closest_marker("layer")
            self.layers[item.nodeid] = marker.args[0] if marker and marker.args else "?"
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if reporter and self.total:
            counts: dict[str, int] = {}
            for layer in self.layers.values():
                counts[layer] = counts.get(layer, 0) + 1
            detail = ", ".join(f"{name}={n}" for name, n in sorted(counts.items()))
            reporter.write_line(f"Tests collectés : {self.total}  ({detail})")

    # --- statut : supprime les points par défaut, conserve les catégories -------
    @pytest.hookimpl(tryfirst=True)
    def pytest_report_teststatus(self, report: pytest.TestReport):
        if hasattr(report, "wasxfail"):
            if report.skipped:
                return "xfailed", "", "XFAIL"
            if report.passed and report.when == "call":
                return "xpassed", "", "XPASS"
        if report.when in ("setup", "teardown") and report.failed:
            return "error", "", "ERROR"
        if report.when == "setup" and report.skipped:
            return "skipped", "", "SKIPPED"
        if report.when == "call":
            return report.outcome, "", report.outcome.upper()
        return None

    # --- une ligne par test ------------------------------------------------------
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        counted = report.when == "call" or (report.when == "setup" and not report.passed)
        if not counted:
            return
        self.done += 1
        if hasattr(report, "wasxfail"):
            status = "XFAIL" if report.skipped else "XPASS"
        elif report.when == "setup" and report.failed:
            status = "ERROR"
        else:
            status = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(report.outcome, "DONE")
        pct = self.done / self.total * 100 if self.total else 100.0
        width = len(str(self.total))
        layer = self.layers.get(report.nodeid, "?")
        reporter = self.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_line(
                f"[{self.done:0{width}d}/{self.total:0{width}d} | {pct:5.1f}%] "
                f"{status:<5} [{layer}] {report.nodeid}"
            )


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(ProgressPlugin(config), "fbd-progress")


# --------------------------------------------------------------------------- #
# Fixtures partagées : pipeline synthétique interne aux tests, exécuté une seule fois par session
# --------------------------------------------------------------------------- #
from tests.support.fixture_data import run_test_pipeline  # noqa: E402
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths  # noqa: E402


def _fixture_data(tmp_path_factory, name: str, date_format: str) -> ProjectPaths:
    paths = ProjectPaths(root=PROJECT_ROOT, data=tmp_path_factory.mktemp(name))
    run_test_pipeline(paths, date_format=date_format, log=lambda _message: None)
    return paths


@pytest.fixture(scope="session")
def fixture_string(tmp_path_factory) -> ProjectPaths:
    """Pipeline complet sur fixtures de test, dates sources en chaînes ISO."""
    return _fixture_data(tmp_path_factory, "fixture_string", "string")


@pytest.fixture(scope="session")
def fixture_date(tmp_path_factory) -> ProjectPaths:
    """Même pipeline de test, dates sources au type Parquet `date32` (le schéma réel peut être l'un ou l'autre)."""
    return _fixture_data(tmp_path_factory, "fixture_date", "date")
