## Why

Postgres is currently Sonar's only data source. Snowflake is the highest-value second connector to ship: it's where modern data warehousing lives, its foreign-key constraints are informational-only (so users frequently don't declare them), which is exactly the schema shape where the naming-heuristic inference from `inferred-relationships` (#7) earns its keep. Adding the second connector also forces the connector boundary to stop being imagined — the shared dataclasses already leak across the codebase from `connectors.postgres`, including one private cross-import (`_coerce_value` into `mcp/tools/sample_tool.py`), and a second connector turns that into a real source of breakage.

## What Changes

- New `SnowflakeConnector` implementing the same async contract as `PostgresConnector` (`discover_tables`, `discover_relationships`, `sample_table`, async-context-manager lifecycle).
- Extract shared dataclasses into `connectors/types.py` (`Column`, `Table`, `ForeignKey`) and the row-coercion helper into `connectors/serialize.py` (`_coerce_value`). Pure refactor — the existing behaviour of `PostgresConnector` is unchanged. Eliminates the existing private cross-import.
- Optional dependency: `snowflake-connector-python` is gated behind a Poetry extras group (`sonar[snowflake]`). Postgres-only users don't pay the install cost.
- Driver-availability check happens at CLI dispatch, not at `__aenter__`. If the extra is not installed, `sonar scan snowflake` (or `snowflake://...`) fails before any credentials are typed with an actionable `pip install sonar[snowflake]` message.
- CLI dispatch grammar grows from one form to three positional forms:
  - `sonar scan postgresql://...` (and `postgres://...`) — Postgres, unchanged.
  - `sonar scan snowflake://user:pass@account/db/schema?warehouse=...` — Snowflake password auth.
  - `sonar scan snowflake` — Snowflake auth via a curated set of `SNOWFLAKE_*` environment variables (covering password, key-pair, OAuth, and externalbrowser SSO). The set is curated rather than driver-pass-through so future driver kwarg renames don't silently break user shell configs.
- Snowflake-shaped behaviours:
  - 2-level identifiers `(schema, table)` — the database is connector-config, not table-shape, so the bundle/index/MCP surface is untouched.
  - Row counts pulled from `INFORMATION_SCHEMA.TABLES.ROW_COUNT`; `None` when missing.
  - Async via `asyncio.to_thread` wrapping of the sync driver.

## Capabilities

### New Capabilities
- `snowflake-connector`: schema discovery, foreign-key extraction, row sampling, dispatch grammar, and optional-dependency discipline for Snowflake data sources.

### Modified Capabilities
(none — extracting the shared types is an implementation refactor that preserves all `postgres-connector` behaviour at the spec level.)

## Impact

- **New code**: `src/sonar/connectors/snowflake.py`, `src/sonar/connectors/types.py`, `src/sonar/connectors/serialize.py`, plus a Snowflake SQL constants module.
- **Refactored**: `src/sonar/connectors/postgres.py` (re-exports types from `types.py` for back-compat at the module path; ideally callers move to `connectors.types` directly), `src/sonar/cli.py` (dispatch grammar + lazy-import guard), and 14 import sites pointing at `connectors.postgres` for `Column`/`Table`/`ForeignKey`/`_coerce_value`.
- **New tests**: `tests/test_snowflake_connector.py` covering URL parsing, env-var dispatch (curated set), missing-extra dispatch guard, discovery, FK extraction, sampling, and the cross-database FK skip + scan-summary surfacing — all running against [`fakesnow`](https://github.com/tekumara/fakesnow), a DuckDB-backed Snowflake emulator (added as a dev dependency). Plus `tests/test_snowflake_live.py` containing live-account smoke tests tagged `@pytest.mark.snowflake_live` and skipped by default.
- **pyproject.toml**: new `[tool.poetry.extras] snowflake` group; new optional dependency `snowflake-connector-python`; new dev dependency `fakesnow`. Lockfile updates.
- **CI**: default test job runs with `poetry install -E snowflake` so fakesnow-backed Snowflake tests run on every PR. A second workflow runs live-tier tests only on push to `main` and `workflow_dispatch`; PRs from forks never see live credentials.
- **README**: Snowflake quickstart section, password-in-URL caveat nudging users toward env-var auth.
- **ROADMAP.md**: mark #9 in flight.
- **No data-format changes**: existing `.sonar/` bundles stay valid. New Snowflake bundles use the same schema.
