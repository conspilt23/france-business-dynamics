"""Émulateur SQLite des scripts T-SQL du projet (tests uniquement).

Objectif : exécuter la VRAIE logique des fichiers `sql/*.sql` (jointures, agrégations, clés étrangères,
CHECK, vues) sur des données de fixtures, sans SQL Server. Les scripts sont lus tels quels, analysés avec
le dialecte `tsql` de sqlglot, puis transpilés vers SQLite.

Ce que l'émulation NE prouve PAS (propre à SQL Server, à valider sur un vrai serveur — voir
docs/execution_checklist.md) : troncature des `varchar`, plans d'exécution, transactions T-SQL
(`SET XACT_ABORT`, `BEGIN/COMMIT` sont ignorés), `IDENTITY`, collations, droits.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import polars as pl
import sqlglot
from sqlglot import exp

from france_business_dynamics.sql_contract import ALL_STAGING, reference_frames


class EmulatedThrow(Exception):
    """Reproduit un `THROW` T-SQL déclenché par une garde du script."""


from france_business_dynamics.sql_runner import split_batches  # noqa: E402,F401  (même découpage que l'exécuteur réel)


def _strip_reference_schema(node: exp.Expression) -> exp.Expression:
    # SQLite refuse un parent qualifié (`REFERENCES analytics.DimDate`) : il résout dans la même base.
    if isinstance(node, exp.Reference):
        for table in node.find_all(exp.Table):
            table.set("db", None)
    return node


def _to_sqlite(statement: exp.Expression) -> str:
    sql = statement.transform(_strip_reference_schema).sql(dialect="sqlite")
    return re.sub(r"(?<![A-Za-z0-9_])N'", "'", sql)  # littéraux nvarchar N'…'


class SqliteEmulator:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("ATTACH DATABASE ':memory:' AS stg")
        self.conn.execute("ATTACH DATABASE ':memory:' AS analytics")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.create_function(
            "DATE_FROM_PARTS", 3, lambda y, m, d: f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        )
        self.skipped: list[str] = []

    # ------------------------------------------------------------------ exécution
    def run_file(self, path: Path) -> list[tuple]:
        """Exécute un fichier .sql ; retourne les lignes du dernier SELECT (jeu de résultats)."""
        rows: list[tuple] = []
        for batch in split_batches(path.read_text(encoding="utf-8")):
            for statement in sqlglot.parse(batch, read="tsql"):
                if statement is None:
                    continue
                result = self._run_statement(statement, batch)
                if result is not None:
                    rows = result
        return rows

    def _run_statement(self, statement: exp.Expression, batch: str = "") -> list[tuple] | None:
        if isinstance(statement, (exp.Use, exp.Set, exp.Transaction, exp.Commit)):
            self.skipped.append(type(statement).__name__)
            return None
        if type(statement).__name__ == "IfBlock":
            return self._run_if_block(statement, batch)
        if isinstance(statement, exp.Create) and (statement.args.get("kind") or "").upper() == "VIEW":
            return self._run_view(statement)
        sql = _to_sqlite(statement)
        cursor = self.conn.execute(sql)
        if isinstance(statement, exp.Select):
            return cursor.fetchall()
        return None

    def _run_if_block(self, block: exp.Expression, batch: str) -> None:
        # sqlglot réduit `THROW 50001, N'msg', 1;` à `THROW;` (arguments perdus) : le message est lu dans le lot brut.
        if re.search(r"\bTHROW\b", batch, re.IGNORECASE):
            condition = _to_sqlite(block.this)
            if self.conn.execute(f"SELECT {condition}").fetchone()[0]:
                message = re.search(r"THROW\s+\d+\s*,\s*N?'((?:[^']|'')*)'", batch, re.IGNORECASE)
                raise EmulatedThrow(message.group(1) if message else "THROW")
        else:  # gardes d'infrastructure (DB_ID, sys.schemas) : propres à SQL Server
            self.skipped.append("IfBlock")
        return None

    def _run_view(self, statement: exp.Create) -> None:
        name = statement.this.sql(dialect="sqlite")
        self.conn.execute(f"DROP VIEW IF EXISTS {name}")
        sql = _to_sqlite(statement).replace("CREATE OR REPLACE VIEW", "CREATE VIEW", 1)
        self.conn.execute(sql)

    # ------------------------------------------------------------------ données
    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params).fetchone()[0]

    def tables(self, schema: str) -> set[str]:
        rows = self.query(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}

    def views(self, schema: str) -> set[str]:
        rows = self.query(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'view'")
        return {name for (name,) in rows}

    def load_staging(self, processed_dir: Path) -> dict[str, int]:
        """Charge stg.* depuis les Parquet des marts + les référentiels Python (comme load_to_db)."""
        loaded: dict[str, int] = {}
        refs = reference_frames()
        for table in ALL_STAGING:
            if table.parquet:
                path = processed_dir / table.parquet
                if not path.exists():
                    continue
                frame = pl.read_parquet(path).select(table.column_names)
            else:
                frame = refs[table.reference_loader].select(table.column_names)
            placeholders = ", ".join("?" for _ in table.columns)
            self.conn.executemany(
                f"INSERT INTO {table.qualified} ({', '.join(table.column_names)}) VALUES ({placeholders})",
                frame.rows(),
            )
            loaded[table.name] = frame.height
        self.conn.commit()
        return loaded
