"""Connexion factice au contrat de pyodbc, adossée à SQLite (tests de load_to_db / sql_runner).

* `TRUNCATE TABLE x` est traduit en `DELETE FROM x` ; `INFORMATION_SCHEMA.COLUMNS` est servi depuis le DDL RÉEL.
* Des pannes peuvent être injectées (ex. la troncature historique « length 14 buffer 6 »).
"""

from __future__ import annotations

import re

from tests.support.tsql_sqlite import SqliteEmulator


class FakeOdbcError(Exception):
    """Joue le rôle de pyodbc.Error."""


TRUNCATION_MESSAGE = (
    "('22001', '[22001] [Microsoft][ODBC Driver 18 for SQL Server]String data, right truncation: length 14 buffer 6 (0) (SQLExecute)')"
)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self._cursor = connection.emulator.conn.cursor()
        self._rows: list[tuple] = []
        self.fast_executemany = False
        self.description = None

    def execute(self, sql: str, *params):
        self._connection.executed.append(sql)
        truncate = re.match(r"\s*TRUNCATE TABLE (\S+)", sql, re.IGNORECASE)
        if truncate:
            self._cursor.execute(f"DELETE FROM {truncate.group(1)}")
            return self
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            self._rows = list(self._connection.live_schema.get(params[0], []))
            return self
        self._cursor.execute(sql, params)
        self.description = self._cursor.description
        self._rows = self._cursor.fetchall() if self._cursor.description else []
        return self

    def executemany(self, sql: str, rows):
        connection = self._connection
        connection.batches += 1
        rows = list(rows)
        if connection.fail_on_batch == connection.batches:
            self._cursor.executemany(sql, rows[:1])  # écriture partielle avant la panne
            raise FakeOdbcError(connection.fail_message)
        if connection.drop_rows:
            rows = rows[: max(len(rows) - connection.drop_rows, 0)]  # perte silencieuse simulée
        self._cursor.executemany(sql, rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def nextset(self):
        return False


class FakeConnection:
    def __init__(self, emulator: SqliteEmulator, live_schema: dict[str, list[tuple]]) -> None:
        self.emulator = emulator
        self.live_schema = live_schema
        self.executed: list[str] = []
        self.batches = 0
        self.fail_on_batch: int | None = None
        self.fail_message = TRUNCATION_MESSAGE
        self.drop_rows = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.emulator.conn.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.emulator.conn.rollback()

    def close(self) -> None:
        self.closed = True
