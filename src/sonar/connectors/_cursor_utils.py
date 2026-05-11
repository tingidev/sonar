"""Shared cursor utilities for connectors that use a synchronous cursor API.

Used by DuckDB and Snowflake connectors, which wrap a sync driver via
`asyncio.to_thread`. Both expose the same cursor protocol: `.cursor()`,
`.execute(query, params)`, `.description`, `.fetchall()`, `.close()`.
"""

from __future__ import annotations

from typing import Any, Sequence


def fetch_dicts(conn: Any, query: str, params: Sequence) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def fetch_rows(conn: Any, query: str, params: Sequence) -> list[tuple]:
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        cur.close()
