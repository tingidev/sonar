"""Sample tool — live-DB-backed MCP tool for shape-recognition sampling.

Returns a small number of rows from a given table with safeguards:
- unknown-table rejection (only tables present in the bundle can be queried)
- server-side row cap (reject, don't clamp)
- injection-safe identifier quoting via `psycopg.sql.Identifier`
- default PII stripping (`pii_risk in {high, medium}`) unless `allow_pii=True`
- every call emits an audit record, including rejections and DB errors
- DSN credentials are scrubbed from any exception text before re-raising
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import psycopg
import psycopg.sql as _pgsql
from psycopg.rows import dict_row

from sonar._dsn import scrub_dsn
from sonar.connectors.postgres import _coerce_value
from sonar.engine.describe import PIIRisk
from sonar.index.bundle import ContextBundle
from sonar.mcp.audit import emit_sample_audit
from sonar.mcp.tools.bundle_tools import ToolError

DEFAULT_SAMPLE_ROWS = 5
MAX_SAMPLE_ROWS = 20

_PROTECTED_PII = frozenset({PIIRisk.HIGH, PIIRisk.MEDIUM})


def make_sample_tool(
    bundle: ContextBundle,
    dsn: str,
    allow_pii: bool = False,
) -> Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]]:
    """Return an async `sample(schema, table, limit=None)` bound to *dsn*."""
    bundle_keys = frozenset((t.schema, t.name) for t in bundle.tables)

    async def sample(
        schema: str,
        table: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        requested = limit if limit is not None else DEFAULT_SAMPLE_ROWS

        if requested > MAX_SAMPLE_ROWS:
            emit_sample_audit(
                outcome="rejected_cap",
                schema=schema, table=table,
                limit_requested=requested,
                limit_effective=None, rows_returned=None,
            )
            raise ToolError(
                f"sample limit {requested} exceeds cap of {MAX_SAMPLE_ROWS}; "
                f"pass limit <= {MAX_SAMPLE_ROWS}"
            )

        if (schema, table) not in bundle_keys:
            emit_sample_audit(
                outcome="rejected_unknown_table",
                schema=schema, table=table,
                limit_requested=requested,
                limit_effective=None, rows_returned=None,
            )
            raise ToolError(f"table {schema}.{table} is not in the bundle")

        protected = (
            set() if allow_pii
            else _protected_column_names(bundle, schema, table)
        )

        try:
            raw_rows = await _fetch_rows(dsn, schema, table, requested)
        except ToolError:
            emit_sample_audit(
                outcome="db_error",
                schema=schema, table=table,
                limit_requested=requested, limit_effective=requested,
                rows_returned=None,
            )
            raise

        rows = _redact_rows(raw_rows, protected)
        emit_sample_audit(
            outcome="ok",
            schema=schema, table=table,
            limit_requested=requested, limit_effective=requested,
            rows_returned=len(rows),
        )
        return rows

    return sample


async def _fetch_rows(
    dsn: str,
    schema: str,
    table: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = _pgsql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
        _pgsql.Identifier(schema),
        _pgsql.Identifier(table),
        _pgsql.Literal(limit),
    )
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query)
                return await cur.fetchall()
    except Exception as exc:
        scrubbed = scrub_dsn(f"{type(exc).__name__}: {exc}", dsn)
        raise ToolError(scrubbed) from None


def _redact_rows(
    raw_rows: list[dict[str, Any]],
    protected_columns: set[str],
) -> list[dict[str, Any]]:
    return [
        {
            key: (None if key in protected_columns else _coerce_value(value))
            for key, value in row.items()
        }
        for row in raw_rows
    ]


def _protected_column_names(
    bundle: ContextBundle,
    schema: str,
    table: str,
) -> set[str]:
    description = bundle.descriptions.get((schema, table))
    if description is None:
        return set()
    return {c.name for c in description.columns if c.pii_risk in _PROTECTED_PII}
