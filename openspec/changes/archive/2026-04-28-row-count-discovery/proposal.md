## Why

`Table.row_count` is part of the data shape returned by `discover_tables`, but the postgres-connector implementation never populates it — every table in every bundle currently carries `row_count=None`. Agents using the MCP `discover` tool to decide which tables matter (size as a proxy for importance) get no signal. Closing this gap is also one item on the pre-launch checklist.

## What Changes

- The postgres-connector's `discover_tables` populates `row_count` for every returned table.
- Row counts come from Postgres planner statistics (`pg_class.reltuples`), not exact `COUNT(*)` — this is a deliberate accuracy-vs-cost trade documented in `design.md`.
- A small integration-test addition exercises row counts on the ChEMBL fixture and on freshly-loaded tables (statistics-not-yet-collected case).
- No public API additions: the field shape `int | None` is unchanged. `None` continues to mean "unknown" — now narrowly, for tables that genuinely have no usable statistics.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `postgres-connector`: `Schema Introspection` requirement extended so each returned `Table` carries a populated `row_count` derived from connector-available statistics, with a clear contract for the unknown case.

## Impact

- Code: `src/sonar/connectors/postgres.py` (the `discover_tables` query gains a join or subselect against `pg_class`), one new integration test in `tests/integration/test_postgres_connector.py`.
- No new dependencies, no new public types, no migrations. Bundle JSON shape is unchanged — `row_count` was always serialised; values just stop being `null` for live tables.
- Downstream MCP `discover` tool starts surfacing real row counts; no MCP signature change.
