## 1. Test harness

- [x] 1.1 Create `docker-compose.yml` at repo root running `postgres:16-alpine` on host port 5433, with env `POSTGRES_DB=sonar_test POSTGRES_USER=sonar POSTGRES_PASSWORD=sonar`, mounting `tests/fixtures/init.sql` at `/docker-entrypoint-initdb.d/init.sql`, and a `pg_isready` healthcheck.
- [x] 1.2 Create `tests/fixtures/init.sql` seeding the schema described in design.md §D10 (`users`, `addresses`, `products`, `orders`, `order_items`, `tags`, `product_tags`) with 3-5 rows per table.
- [x] 1.3 Extend `init.sql` to satisfy the Data Type Representation scenarios: add one `TEXT[]` column on `tags` (for the ARRAY case) and a `USER-DEFINED` enum type `order_status` used on `orders.status` (for the enum case).
- [x] 1.4 Bring up the DB with `docker compose up -d`, wait for healthy, and verify the seeded schema with `psql` (expect all seven tables and the `order_status` enum to be present).
- [x] 1.5 Add `pytest-cov = "^5.0"` to `[tool.poetry.group.dev.dependencies]` and run `poetry lock && poetry install`.
- [x] 1.6 In `pyproject.toml` under `[tool.pytest.ini_options]`, register the `integration` marker and add `addopts = "--cov=sonar --cov-report=term-missing"`; add `[tool.coverage.run] omit = ["tests/*"]`.
- [x] 1.7 Create `tests/conftest.py` with a session-scoped `@pytest_asyncio.fixture` that reads `TEST_DATABASE_URL` (default `postgresql://sonar:sonar@localhost:5433/sonar_test`), opens a `PostgresConnector` as an async context manager, and yields it.

## 2. Connection lifecycle

- [x] 2.1 Create `src/sonar/connectors/_sql.py` with module-level string constants `TABLES_AND_COLUMNS = ""` and `FOREIGN_KEYS = ""` (populated in Groups 3 and 4).
- [x] 2.2 In `src/sonar/connectors/postgres.py`, initialise `self._conn = None` in `__init__` and implement `__aenter__` (open `psycopg.AsyncConnection` via `await psycopg.AsyncConnection.connect(self._connection_string)`) and `__aexit__` (close the connection).
- [x] 2.3 At the top of each public method, raise `RuntimeError("PostgresConnector must be used as an async context manager")` when `self._conn is None`.
- [x] 2.4 Write `test_methods_outside_context_raise` (unit, no DB): instantiate connector without entering context; call each of the three public methods; assert `RuntimeError` with expected message.

## 3. Schema introspection

- [x] 3.1 Populate `TABLES_AND_COLUMNS` in `_sql.py` per design.md §D4, parameterised on `%(schemas)s`.
- [x] 3.2 Implement a private `_non_system_schemas()` method that queries `information_schema.schemata` and returns the schema names excluding `pg_catalog`, `information_schema`, and any name starting with `pg_`.
- [x] 3.3 Implement `discover_tables(schemas: list[str] | None = None)` using `psycopg.rows.dict_row`: resolve schemas (call `_non_system_schemas()` if `None`), execute the query, group rows by `(schema, table)` preserving ordinal order, and build immutable `Table` / `Column` objects. Apply the `udt_name` fallback from design.md §D5.
- [x] 3.4 Write `test_discover_tables_returns_all_user_tables` (integration): expect seven tables all in `public`.
- [x] 3.5 Write `test_default_schemas_exclude_system` (integration): no schema starting with `pg_` or named `information_schema` appears.
- [x] 3.6 Write `test_discover_tables_schema_filter` (integration): `schemas=["public"]` returns all seven; `schemas=["nonexistent"]` returns `[]`.
- [x] 3.7 Write `test_column_types_correct` (integration): spot-check `users.user_id` → `uuid`, `orders.placed_at` → `timestamp with time zone`, `products.price` → `numeric`.
- [x] 3.8 Write `test_array_type_surfaces_udt_name` (integration): the `TEXT[]` column on `tags` has `data_type == "_text"`.
- [x] 3.9 Write `test_user_defined_type_surfaces_udt_name` (integration): `orders.status` has `data_type == "order_status"`.
- [x] 3.10 Write `test_composite_pk_detected` (integration): both `order_items` PK columns and both `product_tags` PK columns have `is_primary_key=True`; no non-key column does.

## 4. Foreign key extraction

- [x] 4.1 Populate `FOREIGN_KEYS` in `_sql.py` per design.md §D6, using the `position_in_unique_constraint` join.
- [x] 4.2 Implement `discover_relationships()` using `psycopg.rows.dict_row`: execute the query and return `list[ForeignKey]` with one entry per column pair.
- [x] 4.3 Write `test_discover_relationships_finds_all_fks` (integration): expect at least the six FKs implied by the seed schema (`addresses→users`, `orders→users`, `order_items→orders`, `order_items→products`, `product_tags→products`, `product_tags→tags`).
- [x] 4.4 Write `test_simple_fk_fields_correct` (integration): the `orders.user_id → users.user_id` FK has the exact source/target schema/table/column values expected by the spec.

## 5. Row sampling

- [x] 5.1 Implement the private `_serialize_row(row: dict) -> dict` helper in `postgres.py` applying the coercion table from design.md §D9 to UUID, datetime, date, Decimal, and bytes; return a new dict (no mutation).
- [x] 5.2 Implement `sample_table(schema: str, table: str, limit: int = 5)` using `psycopg.sql.SQL(...).format(psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table), psycopg.sql.Literal(limit))` and `psycopg.rows.dict_row`; apply `_serialize_row` to each row before returning.
- [x] 5.3 Write `test_sample_table_default_limit` (integration): calling without `limit` against a table with more than 5 rows returns exactly 5.
- [x] 5.4 Write `test_sample_table_custom_limit` (integration): `limit=3` against a table with at least 3 rows returns exactly 3.
- [x] 5.5 Write `test_sample_table_row_shape` (integration): every row is a dict whose keys equal the table's column names.
- [x] 5.6 Write `test_sample_table_values_json_serialisable` (integration): sample `users` (UUID + TIMESTAMPTZ) and `products` (NUMERIC); confirm UUID is `str`, timestamp is an ISO 8601 `str`, NUMERIC is `float`; assert `json.dumps(row)` succeeds for every returned row.

## 6. Validation and wrap-up

- [x] 6.1 Run `poetry run pytest -m integration` — all tests in Groups 2-5 pass.
- [x] 6.2 Check coverage: `src/sonar/connectors/postgres.py` and `src/sonar/connectors/_sql.py` each at or above 80%.
- [x] 6.3 Run `openspec validate postgres-schema-discovery` — passes.
- [x] 6.4 Append a `## Postgres Connector` section to `LEARNINGS.md` covering: `information_schema` permission scoping vs `pg_catalog`; psycopg3 row factories (`dict_row`); the `udt_name` fallback for ARRAY and USER-DEFINED types; the `position_in_unique_constraint` join for composite FK alignment; why `psycopg.sql.Identifier` is used even for trusted inputs; why async is the right shape for a one-shot scan given downstream MCP integration.
