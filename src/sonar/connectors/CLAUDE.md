# Connectors

Database-specific adapters for schema discovery and data sampling. Each connector speaks one database dialect and emits the shared types from `types.py`.

## Interface contract

Every connector must implement:

- `__aenter__` / `__aexit__` — async context manager for connection lifecycle
- `discover_tables(schemas: list[str] | None = None) -> list[Table]` — enumerate tables with columns
- `discover_relationships() -> list[ForeignKey]` — foreign keys, database-wide (not schema-filtered)
- `sample_table(schema: str, table: str, limit: int = 5) -> list[dict]` — return sample rows as dicts

Return types are the frozen dataclasses from `types.py`: `Table`, `Column`, `ForeignKey`. No connector-specific types in return values.

## Implementation patterns

**Identifier quoting.** Every connector must quote user-supplied identifiers to prevent SQL injection. Use the driver's built-in mechanism when available (psycopg's `sql.Identifier` for Postgres). When the driver has no quoting facility, implement `_quote_identifier` with null-byte rejection and double-quote escaping — see Snowflake/DuckDB for the pattern.

**Dotted identifier rejection.** Call `_reject_dotted_identifier` from `types.py` on schema, table, and column identifiers in `discover_relationships`. This keeps the context-index bundle's key encoding unambiguous.

**Limit validation.** `sample_table` must validate the `limit` parameter before constructing a query:
```python
if not isinstance(limit, int) or limit < 0:
    raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
```

**Row serialization.** Sample rows pass through `_serialize_row` from `serialize.py` before return. This handles type coercion (datetimes, decimals, bytes). If the driver returns non-dict rows (e.g. BigQuery `Row` objects), convert to `dict` first.

**Row count extraction.** Implement a `_row_count_from_row(row: dict) -> int | None` function for table discovery. Map driver-specific sentinels to `None` (e.g. Postgres returns -1 for unvacuumed tables).

**Connection guard.** Methods that need a live connection check `self._conn is not None` and raise with a clear message directing the user to the async context manager.

## Sync drivers

Connectors wrapping a synchronous driver (Snowflake, DuckDB, BigQuery) use `asyncio.to_thread` for all blocking calls. Shared cursor operations (`fetch_dicts`, `fetch_rows`) live in `_cursor_utils.py` — import from there, don't duplicate.

## SQL queries

Complex SQL lives in a companion `_<db>_sql.py` module (e.g. `_snowflake_sql.py`, `_bigquery_sql.py`). This keeps the connector class focused on orchestration. The SQL module must be dependency-free (no driver imports) so it can be tested in isolation.

## Adding a new connector

1. Create `<db>.py` implementing the interface contract above
2. Create `_<db>_sql.py` for any non-trivial SQL
3. If the driver is optional, add it as a Poetry extra in `pyproject.toml` and add a driver check in `cli.py` (see `_ensure_duckdb_driver` pattern)
4. Register the connector type in `cli.py`'s DSN dispatch
5. Add a quickstart section to `README.md`
6. Update the roadmap table in `README.md`

## Testing

Each connector has its own test file. Testing strategy depends on the backend:

- **Postgres** — integration tests against a real instance via Docker (`tests/fixtures/chembl/`). Tests in `test_postgres_connector.py` require a running database and are skipped in environments without one.
- **Snowflake** — unit tests against `fakesnow` (DuckDB-backed emulator, dev dependency). Live tests tagged `@pytest.mark.snowflake_live`, skipped by default.
- **DuckDB** — unit tests against real in-process DuckDB. No mocking needed since DuckDB is in-process.
- **BigQuery** — unit tests with monkeypatched client. Guard with `pytest.importorskip("google.cloud.bigquery")` at the top of the test file since the driver is an optional dependency.

Test the `_<db>_sql.py` module independently where it exists. Cover identifier quoting edge cases (backticks, null bytes, special characters).
