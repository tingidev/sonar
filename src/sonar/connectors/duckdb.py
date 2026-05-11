"""DuckDB connector — schema discovery and data sampling.

Async via `asyncio.to_thread` against the sync `duckdb` driver (per design.md
D1). `read_only=True` is passed at connect time to allow scanning files held by
another writer; `:memory:` is exempt because DuckDB forbids read-only in-memory
connections (per design.md D7).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

# Module assumes cli._ensure_duckdb_driver() ran before this file is imported.
# Direct imports from elsewhere bypass the guard and get a raw ModuleNotFoundError.
import duckdb

from sonar.connectors import _duckdb_sql as _ddb_sql
from sonar.connectors._cursor_utils import fetch_dicts as _fetch_dicts
from sonar.connectors._cursor_utils import fetch_rows as _fetch_rows
from sonar.connectors.serialize import _serialize_row
from sonar.connectors.types import Column, ForeignKey, Table, _reject_dotted_identifier

_LOGGER = logging.getLogger("sonar.connectors.duckdb")
_CONTEXT_MANAGER_REQUIRED = "DuckDBConnector must be used as an async context manager"


class DuckDBConnector:
    """DuckDB schema discovery + sampling. Same observable contract as PostgresConnector."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Any | None = None

    async def __aenter__(self) -> DuckDBConnector:
        # DuckDB forbids read_only on :memory: — only apply it for file paths.
        read_only = self._path != ":memory:"
        self._conn = await asyncio.to_thread(duckdb.connect, self._path, read_only)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def discover_tables(self, schemas: list[str] | None = None) -> list[Table]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)
        resolved = await self._resolve_schemas(schemas)
        if not resolved:
            return []
        rows = await asyncio.to_thread(
            _fetch_dicts,
            self._conn,
            _ddb_sql.tables_and_columns_query(len(resolved)),
            resolved,
        )
        return _tables_from_rows(rows)

    async def discover_relationships(self) -> list[ForeignKey]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)
        rows = await asyncio.to_thread(_fetch_dicts, self._conn, _ddb_sql.FOREIGN_KEYS, [])
        return _foreign_keys_from_rows(rows)

    async def sample_table(self, schema: str, table: str, limit: int = 5) -> list[dict]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)
        if not isinstance(limit, int) or limit < 0:
            raise ValueError(f"limit must be a non-negative int, got {limit!r}")
        _reject_dotted_identifier("schema", schema)
        _reject_dotted_identifier("table", table)
        query = (
            f"SELECT * FROM {_quote_identifier(schema)}.{_quote_identifier(table)} "
            f"LIMIT {int(limit)}"
        )
        rows = await asyncio.to_thread(_fetch_dicts, self._conn, query, [])
        return [_serialize_row(row) for row in rows]

    async def _resolve_schemas(self, schemas: list[str] | None) -> list[str]:
        if schemas is not None:
            return schemas
        return await self._non_system_schemas()

    async def _non_system_schemas(self) -> list[str]:
        rows = await asyncio.to_thread(_fetch_rows, self._conn, _ddb_sql.NON_SYSTEM_SCHEMAS, [])
        return [r[0] for r in rows]


def _tables_from_rows(rows: list[dict]) -> list[Table]:
    tables: list[Table] = []
    current_key: tuple[str, str] | None = None
    current_columns: list[Column] = []
    current_row_count: int | None = None

    for row in rows:
        schema = row["schema"]
        table_name = row["table_name"]
        _reject_dotted_identifier("schema", schema)
        _reject_dotted_identifier("table", table_name)
        key = (schema, table_name)
        if key != current_key:
            if current_key is not None:
                tables.append(
                    Table(
                        schema=current_key[0],
                        name=current_key[1],
                        columns=tuple(current_columns),
                        row_count=current_row_count,
                    )
                )
            current_key = key
            current_columns = []
            current_row_count = _row_count_from_row(row)
        current_columns.append(_column_from_row(row))

    if current_key is not None:
        tables.append(
            Table(
                schema=current_key[0],
                name=current_key[1],
                columns=tuple(current_columns),
                row_count=current_row_count,
            )
        )

    return tables


def _row_count_from_row(row: dict) -> int | None:
    raw = row["row_count"]
    if raw is None:
        return None
    return int(raw)


def _column_from_row(row: dict) -> Column:
    return Column(
        name=row["column_name"],
        data_type=row["data_type"],
        nullable=(row["is_nullable"] == "YES"),
        is_primary_key=bool(row["is_primary_key"]),
        default=row["column_default"],
    )


def _foreign_keys_from_rows(rows: list[dict]) -> list[ForeignKey]:
    result: list[ForeignKey] = []
    for row in rows:
        source_schema = row["source_schema"]
        source_table = row["source_table"]
        source_column = row["source_column"]
        target_schema = row["target_schema"]
        target_table = row["target_table"]
        target_column = row["target_column"]

        _reject_dotted_identifier("source schema", source_schema)
        _reject_dotted_identifier("source table", source_table)
        _reject_dotted_identifier("target schema", target_schema)
        _reject_dotted_identifier("target table", target_table)

        result.append(
            ForeignKey(
                source_schema=source_schema,
                source_table=source_table,
                source_column=source_column,
                target_schema=target_schema,
                target_table=target_table,
                target_column=target_column,
            )
        )
    return result


def _quote_identifier(name: str) -> str:
    """DuckDB identifier quoting: wrap in double quotes, escape internal quotes."""
    if "\x00" in name:
        raise ValueError(f"identifier contains null byte: {name!r}")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
