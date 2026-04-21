"""Postgres connector — schema discovery and data sampling."""

import datetime as _datetime
import decimal as _decimal
import uuid as _uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql as _pgsql
from psycopg.rows import dict_row

from sonar.connectors import _sql

_CONTEXT_MANAGER_REQUIRED = "PostgresConnector must be used as an async context manager"


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    foreign_key: str | None = None
    default: str | None = None


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    columns: tuple[Column, ...]
    row_count: int | None = None


@dataclass(frozen=True)
class ForeignKey:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str


class PostgresConnector:
    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._conn: psycopg.AsyncConnection | None = None

    async def __aenter__(self) -> "PostgresConnector":
        self._conn = await psycopg.AsyncConnection.connect(self._connection_string)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def discover_tables(self, schemas: list[str] | None = None) -> list[Table]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        resolved = schemas if schemas is not None else await self._non_system_schemas()
        if not resolved:
            return []

        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(_sql.TABLES_AND_COLUMNS, {"schemas": resolved})
            rows = await cur.fetchall()

        tables: list[Table] = []
        current_key: tuple[str, str] | None = None
        current_columns: list[Column] = []

        for row in rows:
            key = (row["schema"], row["table_name"])
            if key != current_key:
                if current_key is not None:
                    tables.append(
                        Table(
                            schema=current_key[0],
                            name=current_key[1],
                            columns=tuple(current_columns),
                        )
                    )
                current_key = key
                current_columns = []
            current_columns.append(_column_from_row(row))

        if current_key is not None:
            tables.append(
                Table(
                    schema=current_key[0],
                    name=current_key[1],
                    columns=tuple(current_columns),
                )
            )

        return tables

    async def discover_relationships(self) -> list[ForeignKey]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(_sql.FOREIGN_KEYS)
            rows = await cur.fetchall()

        return [
            ForeignKey(
                source_schema=row["source_schema"],
                source_table=row["source_table"],
                source_column=row["source_column"],
                target_schema=row["target_schema"],
                target_table=row["target_table"],
                target_column=row["target_column"],
            )
            for row in rows
        ]

    async def sample_table(self, schema: str, table: str, limit: int = 5) -> list[dict]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        query = _pgsql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
            _pgsql.Identifier(schema),
            _pgsql.Identifier(table),
            _pgsql.Literal(limit),
        )
        async with self._conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query)
            rows = await cur.fetchall()

        return [_serialize_row(row) for row in rows]

    async def _non_system_schemas(self) -> list[str]:
        assert self._conn is not None
        query = """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema')
              AND schema_name NOT LIKE 'pg\\_%' ESCAPE '\\'
            ORDER BY schema_name
        """
        async with self._conn.cursor() as cur:
            await cur.execute(query)
            rows = await cur.fetchall()
        return [r[0] for r in rows]


def _serialize_row(row: dict) -> dict:
    return {k: _coerce_value(v) for k, v in row.items()}


def _coerce_value(value: Any) -> Any:
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (_datetime.datetime, _datetime.date)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return "<binary>"
    return value


def _column_from_row(row: dict) -> Column:
    raw_type = row["data_type"]
    data_type = row["udt_name"] if raw_type in ("ARRAY", "USER-DEFINED") else raw_type
    return Column(
        name=row["column_name"],
        data_type=data_type,
        nullable=(row["is_nullable"] == "YES"),
        is_primary_key=bool(row["is_primary_key"]),
        default=row["column_default"],
    )
