# duckdb-connector Design

## Context

Sonar has two connectors: Postgres (async, psycopg3) and Snowflake (sync driver wrapped in `asyncio.to_thread`). Both follow the same observable contract: async context manager, `discover_tables`, `discover_relationships`, `sample_table`. DuckDB is an embedded, in-process database — no server, no credentials, file-based. The connector must fit the same contract while adapting to DuckDB's Python API (synchronous only) and its data model (single-file catalog, one or more schemas, `main` as default).

## Goals / Non-Goals

**Goals:**
- Add `DuckDBConnector` with the same observable contract as the existing connectors.
- Support local `.duckdb` files and `:memory:` databases.
- CLI dispatch via `duckdb://` prefix.
- Schema enumeration (same as Postgres — covers dbt projects with multiple schemas).
- Row counts from DuckDB's internal statistics.
- FK discovery via INFORMATION_SCHEMA.
- In-memory test database — no mocks, no fakes, no external services.

**Non-Goals:**
- MotherDuck (`md:` cloud databases) — explicitly out of scope.
- Attached external files (CSV, Parquet) — no special handling. Files registered as tables appear in INFORMATION_SCHEMA automatically; unregistered files do not.
- Shared SQL module across connectors — not yet justified (see D4).

## Decisions

### D1: Async via `asyncio.to_thread`

DuckDB's Python API is synchronous. Wrap blocking calls in `asyncio.to_thread`, identical to the Snowflake connector's approach. DuckDB is in-process (microsecond latency), so this is effectively zero-cost but keeps the connector's public surface async-consistent with the rest of Sonar.

Revisit when: DuckDB ships an official async Python API.
Reversibility: cheap — drop the `to_thread` wrapper, make methods natively async.

### D2: Schema enumeration — enumerate non-system schemas

Query `information_schema.schemata`, exclude `information_schema` and `pg_catalog` (DuckDB emulates the latter). Same pattern and exclusion list as the Postgres connector.

Alternative considered: default to `main` (the single schema most DuckDB files have). Rejected because dbt-backed DuckDB files routinely carry `raw`, `staging`, and `marts` schemas — defaulting to `main` causes silent partial discovery with no error. Enumeration costs one lightweight query at startup; the common single-schema case returns `['main']` with negligible overhead.

Revisit when: a user reports unexpected system schemas returned by enumeration not covered by the exclusion list.
Reversibility: cheap.

### D3: Row counts from `duckdb_tables()`

DuckDB exposes a built-in table function `duckdb_tables()` with an `estimated_size` column (approximate row count, updated by the optimizer). Analogous to Postgres's `pg_class.reltuples`. Use it as a JOIN in the discovery query.

Alternative considered: `SELECT COUNT(*) FROM each_table`. Rejected — expensive for large files; defeats the purpose of approximate statistics.

Revisit when: first user reports `row_count` values are significantly wrong (order-of-magnitude off) for their file.
Reversibility: cheap — swap the source column.

### D4: Separate `_duckdb_sql.py` module

SQL strings live in their own module, same pattern as `_sql.py` (Postgres) and `_snowflake_sql.py` (Snowflake). No shared SQL module across connectors.

The information_schema queries are structurally similar but differ in: row-count source (`pg_class` vs `duckdb_tables()`), identifier quoting conventions, and parameterization syntax (`ANY(%(schemas)s)` vs DuckDB's positional `?` / list expansion). A shared layer would paper over these differences without eliminating them.

Revisit when: a third or fourth connector shares enough SQL that the duplication cost exceeds the abstraction cost.
Reversibility: cheap — merge into a shared module at that point.

### D5: DSN format — `duckdb://` prefix, strip to raw path

Strip the `duckdb://` prefix to obtain the DuckDB path argument:
- `duckdb:///abs/path/file.duckdb` → `/abs/path/file.duckdb`
- `duckdb://relative/file.duckdb` → `relative/file.duckdb`
- `duckdb://:memory:` → `:memory:`

This is consistent with the `postgresql://` and `snowflake://` dispatch conventions. The CLI help text lists all accepted forms including examples.

Alternative considered: bare file path detection (e.g. any `.duckdb` extension). Rejected — ambiguous in the CLI dispatch chain and requires heuristic rather than explicit prefix.

Revisit when: a user reports the prefix format is unintuitive for a local file path.
Reversibility: cheap — add a bare-path alias in CLI dispatch without removing the prefix form.

### D6: No cross-database FK filtering

Snowflake requires FK filtering because `INFORMATION_SCHEMA` can expose FKs to tables in other Snowflake databases. DuckDB files are self-contained single-catalog databases — FKs can only reference tables within the same file. No filtering logic needed.

Revisit when: DuckDB adds cross-attached-database FK support.
Reversibility: cheap — additive filter.

### D7: Open connections in read-only mode

The connector SHALL pass `read_only=True` to `duckdb.connect()`. This prevents accidental writes during a scan and, more importantly, allows scanning files that are already held open by another writer (e.g. an active dbt run or an open notebook). Snowflake has no equivalent — its access is governed by server-side roles, not a connection flag. This is DuckDB-specific.

Revisit when: a use case requires writing to DuckDB through Sonar (none currently — the connector contract is read-only by design).
Reversibility: cheap — remove the flag.

### D8: Identifier quoting in `sample_table`

DuckDB uses standard SQL double-quote identifier quoting — the same convention as Snowflake. `sample_table` SHALL use a `_quote_identifier` helper (wrap in double quotes, escape internal double-quotes with `""`, reject null bytes) and call `_reject_dotted_identifier` as a pre-check before composing the query. This follows the Snowflake connector exactly (`_quote_identifier` in `snowflake.py`). DuckDB table/schema names cannot be parameterized as values — only data values can be bound — so quoting is the correct approach.

Revisit when: DuckDB adds a safe identifier-quoting API to its Python library.
Reversibility: cheap.

## Risks / Trade-offs

- **`estimated_size` accuracy**: DuckDB's row count estimate can be stale for files that haven't been vacuumed or analysed. Same caveat as Postgres's `reltuples`. Surfaced as `row_count: int | None` — downstream consumers already handle `None`.
- **Single-writer limitation**: DuckDB files allow only one writer at a time. If a user runs `sonar scan` while another process has the file open for writing, the connection will fail. This surfaces as a clear driver-level error; no special handling needed.
- **`:memory:` utility**: In-memory databases are primarily useful for testing and one-shot MCP server demos. The bundle written by `sonar scan` persists after the connection closes, so the data is not lost even if the DuckDB instance isn't.

## Open Questions

None — decisions above cover all open questions surfaced during exploration.
