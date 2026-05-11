## Why

Sonar's two existing connectors are both server-based and require credentials. DuckDB is the dominant local analytics database — it's the backend for dbt local workflows, ad-hoc data science, and in-process analytics pipelines. Adding it gives users a zero-credential path: point Sonar at a file, get an MCP context server.

## What Changes

- New `DuckDBConnector` class: schema discovery, relationship discovery, and data sampling against local `.duckdb` files and `:memory:` databases.
- CLI DSN dispatch extended to recognise the `duckdb://` prefix.
- `duckdb` added as an optional dependency (guarded at dispatch time, same pattern as Snowflake).
- Schema enumeration: non-system schemas discovered automatically, same as Postgres (`information_schema` and `pg_catalog` excluded). DuckDB files from dbt projects routinely carry `raw`, `staging`, `marts` — defaulting to `main` would silently drop them.

## Capabilities

### New Capabilities

- `duckdb-connector`: DuckDB data source adapter. Connects to local `.duckdb` files and `:memory:` databases. Discovers schemas, tables, columns, primary keys, foreign keys, and row counts. Exposes `discover_tables`, `discover_relationships`, and `sample_table` with the same observable contract as the Postgres and Snowflake connectors.

### Modified Capabilities

## Impact

- `src/sonar/connectors/duckdb.py` — new connector module
- `src/sonar/connectors/_duckdb_sql.py` — SQL strings for DuckDB discovery
- `src/sonar/cli.py` — DSN dispatch (`duckdb://` prefix), optional-dep guard
- `pyproject.toml` — `duckdb` optional dependency, `duckdb` extras group
- `tests/test_duckdb_connector.py` — in-memory DuckDB, no fakes or mocks required
