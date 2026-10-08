"""Couche G — load_to_db : contrôles hors ligne, schéma vivant, insertion par lots, ROLLBACK, messages clairs.

La base est FACTICE (SQLite, voir tests/support/fake_db.py) : ces tests prouvent la logique de contrôle et de
reprise après erreur, PAS le comportement réel du pilote ODBC ni de SQL Server (à valider avec le sql_runner).
"""

from __future__ import annotations

import shutil
import sys
import types

import polars as pl
import pytest

from france_business_dynamics.load_to_db import main, run_load, select_tables
from france_business_dynamics.sql_contract import ALL_STAGING
from france_business_dynamics.sqlconn import SqlSettings, connect
from france_business_dynamics.utils.config import PROJECT_ROOT, ProjectPaths
from france_business_dynamics.utils.errors import PipelineError, PreconditionError, SchemaError
from tests.support.ddl import SQL_DIR, live_schema_from_ddl
from tests.support.fake_db import FakeConnection, FakeOdbcError, TRUNCATION_MESSAGE
from tests.support.tsql_sqlite import SqliteEmulator

pytestmark = pytest.mark.layer("G-sql")
QUIET = {"log": lambda _m: None}


@pytest.fixture
def work(fixture_string, tmp_path) -> ProjectPaths:
    shutil.copytree(fixture_string.data, tmp_path / "data")
    return ProjectPaths(root=PROJECT_ROOT, data=tmp_path / "data")


@pytest.fixture
def server():
    """Base factice dont les tables stg.* ont été créées par le VRAI 04_create_facts.sql."""
    emulator = SqliteEmulator()
    for name in ("03_create_dimensions.sql", "04_create_facts.sql"):
        emulator.run_file(SQL_DIR / name)
    return FakeConnection(emulator, live_schema_from_ddl())


def no_connection():
    raise AssertionError("aucune connexion ne devait être ouverte")


def staged(server, table):
    return server.emulator.scalar(f"SELECT COUNT(*) FROM stg.{table}")


# ------------------------------------------------------------------------------------------------
def test_dry_run_validates_everything_offline_without_connecting(work):
    result = run_load(work, dry_run=True, connection_factory=no_connection, **QUIET)
    assert result["EstablishmentSnapshot"] == 21 and result["RefDepartment"] == 101 and len(result) == 8


def test_full_load_inserts_every_table_and_row_counts_match_the_parquet(work, server):
    loaded = run_load(work, connection_factory=lambda: server, batch_size=7, **QUIET)
    for table in ALL_STAGING:
        assert staged(server, table.name) == loaded[table.name] > 0
    frame = pl.read_parquet(work.processed / "fact_establishment_snapshot.parquet")
    assert staged(server, "EstablishmentSnapshot") == frame.height
    assert server.emulator.scalar("SELECT SUM(establishment_count) FROM stg.EstablishmentSnapshot") == frame["establishment_count"].sum()
    assert server.commits == 8 and server.rollbacks == 0


def test_loading_twice_replaces_instead_of_duplicating(work, server):
    run_load(work, connection_factory=lambda: server, **QUIET)
    first = {t.name: staged(server, t.name) for t in ALL_STAGING}
    run_load(work, connection_factory=lambda: server, **QUIET)
    assert {t.name: staged(server, t.name) for t in ALL_STAGING} == first


def test_loaded_staging_feeds_the_model_scripts_end_to_end(work, server):
    """load_to_db → 05/06/07 : la chaîne complète sur la base factice."""
    run_load(work, connection_factory=lambda: server, **QUIET)
    for name in ("05_analytical_views.sql", "06_load_mart.sql"):
        server.emulator.run_file(SQL_DIR / name)
    rows = server.emulator.run_file(SQL_DIR / "07_quality_checks.sql")
    assert not [r for r in rows if r[1] == "FAIL"]


def test_progress_messages_name_the_table_and_the_percentage(work, server):
    logs: list[str] = []
    run_load(work, connection_factory=lambda: server, batch_size=5, table_names=["EstablishmentSnapshot"], log=logs.append)
    text = "\n".join(logs)
    assert "stg.EstablishmentSnapshot" in text and "%" in text and "terminé" in text
    assert text.count("stg.EstablishmentSnapshot :") >= 5  # plusieurs lots


def test_table_subset_only_touches_requested_tables(work, server):
    run_load(work, connection_factory=lambda: server, table_names=["RefRegion", "RefDepartment"], **QUIET)
    assert staged(server, "RefRegion") == 18 and staged(server, "EstablishmentSnapshot") == 0


# --- hors ligne : les problèmes sont détectés AVANT toute connexion ------------------------------
def test_missing_marts_give_the_command_to_produce_them(work):
    (work.processed / "fact_legal_unit_snapshot.parquet").unlink()
    with pytest.raises(PreconditionError) as err:
        run_load(work, connection_factory=no_connection, **QUIET)
    assert "fact_legal_unit_snapshot.parquet" in err.value.message and "build_mart" in err.value.hint


def test_value_wider_than_the_sql_column_is_caught_offline(work):
    """Le cas historique : une valeur plus large que la colonne ne doit JAMAIS atteindre SQL Server."""
    path = work.processed / "fact_establishment_snapshot.parquet"
    frame = pl.read_parquet(path)
    frame.with_columns(pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("N" * 25)).otherwise(pl.col("naf_nomenclature"))
                       .alias("naf_nomenclature")).write_parquet(path)
    with pytest.raises(SchemaError) as err:
        run_load(work, connection_factory=no_connection, **QUIET)
    assert "stg.EstablishmentSnapshot.naf_nomenclature" in err.value.message
    assert "25" in err.value.message and "20" in err.value.message and "troncature" in err.value.message
    assert "aucune insertion" in err.value.message


def test_all_offline_problems_are_reported_together(work):
    path = work.processed / "fact_establishment_snapshot.parquet"
    frame = pl.read_parquet(path)
    frame.drop("naf_code").with_columns(pl.lit(None, dtype=pl.String).alias("commune_name"),
                                       pl.lit("é").alias("commune_code")).write_parquet(path)
    with pytest.raises(SchemaError) as err:
        run_load(work, dry_run=True, **QUIET)
    for expected in ("missing_column", "null", "non_ascii"):
        assert expected in err.value.message


def test_unknown_table_and_bad_batch_size_are_precondition_errors(work):
    with pytest.raises(PreconditionError):
        select_tables(["Nope"])
    with pytest.raises(PreconditionError):
        run_load(work, dry_run=True, batch_size=0, **QUIET)


# --- schéma vivant -------------------------------------------------------------------------------
def test_old_narrow_schema_is_detected_before_any_insert(work, server):
    """L'ancien schéma (naf_nomenclature varchar(6), status_code varchar(1)) est refusé avec le nom des colonnes."""
    narrow = {t: [(c, ty, 6 if c == "naf_nomenclature" else (1 if c == "status_code" else ln), nl)
                  for c, ty, ln, nl in cols] for t, cols in server.live_schema.items()}
    server.live_schema = narrow
    with pytest.raises(SchemaError) as err:
        run_load(work, connection_factory=lambda: server, **QUIET)
    assert "naf_nomenclature" in err.value.message and "varchar(6)" in err.value.message
    assert "status_code" in err.value.message and "04_create_facts.sql" in err.value.hint
    assert staged(server, "EstablishmentSnapshot") == 0  # rien n'a été inséré
    assert server.batches == 0


def test_missing_table_and_missing_column_in_live_schema(work, server):
    del server.live_schema["LegalUnitSnapshot"]
    server.live_schema["EstablishmentSnapshot"] = [c for c in server.live_schema["EstablishmentSnapshot"] if c[0] != "geo_status"]
    with pytest.raises(SchemaError) as err:
        run_load(work, connection_factory=lambda: server, **QUIET)
    assert "stg.LegalUnitSnapshot" in err.value.message and "missing_table" in err.value.message
    assert "geo_status" in err.value.message


def test_check_only_validates_the_live_schema_and_inserts_nothing(work, server):
    assert run_load(work, check_only=True, connection_factory=lambda: server, **QUIET) == {}
    assert server.batches == 0 and staged(server, "EstablishmentSnapshot") == 0


def test_live_schema_derived_from_the_real_ddl_matches_the_contract(work, server):
    run_load(work, check_only=True, connection_factory=lambda: server, **QUIET)  # ne lève pas


# --- panne pendant l'insertion --------------------------------------------------------------------
def test_failure_midway_rolls_back_the_table_and_explains_where(work, server):
    server.emulator.conn.execute(
        "INSERT INTO stg.EstablishmentSnapshot VALUES (1,'X','n','d','r','MATCHED','NAFRev2','c',NULL,'NN','l','A',99)")
    server.emulator.conn.commit()
    server.fail_on_batch = 2
    with pytest.raises(PipelineError) as err:
        run_load(work, connection_factory=lambda: server, batch_size=5, table_names=["EstablishmentSnapshot"], **QUIET)
    message, hint = err.value.message, err.value.hint
    assert "stg.EstablishmentSnapshot" in message and "6" in message and "10" in message  # lignes 6 à 10
    assert "right truncation" in message
    assert "troncature" in hint and "04_create_facts.sql" in hint and "ROLLBACK" in hint
    assert server.rollbacks == 1
    assert staged(server, "EstablishmentSnapshot") == 1  # contenu précédent intact


def test_silent_row_loss_is_detected_by_the_final_count(work, server):
    server.drop_rows = 1
    with pytest.raises(PipelineError) as err:
        run_load(work, connection_factory=lambda: server, table_names=["RefRegion"], **QUIET)
    assert "≠" in err.value.message and "RefRegion" in err.value.message and server.rollbacks == 1


def test_permission_and_missing_object_errors_get_specific_hints(work, server):
    server.fail_on_batch = 1
    server.fail_message = "The INSERT permission was denied on the object 'RefRegion'"
    with pytest.raises(PipelineError) as err:
        run_load(work, connection_factory=lambda: server, table_names=["RefRegion"], **QUIET)
    assert "Droits insuffisants" in err.value.hint


def test_connection_is_closed_only_when_we_opened_it(work, server):
    run_load(work, connection_factory=lambda: server, table_names=["RefRegion"], **QUIET)
    assert server.closed is False  # connexion injectée : appartient à l'appelant


def test_main_dry_run_exit_code(work, monkeypatch, capsys):
    monkeypatch.setenv("FBD_DATA_DIR", str(work.data))
    assert main(["--dry-run"]) == 0
    assert "Dry-run terminé" in capsys.readouterr().out


# --- configuration et connexion -----------------------------------------------------------------
def test_settings_defaults_and_masked_connection_string():
    settings = SqlSettings.from_env({}, load_dotenv_file=False)
    assert settings.trusted and settings.database == "FranceBusinessDynamics"
    assert "Trusted_Connection=yes" in settings.connection_string() and "PWD" not in settings.connection_string()


def test_sql_authentication_requires_credentials_and_never_prints_the_password():
    with pytest.raises(PreconditionError) as err:
        SqlSettings.from_env({"SQL_TRUSTED_CONNECTION": "no"}, load_dotenv_file=False)
    assert "SQL_USER" in err.value.message
    settings = SqlSettings.from_env(
        {"SQL_TRUSTED_CONNECTION": "no", "SQL_USER": "u", "SQL_PASSWORD": "s3cret", "SQL_SERVER": r"host\SQLEXPRESS"},
        load_dotenv_file=False)
    assert "s3cret" in settings.connection_string() and "s3cret" not in settings.connection_string(mask_password=True)
    assert "s3cret" not in settings.describe() and "SQLEXPRESS" in settings.describe()


def test_connect_without_odbc_library_or_driver_is_a_clear_precondition_error(monkeypatch):
    settings = SqlSettings.from_env({}, load_dotenv_file=False)
    fake = types.SimpleNamespace(drivers=lambda: ["SQL Server"], Error=FakeOdbcError, connect=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    with pytest.raises(PreconditionError) as err:
        connect(settings)
    assert "ODBC Driver 18" in err.value.message and "SQL Server" in err.value.message and "SQL_DRIVER" in err.value.hint


def test_connect_failure_names_the_server_and_hides_the_password(monkeypatch):
    settings = SqlSettings.from_env({"SQL_TRUSTED_CONNECTION": "no", "SQL_USER": "u", "SQL_PASSWORD": "s3cret"}, load_dotenv_file=False)

    def refuse(*args, **kwargs):
        raise FakeOdbcError("Login timeout expired")

    fake = types.SimpleNamespace(drivers=lambda: [settings.driver], Error=FakeOdbcError, connect=refuse)
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    with pytest.raises(PipelineError) as err:
        connect(settings)
    assert "localhost" in err.value.message and "Login timeout" in err.value.message
    assert "s3cret" not in err.value.message and "SQLEXPRESS" in err.value.hint


def test_truncation_error_text_is_the_historical_one():
    assert "length 14 buffer 6" in TRUNCATION_MESSAGE
