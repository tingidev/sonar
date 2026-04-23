"""CLI entrypoint for Sonar."""

from __future__ import annotations

import argparse
import asyncio
import datetime as _datetime
import sys
from pathlib import Path

from sonar.connectors.postgres import PostgresConnector
from sonar.engine.describe import DescriptionEngine
from sonar.engine.llm import AnthropicClient
from sonar.index.bundle import (
    SCHEMA_VERSION,
    BundleMeta,
    ContextBundle,
    _format_database_label,
)
from sonar.index.store import ContextStore
from sonar.relationships import map_relationships


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sonar - data context for AI agents")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Discover and describe a data source")
    scan_parser.add_argument(
        "dsn",
        nargs="?",
        default=None,
        help="Database connection string (psycopg DSN)",
    )
    scan_parser.add_argument(
        "--url",
        dest="url",
        default=None,
        help="Database connection string (alias for the positional DSN)",
    )
    scan_parser.add_argument(
        "--bundle-dir",
        dest="bundle_dir",
        default=".sonar",
        help="Directory where the context bundle is written (default: .sonar/)",
    )

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args(argv)

    if args.command == "scan":
        return _run_scan(args)
    if args.command == "serve":
        print("Starting Sonar MCP server...")
        return 0
    parser.print_help()
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    dsn = args.dsn or args.url
    if not dsn:
        print("scan: DSN required (positional argument or --url)", file=sys.stderr)
        return 2

    bundle_dir = Path(args.bundle_dir)

    try:
        bundle = asyncio.run(_scan_pipeline(dsn))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"scan failed: {exc}", file=sys.stderr)
        return 1

    ContextStore(bundle_dir).write(bundle)
    return 0


async def _scan_pipeline(dsn: str) -> ContextBundle:
    async with PostgresConnector(dsn) as conn:
        tables = await conn.discover_tables()
        foreign_keys = await conn.discover_relationships()
        samples: dict[tuple[str, str], list[dict]] = {}
        for table in tables:
            samples[(table.schema, table.name)] = await conn.sample_table(
                table.schema, table.name
            )

        engine = DescriptionEngine(AnthropicClient())
        descriptions = await engine.describe_database(tables, samples)

    relationships = map_relationships(tables, foreign_keys)

    generated_at = (
        _datetime.datetime.now(_datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    meta = BundleMeta(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        connector="postgres",
        database=_format_database_label(dsn),
    )

    return ContextBundle(
        meta=meta,
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=tuple(relationships),
    )


if __name__ == "__main__":
    raise SystemExit(main())
