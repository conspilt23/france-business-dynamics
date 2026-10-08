"""Lecture du DDL réel (sql/*.sql) via sqlglot : colonnes, types, longueurs, nullabilité."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from france_business_dynamics.utils.config import PROJECT_ROOT
from france_business_dynamics.sql_runner import split_batches

SQL_DIR = PROJECT_ROOT / "sql"

# Par NOM de type sqlglot (stable entre versions) → nom de type SQL Server.
TYPE_NAMES = {
    "VARCHAR": "varchar", "NVARCHAR": "nvarchar", "CHAR": "char", "INT": "int", "BIGINT": "bigint",
    "SMALLINT": "smallint", "TINYINT": "tinyint", "UTINYINT": "tinyint",  # tinyint T-SQL = non signé
    "DATE": "date", "BIT": "bit",
}


def parse_tables(filename: str) -> dict[str, dict[str, tuple]]:
    """`schema.Table` → {colonne: (type, longueur, nullable)} d'après le DDL réel."""
    tables: dict[str, dict[str, tuple]] = {}
    for batch in split_batches((SQL_DIR / filename).read_text(encoding="utf-8")):
        for statement in sqlglot.parse(batch, read="tsql"):
            if not isinstance(statement, exp.Create) or (statement.args.get("kind") or "").upper() != "TABLE":
                continue
            schema = statement.this
            table = schema.this
            columns: dict[str, tuple] = {}
            for definition in schema.expressions:
                if not isinstance(definition, exp.ColumnDef):
                    continue
                datatype = definition.args["kind"]
                length = None
                if datatype.expressions:
                    length = int(datatype.expressions[0].this.this)
                # sqlglot représente un `NULL` explicite comme NotNullColumnConstraint(allow_null=True)
                not_null = any(
                    isinstance(c.kind, exp.NotNullColumnConstraint) and not c.kind.args.get("allow_null")
                    for c in definition.args.get("constraints", [])
                )
                columns[definition.name] = (TYPE_NAMES[datatype.this.value], length, not_null)  # 3e valeur : NOT NULL ?
            tables[f"{table.db}.{table.name}"] = columns
    return tables




def live_schema_from_ddl(filename: str = "04_create_facts.sql") -> dict[str, list[tuple]]:
    """Ce que renverrait INFORMATION_SCHEMA.COLUMNS si 04 avait été exécuté : table → [(col, type, longueur, YES/NO)]."""
    live: dict[str, list[tuple]] = {}
    for qualified, columns in parse_tables(filename).items():
        schema, _, name = qualified.partition(".")
        if schema != "stg":
            continue
        live[name] = [
            (column, sql_type, length if sql_type in ("varchar", "nvarchar", "char") else None,
             "NO" if not_null else "YES")
            for column, (sql_type, length, not_null) in columns.items()
        ]
    return live
