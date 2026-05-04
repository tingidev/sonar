## 1. Shared connector module

- [x] 1.1 Create `src/sonar/connectors/types.py` and move the `Column`, `Table`, and `ForeignKey` dataclasses there verbatim.
- [x] 1.2 Create `src/sonar/connectors/serialize.py` and move `_coerce_value` (and any helpers it depends on, e.g. binary placeholder constant) there verbatim.
- [x] 1.3 In `src/sonar/connectors/postgres.py`, remove the dataclass definitions and `_coerce_value`; import from the new shared modules. Keep the module's other behaviour byte-identical.
- [x] 1.4 Update import sites across `src/` (`relationships.py`, `index/store.py`, `index/bundle.py`, `engine/describe.py`, `engine/_prompts.py`, `mcp/tools/sample_tool.py`) to import `Column`/`Table`/`ForeignKey` from `sonar.connectors.types` and `_coerce_value` from `sonar.connectors.serialize`. Eliminate the `_coerce_value` private cross-import in the sample tool.
- [x] 1.5 Update import sites across `tests/` (`conftest.py`, `test_postgres_connector.py`, `test_relationships.py`, `test_bundle.py`, `test_store.py`, `test_description_engine.py`, `test_mcp_server.py`, `test_mcp_sample_integration.py`, `test_mcp_sample_tool.py`, `test_mcp_bundle_tools.py`) to import from the new shared modules.
- [x] 1.6 Run `poetry run pytest`; confirm full suite still passes with no behaviour change.
- [x] 1.7 Run `poetry run ruff check .` and fix any import-ordering warnings introduced by the move.

## 2. Snowflake driver as optional dependency

- [x] 2.1 Add `snowflake-connector-python` to `pyproject.toml` under `[tool.poetry.extras] snowflake`. Do not add it to the default dependency list.
- [x] 2.2 Add `fakesnow` to `pyproject.toml` under `[tool.poetry.group.dev.dependencies]` (it's a test-time dependency, not a runtime one — contributors clone and run, no credentials).
- [x] 2.3 Run `poetry lock` and commit the lockfile changes.
- [x] 2.4 Install locally via `poetry install -E snowflake` and confirm fakesnow resolves under the dev group.
- [x] 2.5 Update `.github/workflows/*` so the default PR test job installs with `-E snowflake`, ensuring fakesnow-backed Snowflake tests run on every PR.

## 3. Snowflake connector core

- [x] 3.1 Create `src/sonar/connectors/_snowflake_sql.py` holding the `INFORMATION_SCHEMA` query strings for tables/columns/PKs (with row_count from `INFORMATION_SCHEMA.TABLES.ROW_COUNT`) and for foreign keys via `TABLE_CONSTRAINTS` + `REFERENTIAL_CONSTRAINTS` + `KEY_COLUMN_USAGE`.
- [x] 3.2 Create `src/sonar/connectors/snowflake.py` defining `SnowflakeConnector` with `__aenter__`/`__aexit__`, `discover_tables`, `discover_relationships`, and `sample_table`. Driver calls go through `asyncio.to_thread` (per D5). The class assumes `import snowflake.connector` succeeded (the dispatch-time guard handles missing-extra cases).
- [x] 3.3 Implement 2-level identifier handling (per D2): the constructor accepts the bound database; `Table.schema` holds the Snowflake schema name; `Table.name` holds the table; no field carries the database. Drop and warn on cross-database FKs.
- [x] 3.4 Implement `_reject_dotted_identifier` reuse so Snowflake-emitted Tables also get the no-dot guarantee that the bundle key encoding requires.
- [x] 3.5 Implement `sample_table` using parameterized identifier quoting (mirror Postgres `psycopg.sql.Identifier`/`Literal` discipline; for Snowflake, manual identifier quoting via the driver's identifier-quoting helper or a small local helper). Return rows coerced through the shared `_coerce_value`.
- [x] 3.6 Preserve identifier case as returned by INFORMATION_SCHEMA (per D8) — no upper/lower-casing, no normalization.

## 4. CLI dispatch grammar

- [x] 4.1 In `src/sonar/cli.py`, factor out a `_select_connector(positional: str)` helper that maps positional input to a connector instance, with three accepted forms: `postgresql://...` / `postgres://...` (Postgres unchanged), `snowflake://...` (Snowflake password-auth), and the bare keyword `snowflake` (Snowflake env-var auth).
- [x] 4.2 Implement the dispatch-time driver guard (per D4): when the positional matches a Snowflake form, check `importlib.util.find_spec("snowflake.connector")` and exit non-zero with the actionable `pip install sonar[snowflake]` message *before* parsing the URL or reading env vars.
- [x] 4.3 Implement Snowflake URL parser: `snowflake://USER:PASS@ACCOUNT/DATABASE/SCHEMA?warehouse=W&role=R`. Construct `SnowflakeConnector` with the parsed fields. Reject malformed URLs (missing account, missing database, missing schema) with a clear error.
- [x] 4.4 Implement the env-var dispatch path using the **curated 10-var set** defined in `design.md` D3 (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_AUTHENTICATOR`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`, `SNOWFLAKE_TOKEN`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_ROLE`). Forward each one to the driver under its mapped `connect()` kwarg. Silently ignore any other `SNOWFLAKE_*` variables.
- [x] 4.5 Surface a configuration error naming missing required env vars (account, user, database, plus one auth-mechanism variable) before any connection attempt.
- [x] 4.6 Update the unrecognized-argument error path so it lists all four accepted forms.
- [x] 4.7 Apply the same dispatch grammar to `sonar serve` so a user can serve a previously-scanned Snowflake bundle (database/schema scope is irrelevant for bundle-only mode but consistent dispatch is required when a positional is supplied).
- [x] 4.8 Update the `sonar scan` summary output (the structured report printed at end of scan) to include a one-line note when the connector dropped any cross-database foreign keys: `"<N> foreign keys reference tables outside database <DB> and were excluded"`. The connector tracks the count internally and exposes it on the discovery result; CLI renders it.

## 5. Tests — fakesnow tier (default, runs on every PR)

- [x] 5.1 Create `tests/test_snowflake_connector.py`. Use `fakesnow` as the in-process Snowflake emulator. Set up a fixture that boots fakesnow, creates a small DB/schema with realistic tables, FKs, and a few rows, then yields a connected `SnowflakeConnector`. Tear down between tests.
- [x] 5.2 Test URL parsing: a variety of well-formed `snowflake://...` URLs produce the expected connector configuration; malformed URLs raise informative errors. (Pure unit, no fakesnow needed.)
- [x] 5.3 Test env-var dispatch using the curated 10-var set: with monkeypatched env vars, the connector forwards them to the driver under the expected `connect()` kwargs; **uncurated `SNOWFLAKE_*` vars are silently ignored**; missing required vars surface a clear configuration error.
- [x] 5.4 Test the dispatch-time missing-extra guard: with `importlib.util.find_spec("snowflake.connector")` simulated as `None`, both `snowflake://...` and `snowflake` forms exit non-zero before any further work, with the install-extra message.
- [x] 5.5 Test discovery against fakesnow: `discover_tables` returns the expected `Table` objects (including row_count from `INFORMATION_SCHEMA.TABLES.ROW_COUNT`, `None` when missing); `discover_relationships` returns the expected `ForeignKey` objects.
- [x] 5.6 Test cross-database FK skip and scan-summary surfacing: against a fakesnow setup with two databases and an FK from DB-A to DB-B, the connector drops cross-DB FKs, exposes the dropped count to the caller, and the scan summary renders the one-line "N foreign keys reference tables outside database X and were excluded" note. (Cross-DB FK filter exercised via the pure-unit helper because fakesnow rejects cross-DB FK DDL — D6 fakesnow caveat.)
- [x] 5.7 Test sampling against fakesnow: `sample_table` returns dict rows coerced through the shared serializer (datetime → ISO, decimal → float, etc.).
- [x] 5.8 Test identifier-case preservation: discovery against fakesnow returns Tables/Columns whose names are UPPERCASE (no normalization in the connector).
- [x] 5.9 Test the no-dot identifier guard: a fakesnow fixture row with a dotted schema or table name raises the same error path the Postgres connector uses.
- [x] 5.10 In `tests/test_cli.py` (or new), test the dispatch grammar: each accepted positional form constructs the right connector class; unrecognized argument exits with all four forms named.
- [x] 5.11 Run the full suite (`poetry run pytest`) and confirm coverage on `connectors/snowflake.py` and the CLI dispatch path is at or above 80%.

## 6. Tests — live tier (push-to-main and manual trigger only)

- [x] 6.1 Define `pytest.mark.snowflake_live` in `pyproject.toml` (or `tests/conftest.py`) and configure pytest to skip-by-default unless explicitly selected. (Marker registered in `pyproject.toml`; `_skip_unless_live` in the live test module skips automatically when credentials are absent.)
- [x] 6.2 Add `tests/test_snowflake_live.py` containing a small set of real-account smoke tests tagged `@pytest.mark.snowflake_live`. Suggested coverage: connect via env-var auth against `SNOWFLAKE_SAMPLE_DATA`, run `discover_tables` over `TPCH_SF1` (or similar small sample schema), `discover_relationships` (sample data has real FKs), and a `sample_table` against one of the smaller tables. Each test should be cheap (single-digit cents).
- [x] 6.3 Add a GitHub Actions workflow (e.g. `.github/workflows/snowflake-live.yml`) that runs `pytest -m snowflake_live` on `push` to `main` and on `workflow_dispatch`. Credentials come from repository secrets exposed via `env:` only on these triggers — never on PR triggers from forks. Document in the workflow comments which secrets are required (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD` or key-pair).
- [x] 6.4 Verify locally: with `SNOWFLAKE_*` env vars set against a real account, run `pytest -m snowflake_live` and confirm the live tests pass. Without the env vars, pytest cleanly skips the marker. (No-credentials skip path verified locally; positive run deferred until a real Snowflake account is available — see session notes.)

## 7. Documentation

- [x] 7.1 Update `README.md`: add a Snowflake quickstart section showing the URL form and the env-var form. Mention `pip install sonar[snowflake]` prominently. Document the curated 10-var env-var set (table, mirroring D3).
- [x] 7.2 Add the password-in-URL caveat one-liner: prefer `sonar scan snowflake` + `SNOWFLAKE_PASSWORD` env var for anything beyond a quick test (visible in shell history and `ps` output otherwise).
- [x] 7.3 README "Testing" section: note the two-tier model — fakesnow runs by default with `pytest`; live-account tests run on push-to-main and `workflow_dispatch`. Tell contributors they don't need a Snowflake account to contribute.
- [x] 7.4 Update `ROADMAP.md`: mark `snowflake-connector` (#9) as in flight while this change is open; mark archived once landed.
- [x] 7.5 Confirm `openspec validate snowflake-connector` passes.

## 8. Verify

- [x] 8.1 Run `poetry run pytest` — full suite green (fakesnow tier), coverage ≥ 80%. (225 passed, 3 live tests skipped, total coverage 95.9%.)
- [x] 8.2 Run `poetry run ruff check .` — clean.
- [x] 8.3 Run `poetry run sonar scan` against an existing Postgres test database to confirm the refactor preserved Postgres behaviour end-to-end (bundle, descriptions, relationships). (Verified via `pytest -m integration` — `TestScanCLI` exercises the full pipeline against the docker-compose Postgres fixture with the fake LLM client.)
- [ ] 8.4 Trigger the live-tier GitHub Actions workflow manually (`workflow_dispatch`) once before merge to confirm credentials wiring and that real-Snowflake smoke tests pass against `SNOWFLAKE_SAMPLE_DATA`. **Deferred** — no Snowflake account available for this change. Skip-when-credentials-absent path is verified locally; the live workflow itself is wired (`.github/workflows/snowflake-live.yml`) and will run the first time secrets are configured.
