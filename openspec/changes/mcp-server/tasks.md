## 1. Shared primitives

- [ ] 1.1 Create `src/sonar/_dsn.py` with `scrub_dsn(message: str, dsn: str) -> str`. Covers the empty-DSN and no-match paths.
- [ ] 1.2 Add `tests/test_dsn.py` covering: DSN substring present, DSN substring absent, DSN with password containing regex-special characters, empty DSN.
- [ ] 1.3 Refactor `src/sonar/cli.py::_run_scan` to use `scrub_dsn` from `sonar/_dsn.py`. Remove the inline `.replace()` call. Confirm `tests/test_scan.py::test_unreachable_db_exits_nonzero_and_writes_nothing` still passes unchanged.

## 2. MCP audit logger

- [ ] 2.1 Create `src/sonar/mcp/__init__.py` (empty) and `src/sonar/mcp/audit.py`. Export a module-level logger `_AUDIT = logging.getLogger("sonar.mcp.audit")` and a helper `emit_sample_audit(outcome, schema, table, limit_requested, limit_effective, rows_returned)`.
- [ ] 2.2 Add `tests/test_mcp_audit.py` verifying the record's `extra` dict contains the documented fields and excludes any credential or row-content keys (use `caplog` with the `sonar.mcp.audit` logger).

## 3. Bundle-backed tools

- [ ] 3.1 Create `src/sonar/mcp/tools/__init__.py` (empty) and `src/sonar/mcp/tools/bundle_tools.py`.
- [ ] 3.2 Implement `discover_tool(bundle, schema=None)` returning `list[dict]` with `{schema, name, row_count}` per table. Filter by `schema` when provided.
- [ ] 3.3 Implement `describe_tool(bundle, schema, table)` returning the joined view. Raise a tool-level error on unknown table. Null description slot returns the raw column shape with description fields explicitly null.
- [ ] 3.4 Implement `relationships_tool(bundle, schema, table, direction="both")`. Validate `direction` is one of `outgoing`/`incoming`/`both`; reject otherwise.
- [ ] 3.5 Implement `search_tool(bundle, query, limit=20)` with case-insensitive substring matching across table names, table descriptions, column names, column descriptions. Ranked: table-name > column-name > description-body; alphabetical on `(schema, table)` within a tier.
- [ ] 3.6 Add `tests/test_mcp_bundle_tools.py` with hand-built `ContextBundle` fixtures covering every scenario in the delta spec: unfiltered discover, schema-filtered discover, describe with null description, describe on unknown table, outgoing/incoming/both relationships, table with no relationships, table-name search match, description-body search match, limit enforcement.

## 4. Sample tool

- [ ] 4.1 Create `src/sonar/mcp/tools/sample_tool.py`. `import psycopg` and `import psycopg.sql` live inside the module (bundle-only mode never touches this file at server-build time outside the import statement itself — so put the `AsyncConnection` use and psycopg.sql use inside `make_sample_tool`).
- [ ] 4.2 Define module constants `DEFAULT_SAMPLE_ROWS = 5` and `MAX_SAMPLE_ROWS = 20`.
- [ ] 4.3 Implement `make_sample_tool(bundle, dsn, allow_pii=False)` returning an async callable. The returned tool: rejects `limit > MAX_SAMPLE_ROWS` with a clear error (no DB connection opened, audit record emitted); opens a short-lived `psycopg.AsyncConnection` per call; composes SQL with `psycopg.sql.SQL("SELECT * FROM {}.{} LIMIT {}").format(Identifier(schema), Identifier(table), Literal(effective_limit))`; strips columns whose bundle `pii_risk` is in `{high, medium}` (unless `allow_pii`); emits an audit record in every exit path; scrubs DSN from any exception text before re-raising.
- [ ] 4.4 Document the "column with no classification" path: bundle description is null ⇒ all columns pass through unredacted (matches spec scenario).
- [ ] 4.5 Add `tests/test_mcp_sample_tool.py` (unit) covering: cap-accept at `limit=20`, cap-reject at `limit=21` (no connection opened, audit record emitted), default `limit=5`, `pii_risk=high` column stripped by default, `pii_risk=medium` column stripped by default, `pii_risk=low` column passes through by default, all flagged columns pass through with `allow_pii=True`, `psycopg.sql.Identifier` stops an injection payload, DSN scrubbed from a connection-failure error message.
- [ ] 4.6 Add `tests/test_mcp_sample_integration.py` (marked `@pytest.mark.integration`) running `sample` against the docker fixture DB. Covers the happy path end-to-end.

## 5. Server bootstrap

- [ ] 5.1 Create `src/sonar/mcp/server.py` with `build_server(bundle, dsn, allow_pii=False) -> FastMCP`. Registers the four bundle-backed tools via `functools.partial(...)`-wrapped callables passed to `app.tool()`. Registers `sample` only when `dsn is not None`.
- [ ] 5.2 Add `run_stdio(app: FastMCP) -> None` — a thin wrapper around `FastMCP`'s stdio runner for test-isolation (unit tests call `build_server` directly; this wrapper is the single place stdio lifecycle lives).
- [ ] 5.3 Add `tests/test_mcp_server.py` unit tests, one test per spec scenario with explicit assertions matching the scenario wording:
    - **bundle-only mode tool list** — `build_server(bundle, dsn=None)` → registered tool set equals `{discover, describe, relationships, search}` AND explicitly `"sample" not in tool_names` (directly asserts the "Tool absent in bundle-only mode" scenario).
    - **live mode tool list** — `build_server(bundle, dsn="postgresql://...")` → `"sample" in tool_names` AND the four bundle-backed tools are also present (directly asserts the "Tool present in live mode" scenario).
    - **missing bundle aborts startup** — `_run_serve` with a non-existent `--bundle-dir` returns non-zero and emits a clear stderr line; `build_server` is never called.
    - **corrupt bundle aborts startup** — monkeypatched `ContextStore.read` raising `BundleIntegrityError` makes `_run_serve` exit non-zero before the MCP transport opens.
    - **version-mismatch bundle aborts startup** — same shape, but `BundleVersionError` raised; same non-zero exit and pre-transport abort.

## 6. CLI wiring

- [ ] 6.1 Replace the `serve` subcommand stub in `src/sonar/cli.py`. Add `--bundle-dir` (default `.sonar/`), optional positional `dsn`, `--allow-pii` flag.
- [ ] 6.2 Implement `_run_serve(args)`: load bundle via `ContextStore(bundle_dir).read()`; on `None` (missing dir/meta), print error and `return 1`; on `BundleIntegrityError`/`BundleVersionError`, print scrubbed error and `return 1`; call `build_server(bundle, dsn, allow_pii)` and `run_stdio(app)`. Return `0` on clean shutdown.
- [ ] 6.3 Add tests in `tests/test_cli.py` (or create if absent): `sonar serve --help` smoke; missing bundle dir exits non-zero with clear stderr; corrupt bundle exits non-zero with scrubbed stderr.

## 7. Verification

- [ ] 7.1 Run `poetry run pytest`; all tests green. Coverage stays above 80% (Sonar-wide, not per-file).
- [ ] 7.2 Run `poetry run ruff check src tests`; clean.
- [ ] 7.3 Manual smoke: `poetry run sonar scan <test-dsn> --bundle-dir /tmp/sonar-smoke/` followed by `poetry run sonar serve --bundle-dir /tmp/sonar-smoke/` — verify the process starts without error and responds to a stdin `tools/list` JSON-RPC message with the four bundle-backed tools.
- [ ] 7.4 Manual smoke live mode: same as 7.3 but append the DSN — confirm `tools/list` returns five tools and a `tools/call` for `sample` against a fixture table returns redacted rows.

## 8. Documentation

- [ ] 8.1 Update `README.md`: add a "Start the MCP server" section showing bundle-only and live invocations, and the `--allow-pii` flag with its warning.
- [ ] 8.2 `openspec change validate mcp-server` passes.
