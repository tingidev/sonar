# duckdb-connector Specification

## Purpose

DuckDB data source adapter. Connects to local `.duckdb` files and `:memory:` databases. Provides schema discovery, relationship discovery, and data sampling with the same observable contract as the Postgres and Snowflake connectors.

## ADDED Requirements

### Requirement: DuckDB connector implements the data-source connector contract

The system SHALL provide `DuckDBConnector` implementing the same observable contract as `PostgresConnector` and `SnowflakeConnector`: an async context manager lifecycle, a `discover_tables` method returning `list[Table]`, a `discover_relationships` method returning `list[ForeignKey]`, and a `sample_table` method returning `list[dict]`. Returned shapes SHALL use the shared `Column`, `Table`, and `ForeignKey` dataclasses so downstream consumers (`relationship-mapping`, `description-engine`, `context-index`, `mcp-server`) operate on DuckDB output without branching.

#### Scenario: Discovery returns shared Table shape

- **WHEN** `DuckDBConnector.discover_tables` is called against a DuckDB database
- **THEN** it returns `list[Table]` from the shared connector types module, with each Table carrying `schema`, `name`, `columns`, and `row_count` fields

#### Scenario: Foreign-key extraction returns shared ForeignKey shape

- **WHEN** `DuckDBConnector.discover_relationships` is called
- **THEN** it returns `list[ForeignKey]` from the shared connector types module, populated for every FK constraint declared in the database

#### Scenario: Sample rows are serialized through the shared coercion helper

- **WHEN** `DuckDBConnector.sample_table` is called
- **THEN** it returns a list of dicts whose values are coerced through the shared row-serialization helper (dates → ISO strings, decimals → floats, binary → `<binary>` placeholder)

### Requirement: DuckDB connector connects to local files and in-memory databases

The connector SHALL accept a DuckDB path string — either an absolute or relative file path or the special value `:memory:` — and open a DuckDB connection using the synchronous `duckdb` Python library wrapped in `asyncio.to_thread`. The connector MUST be used as an async context manager; calling discovery or sampling methods outside the context manager SHALL raise `RuntimeError`.

#### Scenario: Connector opens a local file

- **WHEN** `DuckDBConnector("/path/to/file.duckdb")` is used as an async context manager
- **THEN** it opens a DuckDB connection to the specified file

#### Scenario: Connector opens an in-memory database

- **WHEN** `DuckDBConnector(":memory:")` is used as an async context manager
- **THEN** it opens an in-memory DuckDB database

#### Scenario: Methods called outside context manager raise RuntimeError

- **WHEN** any of `discover_tables`, `discover_relationships`, or `sample_table` is called without entering the async context manager first
- **THEN** a `RuntimeError` is raised

### Requirement: Schema enumeration excludes system schemas

The connector SHALL enumerate schemas by querying `information_schema.schemata` and excluding `information_schema` and `pg_catalog`. When `discover_tables` is called with no schema filter, all non-system schemas are discovered. When called with an explicit schema list, only those schemas are queried.

#### Scenario: Default enumeration excludes system schemas

- **WHEN** `discover_tables` is called with no schema filter
- **THEN** it queries all schemas returned by the non-system schema enumeration query (excluding `information_schema` and `pg_catalog`)

#### Scenario: Explicit schema filter is respected

- **WHEN** `discover_tables` is called with `schemas=["main"]`
- **THEN** only the `main` schema is queried, regardless of what other schemas exist

#### Scenario: Multiple schemas are all discovered

- **WHEN** a DuckDB file contains schemas `main`, `staging`, and `marts`
- **THEN** `discover_tables` with no filter returns tables from all three schemas

### Requirement: Row counts are sourced from DuckDB internal statistics

The connector SHALL populate `Table.row_count` from the `estimated_size` column of the `duckdb_tables()` built-in function. When no estimate is available, `row_count` SHALL be `None`.

#### Scenario: Row count populated from duckdb_tables

- **WHEN** `discover_tables` is called and `duckdb_tables()` returns an `estimated_size` for a table
- **THEN** the corresponding `Table.row_count` is set to that integer value

#### Scenario: Missing estimate yields None

- **WHEN** `duckdb_tables()` returns no row for a table or `estimated_size` is NULL
- **THEN** `Table.row_count` is `None`

### Requirement: CLI dispatches DuckDB invocations from a `duckdb://` prefix

The `sonar scan` and `sonar serve` commands SHALL accept DuckDB invocations via the `duckdb://` prefix on the positional connection argument. The prefix is stripped to obtain the raw DuckDB path: `duckdb:///abs/path.duckdb` → `/abs/path.duckdb`, `duckdb://relative.duckdb` → `relative.duckdb`, `duckdb://:memory:` → `:memory:`. Existing Postgres and Snowflake dispatch are unchanged.

#### Scenario: Absolute file path dispatches to DuckDB connector

- **WHEN** the user runs `sonar scan duckdb:///home/user/analytics.duckdb`
- **THEN** the CLI constructs a `DuckDBConnector` with path `/home/user/analytics.duckdb`

#### Scenario: Relative file path dispatches to DuckDB connector

- **WHEN** the user runs `sonar scan duckdb://./local.duckdb`
- **THEN** the CLI constructs a `DuckDBConnector` with path `./local.duckdb`

#### Scenario: In-memory target dispatches to DuckDB connector

- **WHEN** the user runs `sonar scan duckdb://:memory:`
- **THEN** the CLI constructs a `DuckDBConnector` with path `:memory:`

### Requirement: DuckDB driver is an optional dependency with dispatch-time guard

The `duckdb` package SHALL be an optional installation extra. When the extra is not installed, dispatching a `duckdb://` invocation MUST fail at the CLI dispatch point with an actionable error message pointing to the install command for the extra, before any path resolution or connection is attempted.

#### Scenario: DuckDB dispatch with missing extra fails fast

- **WHEN** the user runs `sonar scan duckdb://...` and the `duckdb` package is not installed
- **THEN** the CLI exits non-zero before attempting to open any file, with an error message that includes the install command for the DuckDB extra
