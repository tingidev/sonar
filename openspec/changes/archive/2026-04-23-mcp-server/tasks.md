## 0. Description-engine: expand PIIRisk with MEDIUM

Context: `mcp-server`'s default PII-stripping policy (D6) treats `pii_risk ∈ {high, medium}` as protected, but the `description-engine` enum is pinned to `{NONE, LOW, HIGH}`. This group adds the `MEDIUM` bucket so the policy has a value to match. Additive + forward-compatible (existing bundles still parse).

- [x] 0.1 Add `MEDIUM = "medium"` to `PIIRisk` in `src/sonar/engine/describe.py`. Preserve enum declaration order: `NONE`, `LOW`, `MEDIUM`, `HIGH`.
- [x] 0.2 Update `src/sonar/engine/_prompts.py::SYSTEM_PROMPT` to describe the `medium` bucket ("plausible PII; classifier is uncertain") between `low` and `high`, and update the expected-output JSON fragment's `pii_risk` enumeration to `"none | low | medium | high"`.
- [x] 0.3 Add a `test_pii_risk_medium_roundtrip` case in `tests/test_description_engine.py` (or wherever enum round-tripping is tested) asserting `PIIRisk("medium") is PIIRisk.MEDIUM` and a `ColumnDescription` with `pii_risk=MEDIUM` round-trips through `json.dumps` + `PIIRisk(value)`.
- [x] 0.4 Add a `test_pii_classification_medium` case parallel to the existing `"high"` test: mocked LLM returns `pii_risk="medium"` for a `city` column, assert the returned `ColumnDescription.pii_risk == PIIRisk.MEDIUM` and the engine does not override.
- [x] 0.5 Run `poetry run pytest tests/test_description_engine.py tests/test_store.py tests/test_bundle.py` — all green. No existing test should break (the change is additive).

## 1. Shared primitives

- [x] 1.1 Create `src/sonar/_dsn.py` with `scrub_dsn(message: str, dsn: str) -> str`. Covers the empty-DSN and no-match paths.
- [x] 1.2 Add `tests/test_dsn.py` covering: DSN substring present, DSN substring absent, DSN with password containing regex-special characters, empty DSN.
- [x] 1.3 Refactor `src/sonar/cli.py::_run_scan` to use `scrub_dsn` from `sonar/_dsn.py`. Remove the inline `.replace()` call. Confirm `tests/test_scan.py::test_unreachable_db_exits_nonzero_and_writes_nothing` still passes unchanged.

## 2. MCP audit logger

- [x] 2.1 Create `src/sonar/mcp/__init__.py` (empty) and `src/sonar/mcp/audit.py`. Export a module-level logger `_AUDIT = logging.getLogger("sonar.mcp.audit")` and a helper `emit_sample_audit(outcome, schema, table, limit_requested, limit_effective, rows_returned)`.
- [x] 2.2 Add `tests/test_mcp_audit.py` verifying the record's `extra` dict contains the documented fields and excludes any credential or row-content keys (use `caplog` with the `sonar.mcp.audit` logger).

## 3. Bundle-backed tools

- [x] 3.1 Create `src/sonar/mcp/tools/__init__.py` (empty) and `src/sonar/mcp/tools/bundle_tools.py`.
- [x] 3.2 Implement `discover_tool(bundle, schema=None)` returning `list[dict]` with `{schema, name, row_count}` per table. Filter by `schema` when provided.
- [x] 3.3 Implement `describe_tool(bundle, schema, table)` returning the joined view. Raise a tool-level error on unknown table. Null description slot returns the raw column shape with description fields explicitly null.
- [x] 3.4 Implement `relationships_tool(bundle, schema, table, direction="both")`. Validate `direction` is one of `outgoing`/`incoming`/`both`; reject otherwise.
- [x] 3.5 Implement `search_tool(bundle, query, limit=20)` with case-insensitive substring matching across table names, table descriptions, column names, column descriptions. Ranked: table-name > column-name > description-body; alphabetical on `(schema, table)` within a tier.
- [x] 3.6 Add `tests/test_mcp_bundle_tools.py` with hand-built `ContextBundle` fixtures covering every scenario in the delta spec: unfiltered discover, schema-filtered discover, describe with null description, describe on unknown table, outgoing/incoming/both relationships, table with no relationships, table-name search match, description-body search match, limit enforcement.

## 4. Sample tool

- [x] 4.1 Create `src/sonar/mcp/tools/sample_tool.py`. `import psycopg` and `import psycopg.sql` live inside the module (bundle-only mode never touches this file at server-build time outside the import statement itself — so put the `AsyncConnection` use and psycopg.sql use inside `make_sample_tool`).
- [x] 4.2 Define module constants `DEFAULT_SAMPLE_ROWS = 5` and `MAX_SAMPLE_ROWS = 20`.
- [x] 4.3 Implement `make_sample_tool(bundle, dsn, allow_pii=False)` returning an async callable. The returned tool: rejects `limit > MAX_SAMPLE_ROWS` with a clear error (no DB connection opened, audit record emitted); opens a short-lived `psycopg.AsyncConnection` per call; composes SQL with `psycopg.sql.SQL("SELECT * FROM {}.{} LIMIT {}").format(Identifier(schema), Identifier(table), Literal(effective_limit))`; strips columns whose bundle `pii_risk` is in `{high, medium}` (unless `allow_pii`); emits an audit record in every exit path; scrubs DSN from any exception text before re-raising.
- [x] 4.4 Document the "column with no classification" path: bundle description is null ⇒ all columns pass through unredacted (matches spec scenario).
- [x] 4.5 Add `tests/test_mcp_sample_tool.py` (unit) covering: cap-accept at `limit=20`, cap-reject at `limit=21` (no connection opened, audit record emitted), default `limit=5`, `pii_risk=high` column stripped by default, `pii_risk=medium` column stripped by default, `pii_risk=low` column passes through by default, all flagged columns pass through with `allow_pii=True`, `psycopg.sql.Identifier` stops an injection payload, DSN scrubbed from a connection-failure error message.
- [x] 4.6 Add `tests/test_mcp_sample_integration.py` (marked `@pytest.mark.integration`) running `sample` against the docker fixture DB. Covers the happy path end-to-end.

## 5. Server bootstrap

- [x] 5.1 Create `src/sonar/mcp/server.py` with `build_server(bundle, dsn, allow_pii=False) -> FastMCP`. Registers the four bundle-backed tools via `functools.partial(...)`-wrapped callables passed to `app.tool()`. Registers `sample` only when `dsn is not None`. (Implementation note: named wrapper closures preserve type annotations for FastMCP schema derivation — `functools.partial` strips them — but the registration shape matches the decision.)
- [x] 5.2 Add `run_stdio(app: FastMCP) -> None` — a thin wrapper around `FastMCP`'s stdio runner for test-isolation (unit tests call `build_server` directly; this wrapper is the single place stdio lifecycle lives).
- [x] 5.3 Add `tests/test_mcp_server.py` unit tests, one test per spec scenario with explicit assertions matching the scenario wording:
    - **bundle-only mode tool list** — `build_server(bundle, dsn=None)` → registered tool set equals `{discover, describe, relationships, search}` AND explicitly `"sample" not in tool_names` (directly asserts the "Tool absent in bundle-only mode" scenario).
    - **live mode tool list** — `build_server(bundle, dsn="postgresql://...")` → `"sample" in tool_names` AND the four bundle-backed tools are also present (directly asserts the "Tool present in live mode" scenario).
    - **missing bundle aborts startup** — `_run_serve` with a non-existent `--bundle-dir` returns non-zero and emits a clear stderr line; `build_server` is never called.
    - **corrupt bundle aborts startup** — monkeypatched `ContextStore.read` raising `BundleIntegrityError` makes `_run_serve` exit non-zero before the MCP transport opens.
    - **version-mismatch bundle aborts startup** — same shape, but `BundleVersionError` raised; same non-zero exit and pre-transport abort.

## 6. CLI wiring

- [x] 6.1 Replace the `serve` subcommand stub in `src/sonar/cli.py`. Add `--bundle-dir` (default `.sonar/`), optional positional `dsn`, `--allow-pii` flag.
- [x] 6.2 Implement `_run_serve(args)`: load bundle via `ContextStore(bundle_dir).read()`; on `None` (missing dir/meta), print error and `return 1`; on `BundleIntegrityError`/`BundleVersionError`, print scrubbed error and `return 1`; call `build_server(bundle, dsn, allow_pii)` and `run_stdio(app)`. Return `0` on clean shutdown.
- [x] 6.3 Add tests in `tests/test_cli.py` (or create if absent): `sonar serve --help` smoke; missing bundle dir exits non-zero with clear stderr; corrupt bundle exits non-zero with scrubbed stderr.

## 7. Verification

- [x] 7.1 Run `poetry run pytest`; all tests green. Coverage stays above 80% (Sonar-wide, not per-file). (151 passed, 96% coverage.)
- [x] 7.2 Run `poetry run ruff check src tests`; clean. (Also fixed a pre-existing E501 at `tests/test_postgres_connector.py:204`.)
- [x] 7.3 Manual smoke: built a bundle programmatically from the docker fixture DB (no LLM needed — null descriptions), then `sonar serve --bundle-dir /tmp/sonar-smoke/` returned the four bundle-backed tools in the `tools/list` JSON-RPC response. (`sonar scan` was skipped because no `ANTHROPIC_API_KEY` is available in this session; the bundle-on-disk shape is what `serve` consumes, and the contract is the same regardless of producer.)
- [x] 7.4 Manual smoke live mode: rebuilt the bundle with a real PII classification (`email`/`name` → HIGH, `street`/`city` → MEDIUM), then started `sonar serve --bundle-dir /tmp/sonar-smoke/ <test-dsn>`. `tools/list` returned five tools; `tools/call` for `sample` on `public.users` with `limit=2` returned two rows with `email` and `name` columns redacted (null) and non-PII columns passed through. Confirms the end-to-end default PII-strip path.

## 8. Documentation

- [x] 8.1 Update `README.md`: add a "Start the MCP server" section showing bundle-only and live invocations, and the `--allow-pii` flag with its warning.
- [x] 8.2 `openspec change validate mcp-server` passes.
