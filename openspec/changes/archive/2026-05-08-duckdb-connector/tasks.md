## 1. Dependencies and package setup

- [x] 1.1 Add `duckdb` as an optional dependency in `pyproject.toml` with a `duckdb` extras group, mirroring the `snowflake` extras pattern

## 2. SQL module

- [x] 2.1 Create `src/sonar/connectors/_duckdb_sql.py` with `TABLES_AND_COLUMNS` query joining `information_schema.columns`, `information_schema.tables`, `information_schema.key_column_usage` (PKs), and `duckdb_tables()` (row counts)
- [x] 2.2 Add `FOREIGN_KEYS` query using `information_schema.referential_constraints` + `key_column_usage` (same structure as `_sql.py`)
- [x] 2.3 Add `NON_SYSTEM_SCHEMAS` query excluding `information_schema` and `pg_catalog`

## 3. Connector implementation

- [x] 3.1 Create `src/sonar/connectors/duckdb.py` with `DuckDBConnector` class: constructor accepting a path string, async context manager using `asyncio.to_thread` to open (`read_only=True`) and close the connection
- [x] 3.2 Implement `discover_tables` — run `TABLES_AND_COLUMNS`, enumerate schemas if none provided, return `list[Table]` via row-assembly helpers
- [x] 3.3 Implement `discover_relationships` — run `FOREIGN_KEYS`, return `list[ForeignKey]`
- [x] 3.4 Implement `sample_table` — call `_reject_dotted_identifier` on schema and table, quote both with `_quote_identifier` (double-quote wrapping, null-byte guard), compose `SELECT * FROM "schema"."table" LIMIT n`, return serialized rows via `_serialize_row`
- [x] 3.5 Add `_quote_identifier` helper (same as Snowflake connector: double-quote wrap, escape internal `"` as `""`, reject null bytes)
- [x] 3.6 Add `_tables_from_rows`, `_column_from_row`, `_foreign_keys_from_rows` private helpers mirroring the Postgres/Snowflake pattern

## 4. CLI dispatch

- [x] 4.1 Add `_ensure_duckdb_driver()` guard in `cli.py` (lazy import check, exits with actionable install message if `duckdb` is not installed)
- [x] 4.2 Add `duckdb://` dispatch branch in `_resolve_connector` (or equivalent dispatch logic): strip prefix, call guard, construct `DuckDBConnector`
- [x] 4.3 Update CLI help text to list `duckdb://` as an accepted DSN form alongside the existing Postgres and Snowflake forms

## 5. Tests

- [x] 5.1 Create `tests/test_duckdb_connector.py` with a `@pytest.fixture` that creates an in-memory DuckDB database with two related tables (users + orders with a FK)
- [x] 5.2 Test `discover_tables`: assert both tables returned with correct columns, types, nullability, and PK flags
- [x] 5.3 Test `discover_tables` row counts: assert `row_count` populated from `duckdb_tables()` estimated size
- [x] 5.4 Test `discover_relationships`: assert FK from `orders.user_id` → `users.id` is returned
- [x] 5.5 Test `sample_table`: assert rows returned and serialized correctly
- [x] 5.6 Test schema enumeration: create a DB with two schemas (`main` + `staging`), assert both discovered with no filter, only `main` discovered with explicit filter
- [x] 5.7 Test `:memory:` path: assert connector opens and discovers tables against an in-memory DB
- [x] 5.8 Test context manager guard: assert `RuntimeError` raised if `discover_tables` called outside context manager
- [x] 5.9 Test optional dep guard: mock `duckdb` as uninstalled, assert CLI exits non-zero with install message on `duckdb://` dispatch
