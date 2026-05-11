"""Snowflake connector — schema discovery and data sampling.

Async via `asyncio.to_thread` against the sync `snowflake-connector-python`
driver (per design.md D5). 2-level identifiers — the database is bound at
connect time and not carried on `Table`/`ForeignKey` (per design.md D2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

# Module assumes cli._ensure_snowflake_driver() ran before this file is imported.
# Direct imports from elsewhere bypass the guard and get a raw ModuleNotFoundError.
import snowflake.connector

from sonar.connectors import _snowflake_sql as _sf_sql
from sonar.connectors._cursor_utils import fetch_dicts as _fetch_dicts
from sonar.connectors._cursor_utils import fetch_rows as _fetch_rows
from sonar.connectors.serialize import _serialize_row
from sonar.connectors.types import Column, ForeignKey, Table, _reject_dotted_identifier

_LOGGER = logging.getLogger("sonar.connectors.snowflake")
_CONTEXT_MANAGER_REQUIRED = "SnowflakeConnector must be used as an async context manager"


class SnowflakeConnector:
    """Snowflake schema discovery + sampling. Same observable contract as PostgresConnector."""

    def __init__(self, connect_kwargs: dict[str, Any]) -> None:
        for required in ("account", "user", "database"):
            if not connect_kwargs.get(required):
                raise ValueError(f"snowflake connector requires {required!r} in connect_kwargs")
        self._connect_kwargs: dict[str, Any] = dict(connect_kwargs)
        self._conn: Any | None = None
        self._row_count_available: bool = True
        self.cross_database_foreign_keys_dropped: int = 0

    @property
    def database(self) -> str:
        return self._connect_kwargs["database"]

    @property
    def default_schema(self) -> str | None:
        return self._connect_kwargs.get("schema")

    async def __aenter__(self) -> SnowflakeConnector:
        self._conn = await asyncio.to_thread(snowflake.connector.connect, **self._connect_kwargs)
        self._row_count_available = await self._probe_row_count_available()
        return self

    async def _probe_row_count_available(self) -> bool:
        rows = await asyncio.to_thread(
            _fetch_rows, self._conn, _sf_sql.ROW_COUNT_AVAILABLE_PROBE, ()
        )
        return bool(rows and rows[0][0])

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def discover_tables(self, schemas: list[str] | None = None) -> list[Table]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        resolved = await self._resolve_schemas(schemas)
        if not resolved:
            return []

        try:
            rows = await asyncio.to_thread(
                _fetch_dicts,
                self._conn,
                _sf_sql.tables_and_columns_query(
                    len(resolved), has_row_count=self._row_count_available
                ),
                tuple(resolved),
            )
        except snowflake.connector.errors.ProgrammingError:
            # Shared/imported databases (e.g. SNOWFLAKE_SAMPLE_DATA) don't
            # expose KEY_COLUMN_USAGE. Fall back to discovery without PK info.
            _LOGGER.warning(
                "constraint views not accessible on database %r; " "primary key detection disabled",
                self.database,
            )
            rows = await asyncio.to_thread(
                _fetch_dicts,
                self._conn,
                _sf_sql.tables_and_columns_query(
                    len(resolved),
                    has_row_count=self._row_count_available,
                    has_pk_views=False,
                ),
                tuple(resolved),
            )
        return _tables_from_rows(rows)

    async def discover_relationships(self) -> list[ForeignKey]:
        if self._conn is None:
            raise RuntimeError(_CONTEXT_MANAGER_REQUIRED)

        try:
            rows = await asyncio.to_thread(_fetch_dicts, self._conn, _sf_sql.FOREIGN_KEYS, ())
        except snowflake.connector.errors.ProgrammingError:
            _LOGGER.warning(
                "constraint views not accessible on database %r; " "foreign key discovery disabled",
                self.database,
            )
            return []
        result, dropped = _foreign_keys_from_rows(rows, self.database)
        self.cross_database_foreign_keys_dropped = dropped
        if dropped:
            _LOGGER.warning(
                "%d foreign key column(s) reference tables outside database %r and were excluded",
                dropped,
                self.database,
            )
        return result

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
        rows = await asyncio.to_thread(_fetch_dicts, self._conn, query, ())
        return [_serialize_row(row) for row in rows]

    async def _resolve_schemas(self, schemas: list[str] | None) -> list[str]:
        if schemas is not None:
            return schemas
        if self.default_schema is not None:
            return [self.default_schema]
        return await self._non_system_schemas()

    async def _non_system_schemas(self) -> list[str]:
        rows = await asyncio.to_thread(_fetch_rows, self._conn, _sf_sql.NON_SYSTEM_SCHEMAS, ())
        return [r[0] for r in rows]


def _tables_from_rows(rows: list[dict]) -> list[Table]:
    tables: list[Table] = []
    current_key: tuple[str, str] | None = None
    current_columns: list[Column] = []
    current_row_count: int | None = None

    for row in rows:
        schema = _row_get(row, "schema")
        table_name = _row_get(row, "table_name")
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
    raw = _row_get(row, "row_count")
    if raw is None:
        return None
    return int(raw)


def _column_from_row(row: dict) -> Column:
    return Column(
        name=_row_get(row, "column_name"),
        data_type=_row_get(row, "data_type"),
        nullable=(_row_get(row, "is_nullable") == "YES"),
        is_primary_key=bool(_row_get(row, "is_primary_key")),
        default=_row_get(row, "column_default"),
    )


def _foreign_keys_from_rows(rows: list[dict], bound_database: str) -> tuple[list[ForeignKey], int]:
    result: list[ForeignKey] = []
    dropped = 0
    for row in rows:
        target_database = _row_get(row, "target_database")
        target_schema = _row_get(row, "target_schema")
        target_table = _row_get(row, "target_table")
        target_column = _row_get(row, "target_column")

        # Snowflake unquoted identifiers fold case; INFORMATION_SCHEMA can
        # return the database name in different case from what the user supplied
        # to connect(). Compare case-insensitively.
        cross_db = target_database is not None and target_database.upper() != bound_database.upper()
        if cross_db or target_schema is None or target_table is None or target_column is None:
            dropped += 1
            continue

        source_schema = _row_get(row, "source_schema")
        source_table = _row_get(row, "source_table")
        source_column = _row_get(row, "source_column")

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
    return result, dropped


def _row_get(row: dict, key: str) -> Any:
    # Tries lowercase then UPPER. Breaks if a driver returns mixed-case column
    # names (neither lowercase nor UPPER); no known Snowflake driver does this.
    if key in row:
        return row[key]
    upper = key.upper()
    if upper in row:
        return row[upper]
    raise KeyError(key)


def _quote_identifier(name: str) -> str:
    """Snowflake identifier quoting: wrap in double quotes, escape internal quotes."""
    if "\x00" in name:
        raise ValueError(f"identifier contains null byte: {name!r}")
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
