"""Sample tool — live-DB-backed MCP tool for shape-recognition sampling.

Returns a small number of rows from a given table with safeguards:
- server-side row cap (reject, don't clamp)
- injection-safe identifier quoting via `psycopg.sql.Identifier`
- default PII stripping (`pii_risk ∈ {high, medium}`) unless `allow_pii=True`
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
    """Return an async `sample(schema, table, limit=None)` callable bound to `dsn`.

    The returned callable closes over the bundle (for PII lookups) and the DSN
    (for per-call connections). The DSN is never echoed to the caller — on
    connection failure the DSN substring is scrubbed from the exception text
    before re-raising.

    When `allow_pii=True`, the returned callable passes all columns through
    unredacted. When `allow_pii=False` (default), columns whose bundle
    `pii_risk` is `HIGH` or `MEDIUM` are replaced with JSON `null` in each row.
    Columns without a bundle description (null description slot, or new column
    added since last scan) pass through unredacted.
    """

    async def sample(
        schema: str,
        table: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        requested = limit if limit is not None else DEFAULT_SAMPLE_ROWS
        if requested > MAX_SAMPLE_ROWS:
            emit_sample_audit(
                outcome="rejected_cap",
                schema=schema,
                table=table,
                limit_requested=requested,
                limit_effective=None,
                rows_returned=None,
            )
            raise ToolError(
                f"sample limit {requested} exceeds cap of {MAX_SAMPLE_ROWS}; "
                f"pass limit <= {MAX_SAMPLE_ROWS}"
            )

        protected_columns = (
            set()
            if allow_pii
            else _protected_column_names(bundle, schema, table)
        )

        query = _pgsql.SQL("SELECT * FROM {}.{} LIMIT {}").format(
            _pgsql.Identifier(schema),
            _pgsql.Identifier(table),
            _pgsql.Literal(requested),
        )

        try:
            async with await psycopg.AsyncConnection.connect(dsn) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(query)
                    raw_rows = await cur.fetchall()
        except Exception as exc:
            emit_sample_audit(
                outcome="db_error",
                schema=schema,
                table=table,
                limit_requested=requested,
                limit_effective=requested,
                rows_returned=None,
            )
            scrubbed = scrub_dsn(f"{type(exc).__name__}: {exc}", dsn)
            raise ToolError(scrubbed) from None

        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            rows.append(
                {
                    key: (None if key in protected_columns else _coerce_value(value))
                    for key, value in row.items()
                }
            )

        emit_sample_audit(
            outcome="ok",
            schema=schema,
            table=table,
            limit_requested=requested,
            limit_effective=requested,
            rows_returned=len(rows),
        )
        return rows

    return sample


def _protected_column_names(
    bundle: ContextBundle,
    schema: str,
    table: str,
) -> set[str]:
    """Return the set of columns flagged `pii_risk ∈ {HIGH, MEDIUM}` for this table.

    Missing description (null slot, or table not in bundle) yields an empty set:
    every column passes through unredacted. This matches the spec's "column
    without classification" scenario.
    """
    description = bundle.descriptions.get((schema, table))
    if description is None:
        return set()
    return {c.name for c in description.columns if c.pii_risk in _PROTECTED_PII}
