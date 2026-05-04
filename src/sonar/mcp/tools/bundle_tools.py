"""Bundle-backed MCP tools — pure functions of (bundle, args).

Four tools read the loaded `ContextBundle` in-process and never open a DB
connection: `discover`, `describe`, `relationships`, `search`. Composed into
the server via `functools.partial(fn, bundle)` so they appear to the client
as single-argument tools.
"""

from __future__ import annotations

from typing import Any

from sonar.index.bundle import ContextBundle
from sonar.search import search_tool as search_tool


class ToolError(Exception):
    """Raised by a bundle-backed tool when the caller's arguments are invalid.

    The MCP layer surfaces this as a tool-level error so the agent can
    distinguish 'tool argument wrong' from 'tool returned empty'.
    """


def discover_tool(
    bundle: ContextBundle,
    schema: str | None = None,
) -> list[dict[str, Any]]:
    """Return one entry per table in the bundle, optionally filtered by schema.

    Each entry has `schema`, `name`, and `row_count` (null if the bundle did
    not record one).
    """
    out: list[dict[str, Any]] = []
    for table in bundle.tables:
        if schema is not None and table.schema != schema:
            continue
        out.append(
            {
                "schema": table.schema,
                "name": table.name,
                "row_count": table.row_count,
            }
        )
    return out


def describe_tool(
    bundle: ContextBundle,
    schema: str,
    table: str,
) -> dict[str, Any]:
    """Return the joined (tables ⋈ descriptions) view of a single table.

    On a null description slot, the column shape is returned with description
    fields explicitly `null` (not omitted). On an unknown `(schema, table)`,
    a `ToolError` is raised for the MCP layer to surface as a tool-level error.
    """
    key = (schema, table)
    table_obj = None
    for t in bundle.tables:
        if (t.schema, t.name) == key:
            table_obj = t
            break
    if table_obj is None:
        raise ToolError(f"unknown table: {schema}.{table}")

    description = bundle.descriptions.get(key)

    columns: list[dict[str, Any]] = []
    desc_columns_by_name: dict[str, Any] = {}
    if description is not None:
        desc_columns_by_name = {c.name: c for c in description.columns}

    for col in table_obj.columns:
        desc = desc_columns_by_name.get(col.name)
        columns.append(
            {
                "name": col.name,
                "data_type": col.data_type,
                "nullable": col.nullable,
                "is_primary_key": col.is_primary_key,
                "description": desc.description if desc is not None else None,
                "semantic_type": (desc.semantic_type.value if desc is not None else None),
                "pii_risk": desc.pii_risk.value if desc is not None else None,
                "confidence": desc.confidence if desc is not None else None,
            }
        )

    return {
        "schema": table_obj.schema,
        "name": table_obj.name,
        "row_count": table_obj.row_count,
        "description": description.description if description is not None else None,
        "grain": description.grain if description is not None else None,
        "domain_hints": (list(description.domain_hints) if description is not None else None),
        "confidence": description.confidence if description is not None else None,
        "columns": columns,
    }


def relationships_tool(
    bundle: ContextBundle,
    schema: str,
    table: str,
    direction: str = "both",
) -> list[dict[str, Any]]:
    """Return bundle relationships incident on `(schema, table)`.

    `direction` restricts to `outgoing` (source = target table), `incoming`
    (target = target table), or `both` (default). Any other value raises
    `ToolError`.
    """
    if direction not in ("outgoing", "incoming", "both"):
        raise ToolError(
            f"invalid direction: {direction!r} (expected 'outgoing', 'incoming', or 'both')"
        )

    out: list[dict[str, Any]] = []
    for rel in bundle.relationships:
        outgoing = (rel.source_schema, rel.source_table) == (schema, table)
        incoming = (rel.target_schema, rel.target_table) == (schema, table)
        include = (
            (direction == "outgoing" and outgoing)
            or (direction == "incoming" and incoming)
            or (direction == "both" and (outgoing or incoming))
        )
        if not include:
            continue
        out.append(
            {
                "source_schema": rel.source_schema,
                "source_table": rel.source_table,
                "source_column": rel.source_column,
                "target_schema": rel.target_schema,
                "target_table": rel.target_table,
                "target_column": rel.target_column,
                "kind": rel.kind.value,
            }
        )
    return out


__all__ = [
    "ToolError",
    "discover_tool",
    "describe_tool",
    "relationships_tool",
    "search_tool",
]
