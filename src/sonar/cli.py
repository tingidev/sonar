"""CLI entrypoint for Sonar."""

from __future__ import annotations

import argparse
import asyncio
import datetime as _datetime
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from sonar._dsn import scrub_dsn
from sonar.connectors.postgres import PostgresConnector
from sonar.connectors.types import ForeignKey, Table
from sonar.engine.describe import DescriptionEngine
from sonar.engine.llm import LLMConfig, create_llm_client
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
    "duckdb:///path/to/file.duckdb",
    "duckdb://:memory:",
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

_DUCKDB_INSTALL_HINT = "DuckDB driver not installed. Install with: pip install 'sonar[duckdb]'"


class _DispatchError(Exception):
    """Raised when the positional argument cannot be mapped to a connector."""


@dataclass
class _ConnectorSpec:
    connector: Any
    connector_type: str
    database_label: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sonar - data context for AI agents")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable verbose logging (INFO level)",
    )
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Discover and describe a data source")
    scan_parser.add_argument(
        "dsn",
        nargs="?",
        default=None,
        help=("Connection target. Accepted forms: " + "; ".join(_ACCEPTED_FORMS)),
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
    scan_parser.add_argument(
        "--model",
        dest="model",
        default=None,
        help="LLM model to use (e.g. anthropic/claude-haiku-4-5-20251001, gpt-4o, llama3)",
    )

    eval_parser = subparsers.add_parser("eval", help="Run evaluation reports on a bundle")
    eval_parser.add_argument(
        "--bundle-dir",
        dest="bundle_dir",
        default=".sonar",
        help="Bundle directory to evaluate (default: .sonar/)",
    )
    eval_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output structured JSON instead of human-readable text",
    )
    eval_parser.add_argument(
        "--model",
        dest="model",
        default=None,
        help="LLM model for description evaluation (e.g. anthropic/claude-haiku-4-5-20251001)",
    )
    mode_group = eval_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--relationships",
        dest="relationships_dsn",
        metavar="DSN",
        default=None,
        help="Score relationship inference against the live database's declared FKs",
    )
    mode_group.add_argument(
        "--search",
        dest="search_path",
        metavar="GROUND_TRUTH",
        default=None,
        help="Score search relevance against a YAML ground-truth file",
    )
    mode_group.add_argument(
        "--diff",
        dest="diff_other",
        metavar="OTHER_BUNDLE_DIR",
        default=None,
        help="Diff the current bundle against another bundle directory",
    )
    mode_group.add_argument(
        "--descriptions",
        dest="descriptions_mode",
        action="store_true",
        help="Score description quality via LLM-as-judge",
    )

    serve_parser = subparsers.add_parser("serve", help="Start the MCP server over stdio")
    serve_parser.add_argument(
        "dsn",
        nargs="?",
        default=None,
        help=(
            "Connection target. When omitted, the server runs in bundle-only mode "
            "and the sample tool is not registered. Accepted forms: " + "; ".join(_ACCEPTED_FORMS)
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

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    if args.command == "scan":
        return _run_scan(args)
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "eval":
        return _run_eval(args)
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
        bundle = asyncio.run(_scan_pipeline(spec, concurrency=args.concurrency, model=args.model))
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
    spec: _ConnectorSpec,
    *,
    concurrency: int | None = None,
    model: str | None = None,
) -> ContextBundle:
    async with spec.connector as conn:
        tables = await conn.discover_tables()
        foreign_keys = await conn.discover_relationships()

        sem = asyncio.Semaphore(5)

        async def _sample_one(t: Table) -> tuple[tuple[str, str], list[dict]]:
            async with sem:
                rows = await conn.sample_table(t.schema, t.name)
            return (t.schema, t.name), rows

        pairs = await asyncio.gather(*(_sample_one(t) for t in tables))
        samples: dict[tuple[str, str], list[dict]] = dict(pairs)

        config_kwargs: dict[str, Any] = {}
        if concurrency:
            config_kwargs["max_concurrent_calls"] = concurrency
        if model:
            config_kwargs["model"] = model
        config = LLMConfig(**config_kwargs)
        engine = DescriptionEngine(create_llm_client(config), config)
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


def _print_scan_summary(spec: _ConnectorSpec, bundle: ContextBundle, bundle_dir: Path) -> None:
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


def _run_eval(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir)

    if args.relationships_dsn is not None:
        return _run_eval_relationships(args.relationships_dsn, args.json_output)
    if args.search_path is not None:
        return _run_eval_search(bundle_dir, Path(args.search_path), args.json_output)
    if args.diff_other is not None:
        return _run_eval_diff(bundle_dir, Path(args.diff_other), args.json_output)
    if args.descriptions_mode:
        return _run_eval_descriptions(bundle_dir, args.json_output, model=args.model)
    return _run_eval_quality(bundle_dir, args.json_output)


def _load_bundle_for_eval(bundle_dir: Path) -> ContextBundle | None:
    try:
        bundle = ContextStore(bundle_dir).read()
    except (BundleIntegrityError, BundleVersionError) as exc:
        print(f"eval failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    if bundle is None:
        print(
            f"eval: no bundle found at {bundle_dir}; run `sonar scan` first",
            file=sys.stderr,
        )
        return None
    return bundle


def _run_eval_quality(bundle_dir: Path, json_output: bool) -> int:
    from sonar.eval._report import format_quality_human, format_quality_json
    from sonar.eval.quality import build_quality_report

    bundle = _load_bundle_for_eval(bundle_dir)
    if bundle is None:
        return 1
    report = build_quality_report(bundle, str(bundle_dir))
    if json_output:
        print(format_quality_json(report))
    else:
        print(format_quality_human(report))
    return 0


def _run_eval_relationships(positional: str, json_output: bool) -> int:
    from sonar.eval._report import format_relationships_human, format_relationships_json
    from sonar.eval.relationships import evaluate_relationships

    try:
        spec = _select_connector(positional)
    except _DispatchError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2

    try:
        tables, foreign_keys = asyncio.run(_collect_relationship_inputs(spec))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        message = scrub_dsn(f"{type(exc).__name__}: {exc}", positional)
        print(f"eval failed: {message}", file=sys.stderr)
        return 1

    report = evaluate_relationships(tables, foreign_keys)
    if json_output:
        print(format_relationships_json(report, spec.database_label))
    else:
        print(format_relationships_human(report, spec.database_label))
    return 0


async def _collect_relationship_inputs(
    spec: _ConnectorSpec,
) -> tuple[list[Table], list[ForeignKey]]:
    async with spec.connector as conn:
        tables = await conn.discover_tables()
        foreign_keys = await conn.discover_relationships()
    return tables, foreign_keys


def _run_eval_search(bundle_dir: Path, ground_truth_path: Path, json_output: bool) -> int:
    from sonar.eval._report import format_search_human, format_search_json
    from sonar.eval.search import GroundTruthError, evaluate_search, load_ground_truth

    bundle = _load_bundle_for_eval(bundle_dir)
    if bundle is None:
        return 1
    try:
        ground_truth = load_ground_truth(ground_truth_path)
    except (OSError, GroundTruthError) as exc:
        print(f"eval failed: {exc}", file=sys.stderr)
        return 1

    report = evaluate_search(bundle, ground_truth)
    if json_output:
        print(format_search_json(report, str(bundle_dir)))
    else:
        print(format_search_human(report, str(bundle_dir)))
    return 0


def _run_eval_diff(bundle_dir: Path, other_dir: Path, json_output: bool) -> int:
    from sonar.eval._report import format_diff_human, format_diff_json
    from sonar.eval.diff import diff_bundles

    current = _load_bundle_for_eval(bundle_dir)
    if current is None:
        return 1
    try:
        other = ContextStore(other_dir).read()
    except (BundleIntegrityError, BundleVersionError) as exc:
        print(f"eval failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if other is None:
        print(f"eval: no bundle found at {other_dir}", file=sys.stderr)
        return 1

    report = diff_bundles(current, other)
    if json_output:
        print(format_diff_json(report, str(bundle_dir), str(other_dir)))
    else:
        print(format_diff_human(report, str(bundle_dir), str(other_dir)))
    return 0


def _run_eval_descriptions(bundle_dir: Path, json_output: bool, *, model: str | None = None) -> int:
    from sonar.eval._report import (
        format_descriptions_human,
        format_descriptions_json,
    )
    from sonar.eval.descriptions import evaluate_descriptions

    bundle = _load_bundle_for_eval(bundle_dir)
    if bundle is None:
        return 1

    config = LLMConfig(model=model) if model else LLMConfig()
    client = create_llm_client(config)
    try:
        report = asyncio.run(evaluate_descriptions(bundle, client, config=config))
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"eval failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if json_output:
        print(format_descriptions_json(report, str(bundle_dir)))
    else:
        print(format_descriptions_human(report, str(bundle_dir)))

    if report.scored_count == 0 and report.judge_failures > 0:
        print(
            f"eval: judge failed on all {report.judge_failures} tables; " "no scores produced",
            file=sys.stderr,
        )
        return 1
    return 0


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

    if positional.startswith("duckdb://"):
        _ensure_duckdb_driver()
        path = positional[len("duckdb://") :]
        from sonar.connectors.duckdb import DuckDBConnector

        return _ConnectorSpec(
            connector=DuckDBConnector(path),
            connector_type="duckdb",
            database_label=Path(path).name,
        )

    raise _DispatchError("unrecognized argument; accepted forms: " + "; ".join(_ACCEPTED_FORMS))


def _ensure_snowflake_driver() -> None:
    if importlib.util.find_spec("snowflake.connector") is None:
        raise _DispatchError(_SNOWFLAKE_INSTALL_HINT)


def _ensure_duckdb_driver() -> None:
    if importlib.util.find_spec("duckdb") is None:
        raise _DispatchError(_DUCKDB_INSTALL_HINT)


def _snowflake_kwargs_from_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if not parsed.username:
        raise _DispatchError("snowflake URL must include a user")
    if parsed.password is None:
        raise _DispatchError(
            "snowflake URL must include a password " "(or use bare 'snowflake' for env-var auth)"
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
        v
        for v in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_DATABASE")
        if not os.environ.get(v)
    ]
    if missing:
        raise _DispatchError("missing required Snowflake env vars: " + ", ".join(missing))

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
