"""Couche G — scripts SQL : ordre, syntaxe T-SQL, alignement contrat/DDL, exécution émulée sur fixtures.

⚠ Aucun SQL Server n'est disponible ici : l'exécution est émulée sur SQLite (voir tests/support/tsql_sqlite.py
pour ce que cela prouve et ne prouve pas).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import polars as pl
import pytest
import sqlglot
from sqlglot import exp

from france_business_dynamics.sql_contract import ALL_STAGING, BY_NAME
from france_business_dynamics.utils.config import PROJECT_ROOT
from tests.support import oracle
from tests.support.tsql_sqlite import EmulatedThrow, SqliteEmulator, split_batches

pytestmark = pytest.mark.layer("G-sql")
SQL_DIR = PROJECT_ROOT / "sql"
ORDER = [
    "01_create_database.sql", "02_create_schema.sql", "03_create_dimensions.sql", "04_create_facts.sql",
    "05_analytical_views.sql", "06_load_mart.sql", "07_quality_checks.sql",
]


# ------------------------------------------------------------------------------------------------
# Ordre et syntaxe
# ------------------------------------------------------------------------------------------------
def test_sql_pipeline_order_is_present():
    assert [p.name for p in sorted(SQL_DIR.glob("*.sql"))] == ORDER


def test_sql_load_and_quality_order():
    assert (SQL_DIR / "06_load_mart.sql").exists() and (SQL_DIR / "07_quality_checks.sql").exists()
    assert not (SQL_DIR / "06_quality_checks.sql").exists()
    assert not (SQL_DIR / "07_load_mart.sql").exists()


@pytest.mark.parametrize("name", ORDER)
def test_every_script_parses_as_tsql(name):
    for batch in split_batches((SQL_DIR / name).read_text(encoding="utf-8")):
        statements = [s for s in sqlglot.parse(batch, read="tsql") if s is not None]
        assert statements, f"{name} : lot vide ou illisible"


@pytest.mark.parametrize("name", ORDER)
def test_scripts_are_utf8_without_bom_and_declare_the_database(name):
    raw = (SQL_DIR / name).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    if name != "01_create_database.sql":
        assert "USE FranceBusinessDynamics;" in text  # jamais exécuté sur `master` par erreur


def test_rebuild_scripts_never_patch_columns_or_hide_old_schemas():
    drops: set[str] = set()
    creates: set[str] = set()
    for name in ("03_create_dimensions.sql", "04_create_facts.sql"):
        text = (SQL_DIR / name).read_text(encoding="utf-8")
        assert "ALTER COLUMN" not in text.upper()
        assert "IF OBJECT_ID" not in text.upper()  # l'ancien « créer si absent » conservait les tables étroites
        assert "RECONSTRUCTION" in text  # comportement documenté en tête de fichier
        creates |= set(re.findall(r"CREATE TABLE ([\w.]+)", text))
        drops |= set(re.findall(r"DROP TABLE IF EXISTS ([\w.]+)", text))
    # 03 supprime les FAITS (enfants) avant les dimensions ; 04 les recrée : l'union doit tout couvrir
    assert creates <= drops, creates - drops
    assert len(creates) == 5 + 11


def test_throw_statement_has_valid_tsql_form_because_sqlglot_cannot_verify_it():
    """sqlglot réduit THROW à `THROW;` : on contrôle donc la forme documentée par regex (numéro ≥ 50000)."""
    text = (SQL_DIR / "06_load_mart.sql").read_text(encoding="utf-8")
    found = re.findall(r"THROW\s+(\d+)\s*,\s*N'[^']+'\s*,\s*(\d+)\s*;", text)
    assert found and all(int(number) >= 50000 and 0 <= int(state) <= 255 for number, state in found)


def test_load_script_has_an_empty_staging_guard_and_a_transaction():
    text = (SQL_DIR / "06_load_mart.sql").read_text(encoding="utf-8")
    assert "THROW 50001" in text and "XACT_ABORT ON" in text
    assert text.index("THROW 50001") < text.index("BEGIN TRANSACTION") < text.index("COMMIT TRANSACTION")


# ------------------------------------------------------------------------------------------------
# DDL ↔ contrat Python
# ------------------------------------------------------------------------------------------------
from tests.support.ddl import parse_tables  # noqa: E402


def test_staging_ddl_is_the_exact_copy_of_the_python_contract():
    ddl = parse_tables("04_create_facts.sql")
    for table in ALL_STAGING:
        expected = {c.name: (c.sql_type, c.length, not c.nullable) for c in table.columns}  # 3e valeur : NOT NULL ?
        assert ddl[table.qualified] == expected, table.qualified
        assert list(ddl[table.qualified]) == table.column_names  # même ordre de colonnes


def test_contract_covers_every_mart_column():
    for table in ALL_STAGING:
        if table.parquet:
            assert table.parquet.endswith(".parquet") and table.column_names


# Colonnes de staging → colonnes du modèle analytique qui les reçoivent (6 : INSERT … SELECT).
STAGING_TO_ANALYTICS = [
    ("EstablishmentSnapshot", "commune_code", "analytics.DimGeography", "CommuneCode"),
    ("EstablishmentSnapshot", "commune_code", "analytics.FactEstablishmentSnapshot", "CommuneCode"),
    ("EstablishmentSnapshot", "commune_name", "analytics.DimGeography", "CommuneName"),
    ("EstablishmentSnapshot", "department_code", "analytics.DimGeography", "DepartmentCode"),
    ("EstablishmentSnapshot", "region_code", "analytics.DimGeography", "RegionCode"),
    ("EstablishmentSnapshot", "geo_status", "analytics.DimGeography", "GeoStatus"),
    ("EstablishmentSnapshot", "naf_nomenclature", "analytics.DimActivity", "NafNomenclature"),
    ("EstablishmentSnapshot", "naf_code", "analytics.DimActivity", "NafCode"),
    ("LegalUnitSnapshot", "naf_nomenclature", "analytics.DimActivity", "NafNomenclature"),
    ("LegalUnitSnapshot", "naf_code", "analytics.DimActivity", "NafCode"),
    ("AdministrativeEvents", "naf_nomenclature", "analytics.DimActivity", "NafNomenclature"),
    ("AdministrativeEvents", "naf_code", "analytics.DimActivity", "NafCode"),
    ("EstablishmentSnapshot", "naf_section", "analytics.DimActivity", "NafSection"),
    ("EstablishmentSnapshot", "workforce_code", "analytics.DimWorkforce", "WorkforceCode"),
    ("EstablishmentSnapshot", "workforce_code", "analytics.FactEstablishmentSnapshot", "WorkforceCode"),
    ("EstablishmentSnapshot", "workforce_label", "analytics.DimWorkforce", "WorkforceLabel"),
    ("LegalUnitSnapshot", "legal_category_code", "analytics.DimLegalCategory", "LegalCategoryCode"),
    ("LegalUnitSnapshot", "legal_category_code", "analytics.FactLegalUnitSnapshot", "LegalCategoryCode"),
    ("LegalUnitSnapshot", "enterprise_category", "analytics.FactLegalUnitSnapshot", "EnterpriseCategory"),
    ("AdministrativeEvents", "entity_type", "analytics.FactAdministrativeEvents", "EntityType"),
    ("AdministrativeEvents", "event_type", "analytics.FactAdministrativeEvents", "EventType"),
    ("RefRegion", "region_name", "analytics.DimGeography", "RegionName"),
    ("RefDepartment", "department_name", "analytics.DimGeography", "DepartmentName"),
    ("RefNafSection", "naf_section_label", "analytics.DimActivity", "NafSectionLabel"),
    ("RefLegalCategoryLevel1", "level1_label", "analytics.DimLegalCategory", "Level1Label"),
    ("RefWorkforce", "workforce_label", "analytics.DimWorkforce", "WorkforceLabel"),
    ("RefWorkforce", "workforce_code", "analytics.DimWorkforce", "WorkforceCode"),
]
DOCUMENTED_NARROWING = {  # étroit PAR CONCEPTION : le CHECK analytics rejette tout autre code (échec bruyant)
    ("EstablishmentSnapshot", "status_code"), ("LegalUnitSnapshot", "status_code"),
}


def analytics_tables() -> dict[str, dict[str, tuple]]:
    return {**parse_tables("03_create_dimensions.sql"), **parse_tables("04_create_facts.sql")}


@pytest.mark.parametrize(("stg_table", "stg_col", "dst_table", "dst_col"), STAGING_TO_ANALYTICS)
def test_analytics_column_is_at_least_as_wide_as_its_staging_source(stg_table, stg_col, dst_table, dst_col):
    """Aucune troncature possible entre staging et modèle : l'aval est toujours ≥ l'amont."""
    source = BY_NAME[stg_table].column(stg_col)
    target = analytics_tables()[dst_table][dst_col]
    target_type, target_length, _ = target
    assert target_type == source.sql_type, (stg_table, stg_col)
    assert (target_length or 0) >= (source.length or 0), (dst_table, dst_col, target_length, source.length)


def test_status_codes_are_narrow_by_design_and_protected_by_check_constraints():
    ddl = (SQL_DIR / "04_create_facts.sql").read_text(encoding="utf-8")
    assert "CHECK (StatusCode IN ('A', 'F', 'U'))" in ddl and "CHECK (StatusCode IN ('A', 'C', 'U'))" in ddl
    tables = analytics_tables()
    assert tables["analytics.FactEstablishmentSnapshot"]["StatusCode"][:2] == ("char", 1)
    assert DOCUMENTED_NARROWING  # documenté ici ET dans docs/data_dictionary.md


# ------------------------------------------------------------------------------------------------
# Exécution émulée sur fixtures
# ------------------------------------------------------------------------------------------------
@pytest.fixture
def db(fixture_string):
    emulator = SqliteEmulator()
    for name in ORDER[:5]:
        emulator.run_file(SQL_DIR / name)
    emulator.load_staging(fixture_string.processed)
    return emulator


@pytest.fixture
def loaded(db):
    db.run_file(SQL_DIR / "06_load_mart.sql")
    return db


def test_scripts_01_to_05_create_the_documented_objects(db):
    assert db.tables("analytics") == {
        "DimDate", "DimGeography", "DimActivity", "DimWorkforce", "DimLegalCategory",
        "FactEstablishmentSnapshot", "FactLegalUnitSnapshot", "FactAdministrativeEvents",
    }
    assert db.tables("stg") == {t.name for t in ALL_STAGING}
    assert db.views("analytics") == {
        "vw_EstablishmentSnapshot", "vw_LegalUnitSnapshot", "vw_AdministrativeEvents", "vw_EstablishmentsByRegion",
        "vw_EstablishmentsByNafSection", "vw_EstablishmentsByWorkforce", "vw_AdministrativeEventsByYear",
        "vw_LegalUnitsByLegalCategory",
    }


def test_rebuild_scripts_are_idempotent_even_with_data_loaded(loaded):
    before = loaded.scalar("SELECT COUNT(*) FROM analytics.FactEstablishmentSnapshot")
    assert before > 0
    for name in ("03_create_dimensions.sql", "04_create_facts.sql", "05_analytical_views.sql"):
        loaded.run_file(SQL_DIR / name)  # rejouer : doit réussir et repartir de zéro
    assert loaded.scalar("SELECT COUNT(*) FROM analytics.FactEstablishmentSnapshot") == 0
    assert loaded.scalar("SELECT COUNT(*) FROM stg.EstablishmentSnapshot") == 0  # staging aussi reconstruit


def test_load_script_is_rerunnable_without_duplicates(loaded):
    first = loaded.query("SELECT 'e', COUNT(*), SUM(EstablishmentCount) FROM analytics.FactEstablishmentSnapshot")
    loaded.run_file(SQL_DIR / "06_load_mart.sql")
    assert loaded.query("SELECT 'e', COUNT(*), SUM(EstablishmentCount) FROM analytics.FactEstablishmentSnapshot") == first


def test_load_script_refuses_to_run_on_empty_staging(fixture_string):
    emulator = SqliteEmulator()
    for name in ORDER[:5]:
        emulator.run_file(SQL_DIR / name)
    with pytest.raises(EmulatedThrow, match="load_to_db"):
        emulator.run_file(SQL_DIR / "06_load_mart.sql")


def test_facts_reconcile_with_staging_and_with_the_independent_oracle(loaded):
    assert loaded.scalar("SELECT SUM(EstablishmentCount) FROM analytics.FactEstablishmentSnapshot") == sum(oracle.establishment_mart().values())
    assert loaded.scalar("SELECT SUM(LegalUnitCount) FROM analytics.FactLegalUnitSnapshot") == sum(oracle.legal_unit_mart().values())
    assert loaded.scalar("SELECT SUM(EventCount) FROM analytics.FactAdministrativeEvents") == sum(oracle.events().values())


def test_dimensions_carry_labels_codes_and_sentinels(loaded):
    geo = {r[0]: r[1:] for r in loaded.query(
        "SELECT CommuneCode, CommuneName, DepartmentCode, DepartmentName, RegionCode, RegionName, GeoStatus FROM analytics.DimGeography")}
    assert geo["2A001"] == ("Afa", "2A", "Corse-du-Sud", "94", "Corse", "MATCHED")
    assert geo["97411"] == ("Saint-Denis", "974", "La Réunion", "04", "La Réunion", "MATCHED")
    assert geo["01999"][1:3] == ("01", "Ain")  # héritée du parent
    assert geo["UNKNOWN"] == ("Non renseigné", "UNKNOWN", "Non renseigné", "UNKNOWN", "Non renseigné", "NO_COMMUNE")
    assert geo["99999"][-1] == "UNMATCHED"
    assert loaded.scalar("SELECT FullDate FROM analytics.DimDate WHERE DateKey = 20261001") == "2026-10-01"
    assert loaded.query("SELECT CalendarYear, CalendarQuarter, CalendarMonth, MonthName FROM analytics.DimDate") == [(2026, 4, 10, "octobre")]
    workforce = dict(loaded.query("SELECT WorkforceCode, SortOrder FROM analytics.DimWorkforce"))
    assert workforce["NN"] == 1 and workforce["53"] == 16 and workforce["UNKNOWN"] == 99 and len(workforce) == 17


def test_activity_dimension_never_gives_a_rev2_section_to_historical_nomenclatures(loaded):
    rows = loaded.query("SELECT NafNomenclature, NafCode, NafSection, NafSectionLabel, IsNafRev2 FROM analytics.DimActivity")
    by_key = {(r[0], r[1]): r[2:] for r in rows}
    assert by_key[("NAFRev1", "52.1B")] == (None, "Hors NAF Rev.2 ou non classé", 0)
    assert by_key[("NAFRev2", "47.11A")] == ("G", "Commerce ; réparation d'automobiles et de motocycles", 1)
    assert by_key[("NAFRev2", "4711A")][0] is None and by_key[("NAFRev2", "4711A")][2] == 0  # Rev2 mal formé
    assert all(sec is None for (nom, _), (sec, _, _) in by_key.items() if nom != "NAFRev2")
    assert len(rows) == len(by_key)  # unicité (nomenclature, code)


def test_legal_category_dimension_uses_level1_labels(loaded):
    rows = {r[0]: r[1:] for r in loaded.query("SELECT LegalCategoryCode, Level1Code, Level1Label FROM analytics.DimLegalCategory")}
    assert rows["5710"] == ("5", "Société commerciale") and rows["1000"] == ("1", "Entrepreneur individuel")
    assert rows["UNKNOWN"] == (None, "Non renseigné")


def test_views_answer_the_business_questions_and_reconcile(loaded):
    total = loaded.scalar("SELECT SUM(EstablishmentCount) FROM analytics.vw_EstablishmentsByRegion")
    assert total == loaded.scalar("SELECT SUM(EstablishmentCount) FROM analytics.FactEstablishmentSnapshot") == 26
    assert total == loaded.scalar("SELECT SUM(EstablishmentCount) FROM analytics.vw_EstablishmentsByNafSection")
    assert total == loaded.scalar("SELECT SUM(EstablishmentCount) FROM analytics.vw_EstablishmentsByWorkforce")
    regions = {r[0]: r[1] for r in loaded.query(
        "SELECT RegionName, SUM(EstablishmentCount) FROM analytics.vw_EstablishmentsByRegion GROUP BY RegionName")}
    cog = oracle.cog()
    expected_regions: dict[str, int] = {}
    for (commune, *_), count in oracle.establishment_mart().items():
        code = cog[commune]["reg"] if commune in cog else None
        label = {"84": "Auvergne-Rhône-Alpes", "94": "Corse", "11": "Île-de-France", "04": "La Réunion"}.get(code, "Non renseigné")
        expected_regions[label] = expected_regions.get(label, 0) + count
    assert regions == expected_regions
    assert regions["Auvergne-Rhône-Alpes"] == 21 and regions["Non renseigné"] == 2  # 99999 (hors COG) + sans commune
    sections = {r[0]: r[1] for r in loaded.query(
        "SELECT NafSection, SUM(EstablishmentCount) FROM analytics.vw_EstablishmentsByNafSection GROUP BY NafSection")}
    expected_unclassified = sum(
        count for (_, nomenclature, code, *_), count in oracle.establishment_mart().items()
        if oracle.section(None if nomenclature == "UNKNOWN" else nomenclature, None if code == "UNKNOWN" else code) is None)
    # NAFRev1, NAF1993, NAP, « sans NAF » et NAFRev2 mal formé : isolés, jamais mélangés aux sections Rev.2
    assert sections[None] == expected_unclassified == 5
    assert loaded.scalar("SELECT StatusLabel FROM analytics.vw_EstablishmentsByRegion WHERE StatusCode = 'F' LIMIT 1") == "Fermé"
    events = {(r[0], r[1]): r[2] for r in loaded.query(
        "SELECT EventYear, EventType, EventCount FROM analytics.vw_AdministrativeEventsByYear")}
    expected: dict = {}
    for (year, event_type, *_), count in oracle.events().items():
        expected[(year, event_type)] = expected.get((year, event_type), 0) + count
    assert events == expected
    assert loaded.scalar("SELECT SUM(LegalUnitCount) FROM analytics.vw_LegalUnitsByLegalCategory") == 14


def test_quality_checks_pass_on_the_loaded_fixtures(loaded):
    results = loaded.run_file(SQL_DIR / "07_quality_checks.sql")
    by_name = {name: (status, observed, expected) for name, status, observed, expected in results}
    assert len(results) == 13 and not [n for n, (s, *_) in by_name.items() if s == "FAIL"], by_name
    assert by_name["reconciliation_establishments"][0] == "PASS" and by_name["reconciliation_establishments"][1] == "26"
    # 1 établissement sur 26 sans commune (≈ 3,8 %) dépasse le seuil de 1 % : avertissement attendu
    assert by_name["unknown_commune_share_le_1pct"][0] == "WARN"
    assert {n for n, (s, *_) in by_name.items() if s == "WARN"} == {"unknown_commune_share_le_1pct"}


# ------------------------------------------------------------------------------------------------
# Contraintes : le modèle REFUSE les données incohérentes
# ------------------------------------------------------------------------------------------------
def test_constraints_reject_bad_status_orphans_and_non_positive_counts(loaded):
    base = "INSERT INTO analytics.FactEstablishmentSnapshot VALUES (20261001, '01004', 1, 'NN', '{}', {})"
    with pytest.raises(sqlite3.IntegrityError):
        loaded.conn.execute(base.format("X", 1))  # CK_FES_Status
    with pytest.raises(sqlite3.IntegrityError):
        loaded.conn.execute(base.format("U", 0))  # CK_FES_Count
    with pytest.raises(sqlite3.IntegrityError):
        loaded.conn.execute("INSERT INTO analytics.FactEstablishmentSnapshot VALUES (20261001, 'NOPE', 1, 'NN', 'A', 1)")  # FK
    with pytest.raises(sqlite3.IntegrityError):
        loaded.conn.execute("INSERT INTO analytics.DimActivity VALUES (9999, 'NAFRev2', '47.11A', 'G', 'x', 1)")  # UQ


def test_quality_checks_detect_a_broken_model(loaded):
    loaded.conn.execute("DELETE FROM analytics.FactEstablishmentSnapshot WHERE EstablishmentCount > 1")
    loaded.conn.execute("UPDATE analytics.DimActivity SET NafSection = 'M' WHERE NafNomenclature = 'NAFRev1'")
    # dépendance fonctionnelle : DEUX lignes pour 2A001 avec des noms DIFFÉRENTS (dupliquer d'abord, modifier une seule)
    loaded.conn.execute("INSERT INTO stg.EstablishmentSnapshot SELECT * FROM stg.EstablishmentSnapshot WHERE commune_code = '2A001'")
    loaded.conn.execute("UPDATE stg.EstablishmentSnapshot SET commune_name = 'AUTRE' WHERE rowid = (SELECT MAX(rowid) FROM stg.EstablishmentSnapshot)")
    results = {n: s for n, s, *_ in loaded.run_file(SQL_DIR / "07_quality_checks.sql")}
    assert results["reconciliation_establishments"] == "FAIL"
    assert results["no_rev2_section_outside_nafrev2"] == "FAIL"
    assert results["geography_attributes_functionally_dependent"] == "FAIL"


def test_load_script_sums_duplicate_staging_rows_instead_of_losing_them(db):
    db.conn.execute("INSERT INTO stg.EstablishmentSnapshot SELECT * FROM stg.EstablishmentSnapshot WHERE commune_code = '2A001'")
    db.run_file(SQL_DIR / "06_load_mart.sql")
    assert db.scalar("SELECT EstablishmentCount FROM analytics.FactEstablishmentSnapshot WHERE CommuneCode = '2A001'") == 2
    assert {n: s for n, s, *_ in db.run_file(SQL_DIR / "07_quality_checks.sql")}["reconciliation_establishments"] == "PASS"
