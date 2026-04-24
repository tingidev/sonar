"""MCP server bootstrap.

Wires the four bundle-backed tools (and optionally the `sample` live-DB tool)
into a `FastMCP` app. Kept thin so tests can call `build_server` directly
without opening a stdio transport.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from sonar.index.bundle import ContextBundle
from sonar.mcp.tools.bundle_tools import (
    describe_tool,
    discover_tool,
    relationships_tool,
    search_tool,
)


def build_server(
    bundle: ContextBundle,
    dsn: str | None,
    allow_pii: bool = False,
) -> FastMCP:
    """Return a `FastMCP` app wired to the given bundle.

    The four bundle-backed tools are registered unconditionally. The `sample`
    tool is registered only when `dsn is not None` — bundle-only mode exposes
    a stateless, credential-free tool surface suitable for Layer 2 artifact
    sharing (see design D2).
    """
    app = FastMCP("sonar")

    @app.tool(
        name="discover",
        description="List tables in the bundle, optionally filtered by schema.",
    )
    def discover(schema: str | None = None) -> list[dict[str, Any]]:
        return discover_tool(bundle, schema=schema)

    @app.tool(
        name="describe",
        description="Return the joined semantic description of a single table.",
    )
    def describe(schema: str, table: str) -> dict[str, Any]:
        return describe_tool(bundle, schema, table)

    @app.tool(
        name="relationships",
        description="Return bundle relationships incident on a table.",
    )
    def relationships(
        schema: str,
        table: str,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        return relationships_tool(bundle, schema, table, direction=direction)

    @app.tool(
        name="search",
        description="Case-insensitive substring search across tables, columns, and descriptions.",
    )
    def search(query: str, limit: int = 20) -> list[dict[str, Any]]:
        return search_tool(bundle, query, limit=limit)

    if dsn is not None:
        from sonar.mcp.tools.sample_tool import make_sample_tool

        sample_callable = make_sample_tool(bundle, dsn, allow_pii=allow_pii)

        @app.tool(
            name="sample",
            description=(
                "Return a small number of rows from a live table, with PII-flagged "
                "columns redacted unless the server was started with --allow-pii."
            ),
        )
        async def sample(
            schema: str,
            table: str,
            limit: int | None = None,
        ) -> list[dict[str, Any]]:
            return await sample_callable(schema, table, limit=limit)

    return app


def run_stdio(app: FastMCP) -> None:
    """Run the FastMCP app on stdio until the transport closes.

    Thin wrapper kept in one place so the stdio lifecycle is a single line
    of test-exempt code. Unit tests construct `app` via `build_server` and
    exercise the registered tools directly without touching this runner.
    """
    app.run(transport="stdio")
