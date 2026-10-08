"""Couche G — sql_runner : ordre des scripts, garde-fous, interprétation des contrôles 07, erreurs lisibles.

Connexions FACTICES : on vérifie l'orchestration (ordre, bases ciblées, arrêt sur erreur), pas SQL Server.
"""

from __future__ import annotations

import pytest

from france_business_dynamics.sql_runner import (
    CREATE_SCRIPTS,
    FINALIZE_SCRIPTS,
    evaluate_quality_rows,
    main,
    run_script,
    run_sql_pipeline,
    split_batches,
)
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths
from france_business_dynamics.utils.errors import PipelineError, PreconditionError

pytestmark = pytest.mark.layer("G-sql")
QUIET = {"log": lambda _m: None}
PATHS = ProjectPaths(root=PROJECT_ROOT, data=PROJECT_ROOT / "data")
PASS_ROWS = [("a", "PASS", "1", "1"), ("b", "WARN", "2", "1")]


class Cursor:
    def __init__(self, db) -> None:
        self.db, self.description, self._rows, self._sets = db, None, [], []

    def execute(self, sql):
        code = [line for line in sql.strip().splitlines() if line.strip() and not line.strip().startswith("--")]
        self.db.log.append((self.db.database, (code[0] if code else "")[:60].strip()))
        if self.db.fail_when and self.db.fail_when in sql:
            raise RuntimeError(self.db.fail_message)
        self.description, self._rows, self._sets = None, [], []
        if "CheckName" in sql:
            self.description = [("CheckName",), ("Status",), ("Observed",), ("Expected",)]
            self._rows = self.db.quality_rows
        if self.db.error_in_nextset and "INSERT INTO analytics.DimDate" in sql:
            self._sets = ["boom"]

    def fetchall(self):
        return self._rows

    def nextset(self):
        if self._sets:
            self._sets.pop()
            raise RuntimeError("Violation of PRIMARY KEY constraint (levée dans un jeu de résultats suivant)")
        return False


class Db:
    def __init__(self, database, shared) -> None:
        self.database = database
        self.log = shared["log"]
        self.quality_rows = shared["quality_rows"]
        self.fail_when = shared.get("fail_when")
        self.fail_message = shared.get("fail_message", "boom")
        self.error_in_nextset = shared.get("error_in_nextset", False)
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True


def factory(shared):
    opened: list[str | None] = []

    def make(database):
        opened.append(database)
        return Db(database, shared)

    make.opened = opened
    return make


def shared(**kwargs):
    return {"log": [], "quality_rows": PASS_ROWS, **kwargs}


# ------------------------------------------------------------------------------------------------
def test_split_batches_on_go_lines_only():
    text = "SELECT 1;\nGO\nSELECT 'GO now';\ngo\n  GO  \nSELECT 3;\nGO;\n"
    assert split_batches(text) == ["SELECT 1;", "SELECT 'GO now';", "SELECT 3;"]


def test_every_project_script_splits_into_executable_batches():
    for name in CREATE_SCRIPTS + FINALIZE_SCRIPTS:
        text = (PROJECT_ROOT / "sql" / name).read_text(encoding="utf-8")
        batches = split_batches(text)
        assert batches and not any(b.upper().strip() == "GO" for b in batches)


def test_create_view_batches_are_not_preceded_by_other_statements():
    """CREATE VIEW doit être la 1re instruction de son lot (règle T-SQL) : un seul CREATE par lot."""
    for batch in split_batches((PROJECT_ROOT / "sql" / "05_analytical_views.sql").read_text(encoding="utf-8")):
        if "CREATE OR ALTER VIEW" in batch:
            code = [l for l in batch.splitlines() if l.strip() and not l.strip().startswith("--")]
            assert code[0].startswith("CREATE OR ALTER VIEW") and batch.count("CREATE OR ALTER VIEW") == 1


def test_evaluate_quality_rows():
    fails, warns = evaluate_quality_rows([("a", "PASS", 1, 1), ("b", "warn", 2, 1), ("c", "FAIL", 3, 1)])
    assert [r[0] for r in fails] == ["c"] and [r[0] for r in warns] == ["b"]


def test_create_requires_explicit_confirmation_because_it_rebuilds_tables():
    with pytest.raises(PreconditionError) as err:
        run_sql_pipeline(PATHS, stage="create", connection_factory=factory(shared()), **QUIET)
    assert "--yes" in err.value.hint and "RECONSTRUISENT" in err.value.message


def test_create_runs_01_on_master_then_02_to_05_on_the_project_database_in_order():
    state = shared()
    make = factory(state)
    assert run_sql_pipeline(PATHS, stage="create", yes=True, connection_factory=make, **QUIET) == 0
    assert make.opened == ["master", None]
    on_master = [sql for db, sql in state["log"] if db == "master"]
    assert on_master[0] == "SET NOCOUNT ON" and any("DB_ID" in s for s in on_master)
    scripts_in_order = [sql for db, sql in state["log"] if db is None]
    assert scripts_in_order[0] == "SET NOCOUNT ON"
    assert [i for i, s in enumerate(scripts_in_order) if "sys.schemas" in s] and scripts_in_order.index("USE FranceBusinessDynamics;") >= 0
    creates = [s for s in scripts_in_order if s.startswith("CREATE TABLE") or s.startswith("CREATE OR ALTER VIEW")]
    assert any("DimDate" in c for c in creates) and any("vw_AdministrativeEvents" in c for c in creates)
    dim_index = next(i for i, s in enumerate(scripts_in_order) if "DimDate" in s and s.startswith("CREATE"))
    fact_index = next(i for i, s in enumerate(scripts_in_order) if "FactEstablishmentSnapshot (" in s and s.startswith("CREATE"))
    view_index = next(i for i, s in enumerate(scripts_in_order) if s.startswith("CREATE OR ALTER VIEW"))
    assert dim_index < fact_index < view_index  # dimensions → faits → vues


def test_all_stage_checks_data_offline_first_then_creates_loads_and_finalizes_in_documented_order():
    order: list[str] = []
    state = shared()

    def make(database):
        order.append(f"connect:{database}")
        return Db(database, state)

    def fake_load(paths, **kwargs):
        order.append("load:dry" if kwargs.get("dry_run") else "load:real")

    code = run_sql_pipeline(PATHS, stage="all", yes=True, connection_factory=make, load=fake_load, **QUIET)
    assert code == 0
    assert order == ["load:dry", "connect:master", "connect:None", "load:real", "connect:None"]
    ran = [sql for _, sql in state["log"]]
    markers = [
        next(i for i, s in enumerate(ran) if "DB_ID" in s),                       # 01
        next(i for i, s in enumerate(ran) if s.startswith("CREATE TABLE analytics.DimDate")),  # 03
        next(i for i, s in enumerate(ran) if s.startswith("CREATE OR ALTER VIEW")),            # 05
        next(i for i, s in enumerate(ran) if s.startswith("BEGIN TRANSACTION")),               # 06
        next(i for i, s in enumerate(ran) if s.startswith("SELECT CheckName")),                # 07
    ]
    assert markers == sorted(markers)


def test_all_stage_opens_no_connection_when_offline_checks_fail():
    opened: list = []

    def boom(paths, **kwargs):
        raise PipelineError("données non conformes")

    def make(database):
        opened.append(database)
        raise AssertionError("aucune connexion avant la validation hors ligne")

    with pytest.raises(PipelineError):
        run_sql_pipeline(PATHS, stage="all", yes=True, connection_factory=make, load=boom, **QUIET)
    assert opened == []


def test_dry_run_prints_the_plan_and_touches_nothing():
    logs: list[str] = []
    calls: list = []
    code = run_sql_pipeline(PATHS, stage="all", dry_run=True, connection_factory=lambda d: calls.append(d),
                            load=lambda p, **k: calls.append("load"), log=logs.append)
    assert code == 0 and calls == ["load"]  # seul le contrôle hors ligne s'exécute
    assert "01_create_database.sql" in logs[0] and logs[0].index("05_") < logs[0].index("load_to_db") < logs[0].index("06_")


def test_finalize_returns_1_when_a_sql_quality_check_fails():
    state = shared(quality_rows=[("reconciliation", "FAIL", "1", "2"), ("ok", "PASS", "1", "1")])
    logs: list[str] = []
    code = run_sql_pipeline(PATHS, stage="finalize", connection_factory=factory(state), log=logs.append)
    assert code == 1
    assert any("[FAIL]" in line and "reconciliation" in line for line in logs)
    assert "1 PASS, 0 WARN, 1 FAIL" in logs[-1]


def test_finalize_with_only_warnings_succeeds():
    assert run_sql_pipeline(PATHS, stage="finalize", connection_factory=factory(shared()), **QUIET) == 0


def test_finalize_without_result_set_is_an_error():
    with pytest.raises(PipelineError):
        run_sql_pipeline(PATHS, stage="finalize", connection_factory=factory(shared(quality_rows=[])), **QUIET)


def test_script_failure_names_script_batch_and_gives_a_specific_hint():
    state = shared(fail_when="IF NOT EXISTS (SELECT 1 FROM stg.EstablishmentSnapshot)", fail_message="THROW 50001: Staging vide")
    with pytest.raises(PipelineError) as err:
        run_sql_pipeline(PATHS, stage="finalize", connection_factory=factory(state), **QUIET)
    assert "06_load_mart.sql" in err.value.message and "lot" in err.value.message
    assert "load_to_db" in err.value.hint


def test_error_raised_only_when_draining_result_sets_is_not_swallowed():
    """Gotcha pyodbc : l'erreur d'une instruction ultérieure d'un lot n'apparaît qu'à nextset()."""
    state = shared(error_in_nextset=True)
    with pytest.raises(PipelineError) as err:
        run_sql_pipeline(PATHS, stage="finalize", connection_factory=factory(state), **QUIET)
    assert "PRIMARY KEY" in err.value.message and "06_load_mart.sql" in err.value.message


def test_connections_are_always_closed():
    state = shared()
    opened: list[Db] = []

    def make(database):
        db = Db(database, state)
        opened.append(db)
        return db

    run_sql_pipeline(PATHS, stage="finalize", connection_factory=make, **QUIET)
    assert opened and all(db.closed for db in opened)


def test_main_list_prints_the_documented_order(capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.index("01_create_database") < out.index("05_analytical_views") < out.index("load_to_db") < out.index("06_load_mart") < out.index("07_quality_checks")
