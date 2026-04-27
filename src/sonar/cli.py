"""CLI entrypoint for Sonar."""

from __future__ import annotations

import argparse
import asyncio
import datetime as _datetime
import sys
from pathlib import Path

from sonar._dsn import scrub_dsn
from sonar.connectors.postgres import PostgresConnector
from sonar.engine.describe import DescriptionEngine
from sonar.engine.llm import AnthropicClient, LLMConfig
from sonar.index.bundle import (
    SCHEMA_VERSION,
    BundleIntegrityError,
    BundleMeta,
    BundleVersionError,
    ContextBundle,
    format_database_label,
)
from sonar.index.store import ContextStore
from sonar.mcp.server import build_server, run_stdio
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
    scan_parser.add_argument(
        "--concurrency",
        dest="concurrency",
        type=int,
        default=None,
        help="Max concurrent LLM calls during description (default: 5)",
    )

    serve_parser = subparsers.add_parser("serve", help="Start the MCP server over stdio")
    serve_parser.add_argument(
        "dsn",
        nargs="?",
        default=None,
        help=(
            "Database connection string (psycopg DSN). When omitted, the server "
            "runs in bundle-only mode and the sample tool is not registered."
        ),
    )
    serve_parser.add_argument(
        "--bundle-dir",
        dest="bundle_dir",
        default=".sonar",
        help="Directory containing the context bundle (default: .sonar/)",
    )
    serve_parser.add_argument(
        "--allow-pii",
        dest="allow_pii",
        action="store_true",
        help=(
            "Disable default PII-stripping in sample results. Only use in "
            "operator-authorised environments; every sample call is audited "
            "regardless of this flag."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "scan":
        return _run_scan(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.print_help()
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    dsn = args.dsn or args.url
    if not dsn:
        print("scan: DSN required (positional argument or --url)", file=sys.stderr)
        return 2

    bundle_dir = Path(args.bundle_dir)

    try:
        bundle = asyncio.run(_scan_pipeline(dsn, concurrency=args.concurrency))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        # psycopg's OperationalError embeds the full connection string in its
        # str(), which would leak a password if one was in the DSN. Scrub the
        # DSN out of the rendered message before it reaches stderr.
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", dsn)
        print(f"scan failed: {message}", file=sys.stderr)
        return 1

    ContextStore(bundle_dir).write(bundle)
    return 0


async def _scan_pipeline(dsn: str, *, concurrency: int | None = None) -> ContextBundle:
    async with PostgresConnector(dsn) as conn:
        tables = await conn.discover_tables()
        foreign_keys = await conn.discover_relationships()
        samples: dict[tuple[str, str], list[dict]] = {}
        for table in tables:
            samples[(table.schema, table.name)] = await conn.sample_table(
                table.schema, table.name
            )

        config = LLMConfig(max_concurrent_calls=concurrency) if concurrency else LLMConfig()
        engine = DescriptionEngine(AnthropicClient(config), config)
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
        database=format_database_label(dsn),
    )

    return ContextBundle(
        meta=meta,
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=tuple(relationships),
    )


def _run_serve(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir)
    dsn = args.dsn

    try:
        bundle = ContextStore(bundle_dir).read()
    except (BundleIntegrityError, BundleVersionError) as exc:
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", dsn)
        print(f"serve failed: {message}", file=sys.stderr)
        return 1

    if bundle is None:
        print(
            f"serve: no bundle found at {bundle_dir}; run `sonar scan` first",
            file=sys.stderr,
        )
        return 1

    app = build_server(bundle, dsn, allow_pii=bool(args.allow_pii))
    try:
        run_stdio(app)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", dsn)
        print(f"serve failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
