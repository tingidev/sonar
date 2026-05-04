"""CLI entrypoint for Sonar."""

from __future__ import annotations

import argparse
import asyncio
import datetime as _datetime
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

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

_ACCEPTED_FORMS = (
    "postgresql://...",
    "postgres://...",
    "snowflake://USER:PASS@ACCOUNT/DATABASE/SCHEMA[?warehouse=...&role=...]",
    "snowflake (bare keyword; reads SNOWFLAKE_* env vars)",
)

_SNOWFLAKE_ENV_TO_KWARG: dict[str, str] = {
    "SNOWFLAKE_ACCOUNT": "account",
    "SNOWFLAKE_USER": "user",
    "SNOWFLAKE_AUTHENTICATOR": "authenticator",
    "SNOWFLAKE_PASSWORD": "password",
    "SNOWFLAKE_PRIVATE_KEY_PATH": "private_key_file",
    "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE": "private_key_file_pwd",
    "SNOWFLAKE_TOKEN": "token",
    "SNOWFLAKE_DATABASE": "database",
    "SNOWFLAKE_SCHEMA": "schema",
    "SNOWFLAKE_WAREHOUSE": "warehouse",
    "SNOWFLAKE_ROLE": "role",
}

_SNOWFLAKE_INSTALL_HINT = (
    "Snowflake driver not installed. Install with: pip install 'sonar[snowflake]'"
)


class _DispatchError(Exception):
    """Raised when the positional argument cannot be mapped to a connector."""


@dataclass
class _ConnectorSpec:
    connector: Any
    connector_type: str
    database_label: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sonar - data context for AI agents")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Discover and describe a data source")
    scan_parser.add_argument(
        "dsn",
        nargs="?",
        default=None,
        help=(
            "Connection target. Accepted forms: "
            + "; ".join(_ACCEPTED_FORMS)
        ),
    )
    scan_parser.add_argument(
        "--url",
        dest="url",
        default=None,
        help="Connection target (alias for the positional argument)",
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
            "Connection target. When omitted, the server runs in bundle-only mode "
            "and the sample tool is not registered. Accepted forms: "
            + "; ".join(_ACCEPTED_FORMS)
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
    positional = args.dsn or args.url
    if not positional:
        print("scan: DSN required (positional argument or --url)", file=sys.stderr)
        return 2

    try:
        spec = _select_connector(positional)
    except _DispatchError as exc:
        print(f"scan: {exc}", file=sys.stderr)
        return 2

    bundle_dir = Path(args.bundle_dir)

    try:
        bundle = asyncio.run(_scan_pipeline(spec, concurrency=args.concurrency))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        # Connection-error paths can embed credentials in str(exc); scrub the
        # raw positional out before it reaches stderr.
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", positional)
        print(f"scan failed: {message}", file=sys.stderr)
        return 1

    ContextStore(bundle_dir).write(bundle)
    _print_scan_summary(spec, bundle, bundle_dir)
    return 0


async def _scan_pipeline(
    spec: _ConnectorSpec, *, concurrency: int | None = None
) -> ContextBundle:
    async with spec.connector as conn:
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
        connector=spec.connector_type,
        database=spec.database_label,
    )

    return ContextBundle(
        meta=meta,
        tables=tuple(tables),
        descriptions=descriptions,
        relationships=tuple(relationships),
    )


def _print_scan_summary(
    spec: _ConnectorSpec, bundle: ContextBundle, bundle_dir: Path
) -> None:
    print(
        f"Scanned {spec.database_label}: "
        f"{len(bundle.tables)} tables, {len(bundle.relationships)} relationships"
    )
    print(f"Bundle written to {bundle_dir}")
    dropped = getattr(spec.connector, "cross_database_foreign_keys_dropped", 0)
    if dropped:
        bound_db = getattr(spec.connector, "database", spec.database_label)
        print(
            f"{dropped} foreign keys reference tables outside database "
            f"{bound_db} and were excluded"
        )


def _run_serve(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir)
    positional = args.dsn

    # Validate positional grammar consistently across scan + serve. Snowflake
    # positionals are accepted to keep dispatch consistent, but the live sample
    # tool is currently Postgres-only — Snowflake serves run bundle-only.
    dsn_for_sample_tool: str | None = None
    if positional is not None:
        try:
            spec = _select_connector(positional)
        except _DispatchError as exc:
            print(f"serve: {exc}", file=sys.stderr)
            return 2
        if spec.connector_type == "postgres":
            dsn_for_sample_tool = positional

    try:
        bundle = ContextStore(bundle_dir).read()
    except (BundleIntegrityError, BundleVersionError) as exc:
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", positional)
        print(f"serve failed: {message}", file=sys.stderr)
        return 1

    if bundle is None:
        print(
            f"serve: no bundle found at {bundle_dir}; run `sonar scan` first",
            file=sys.stderr,
        )
        return 1

    app = build_server(bundle, dsn_for_sample_tool, allow_pii=bool(args.allow_pii))
    try:
        run_stdio(app)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", positional)
        print(f"serve failed: {message}", file=sys.stderr)
        return 1
    return 0


def _select_connector(positional: str) -> _ConnectorSpec:
    if positional.startswith("postgresql://") or positional.startswith("postgres://"):
        return _ConnectorSpec(
            connector=PostgresConnector(positional),
            connector_type="postgres",
            database_label=format_database_label(positional),
        )

    if positional == "snowflake" or positional.startswith("snowflake://"):
        _ensure_snowflake_driver()
        if positional == "snowflake":
            connect_kwargs = _snowflake_kwargs_from_env()
        else:
            connect_kwargs = _snowflake_kwargs_from_url(positional)
        from sonar.connectors.snowflake import SnowflakeConnector

        return _ConnectorSpec(
            connector=SnowflakeConnector(connect_kwargs),
            connector_type="snowflake",
            database_label=_snowflake_label(connect_kwargs),
        )

    raise _DispatchError(
        "unrecognized argument; accepted forms: " + "; ".join(_ACCEPTED_FORMS)
    )


def _ensure_snowflake_driver() -> None:
    if importlib.util.find_spec("snowflake.connector") is None:
        raise _DispatchError(_SNOWFLAKE_INSTALL_HINT)


def _snowflake_kwargs_from_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if not parsed.username:
        raise _DispatchError("snowflake URL must include a user")
    if parsed.password is None:
        raise _DispatchError(
            "snowflake URL must include a password "
            "(or use bare 'snowflake' for env-var auth)"
        )
    if not parsed.hostname:
        raise _DispatchError("snowflake URL must include an account locator")

    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 2:
        raise _DispatchError("snowflake URL path must be /DATABASE/SCHEMA")
    database, schema = path_parts[0], path_parts[1]

    qs = parse_qs(parsed.query)
    kwargs: dict[str, Any] = {
        "account": parsed.hostname,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password),
        "database": database,
        "schema": schema,
    }
    if "warehouse" in qs:
        kwargs["warehouse"] = qs["warehouse"][0]
    if "role" in qs:
        kwargs["role"] = qs["role"][0]
    return kwargs


def _snowflake_kwargs_from_env() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for env_var, kwarg in _SNOWFLAKE_ENV_TO_KWARG.items():
        value = os.environ.get(env_var)
        if value:
            kwargs[kwarg] = value

    missing = [
        v for v in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_DATABASE")
        if not os.environ.get(v)
    ]
    if missing:
        raise _DispatchError(
            "missing required Snowflake env vars: " + ", ".join(missing)
        )

    has_auth = (
        "password" in kwargs
        or "private_key_file" in kwargs
        or "token" in kwargs
        or kwargs.get("authenticator") == "externalbrowser"
    )
    if not has_auth:
        raise _DispatchError(
            "no Snowflake authentication configured. Set one of: "
            "SNOWFLAKE_PASSWORD, SNOWFLAKE_PRIVATE_KEY_PATH, SNOWFLAKE_TOKEN, "
            "or SNOWFLAKE_AUTHENTICATOR=externalbrowser"
        )
    return kwargs


def _snowflake_label(connect_kwargs: dict[str, Any]) -> str:
    user = connect_kwargs.get("user")
    account = connect_kwargs["account"]
    database = connect_kwargs["database"]
    schema = connect_kwargs.get("schema")

    parts: list[str] = []
    if user:
        parts.append(f"{user}@")
    parts.append(account)
    parts.append(f"/{database}")
    if schema:
        parts.append(f"/{schema}")
    return "".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
