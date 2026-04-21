## Context

Sonar's repo is scaffolded but empty: `src/sonar/connectors/postgres.py` declares frozen dataclasses `Column`, `Table`, and `ForeignKey` plus a `PostgresConnector` class with three async methods that raise `NotImplementedError`. `openspec/specs/` is empty. No runtime dependency beyond `psycopg[binary]` and the standard library is required for this change. Tests do not yet exist, and no test-Postgres infrastructure is in place.

The spec for this change (see `specs/postgres-connector/spec.md`) establishes five requirements covering connection lifecycle, schema introspection, data-type representation, foreign-key extraction, and row sampling. The design below commits to the technical decisions that realise those requirements.

## Goals / Non-Goals

**Goals:**

- Decide on a single, low-ceremony connection lifecycle model for a one-shot scanning tool.
- Fix the exact introspection queries (`information_schema` vs `pg_catalog`) and document the non-obvious joins up front.
- Pin a type-coercion table for `sample_table` return values.
- Define the test harness (Docker Compose + seed SQL + pytest fixtures) so it is reusable by subsequent changes (`llm-description-engine`, `relationship-mapping`).

**Non-Goals:**

- No connection pooling. Sonar scans a database once per invocation; a pool adds overhead without benefit.
- No row-count gathering in this change. The `Table.row_count` field remains `None`; a later change may populate it from `pg_class.reltuples` if the LLM-description stage shows it is needed.
- No CLI wiring. `sonar scan` remains a placeholder until the `context-index` change.
- No cross-schema FK filtering parameter on `discover_relationships()`. Callers filter results as needed.
- No runtime handling of databases where the connecting role cannot see `information_schema`. This is a known-unknown for later deployment contexts, not a requirement for the test harness.

## Decisions

### D1. Connection lifecycle via async context manager

`PostgresConnector` implements `__aenter__` and `__aexit__`. `__aenter__` opens a single `psycopg.AsyncConnection` via `await psycopg.AsyncConnection.connect(self._connection_string)` and stores it as `self._conn`. `__aexit__` closes it. `self._conn` is `None` outside the context.

Alternatives considered: per-method connect/disconnect (adds handshake cost on every call, ugly for integration tests that call three methods per test), connection pool (overkill — never more than one concurrent query), single connection opened in `__init__` (breaks the "context manager is the ceremony" contract and leaks connections on error).

### D2. Introspection query placement

SQL strings live in a dedicated module `src/sonar/connectors/_sql.py` as module-level constants `TABLES_AND_COLUMNS` and `FOREIGN_KEYS`. `postgres.py` imports them. Reason: the queries are long enough that inlining them pushes the connector file past the readable-in-one-screen threshold, and a separate module gives later changes a natural place to add more query constants.

### D3. Introspection queries use `information_schema`

All introspection goes through `information_schema` rather than `pg_catalog`. Tradeoff accepted: `information_schema` silently hides tables the connecting role lacks `USAGE` on. For the test harness the role owns all objects, so visibility is complete. For production deployments this becomes a concern handled by the future `mcp-server` change.

### D4. `TABLES_AND_COLUMNS` query shape

Three joins: `information_schema.columns` to `information_schema.tables` (filter to `BASE TABLE`) to `information_schema.key_column_usage` via a `LEFT JOIN` gated by an `EXISTS` subquery on `information_schema.table_constraints` where `constraint_type = 'PRIMARY KEY'`. The `schemas` parameter is bound as a Postgres `text[]` with `WHERE c.table_schema = ANY(%(schemas)s)`. When the caller passes `schemas=None`, a preliminary query against `information_schema.schemata` lists non-system schemas (exclude `pg_catalog`, `information_schema`, and any name starting with `pg_`) and the result is passed as the `schemas` parameter.

### D5. Data-type mapping for ARRAY and USER-DEFINED

Returned `Column.data_type` is `udt_name` when `information_schema.columns.data_type` is the literal `'ARRAY'` or `'USER-DEFINED'`; otherwise `data_type`. Rationale: the SQL-standard `data_type` value is not informative for arrays or user-defined types; the `udt_name` is (e.g. `_int4` for `integer[]`, or the enum/domain name).

### D6. `FOREIGN_KEYS` query uses `position_in_unique_constraint`

The query joins `information_schema.referential_constraints` to `information_schema.key_column_usage` twice — once for the source side on `constraint_name`, once for the target side on `(unique_constraint_name, unique_constraint_schema, ordinal_position = position_in_unique_constraint)`. The `position_in_unique_constraint` equality is the load-bearing join: it aligns columns in composite foreign keys. Aligning by column name would fail when the referenced column has a different name in the target table.

### D7. psycopg3 row factory

Both introspection queries and row sampling use `psycopg.rows.dict_row` as the row factory, yielding `dict[str, Any]` rows. This keeps the grouping logic in `discover_tables` simple and makes `sample_table` output shape trivially correct.

### D8. Row sampling uses parameterised identifiers

`sample_table` builds its query with `psycopg.sql.SQL("SELECT * FROM {}.{} LIMIT {}").format(psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table), psycopg.sql.Literal(limit))`. Although `schema` and `table` come from prior discovery and are trusted, `Identifier` is used anyway — it handles reserved words, mixed case, and quotes for free, and costs nothing.

### D9. Value coercion table for `sample_table`

A private module-level function `_serialize_row(row: dict) -> dict` returns a new dict with the following transformations applied to each value:

| Input type | Output |
|---|---|
| `uuid.UUID` | `str(value)` |
| `datetime.datetime`, `datetime.date` | `value.isoformat()` |
| `decimal.Decimal` | `float(value)` |
| `bytes` | `"<binary>"` (sentinel — base64 would bloat LLM prompts; a sentinel conveys presence without content) |
| Everything else | pass through |

No mutation — `_serialize_row` returns a new dict per row.

### D10. Test harness

`docker-compose.yml` at repo root runs `postgres:16-alpine` on port `5433` (to avoid colliding with a local 5432). Environment: `POSTGRES_DB=sonar_test POSTGRES_USER=sonar POSTGRES_PASSWORD=sonar`. The container mounts `tests/fixtures/init.sql` at `/docker-entrypoint-initdb.d/init.sql` for auto-seeding on first start.

`tests/fixtures/init.sql` seeds a small e-commerce schema with exactly the shapes the spec's scenarios require: `users` (UUID PK, TIMESTAMPTZ), `addresses`, `products` (NUMERIC, TIMESTAMPTZ), `orders`, `order_items` (composite PK), `tags`, `product_tags` (composite PK). This gives at minimum seven tables, one UUID PK, two composite PKs, one multi-hop FK chain, a NUMERIC column, and TIMESTAMPTZ columns — enough to exercise every spec scenario.

`tests/conftest.py` provides a session-scoped `@pytest_asyncio.fixture` that reads `TEST_DATABASE_URL` from the environment (defaulting to `postgresql://sonar:sonar@localhost:5433/sonar_test`), opens a `PostgresConnector` as an async context manager, and yields it. A `@pytest.mark.integration` marker (registered in `pyproject.toml`) lets CI skip live-DB tests with `-m "not integration"`.

### D11. Coverage tooling

`pytest-cov` is added to `[tool.poetry.group.dev.dependencies]`. `pyproject.toml` gains `addopts = "--cov=sonar --cov-report=term-missing"` under `[tool.pytest.ini_options]` and `[tool.coverage.run] omit = ["tests/*"]`. Target coverage on `src/sonar/connectors/*.py` is 80%.

## Risks / Trade-offs

- **Risk:** `information_schema` is permission-scoped; tables the role lacks `USAGE` on vanish silently. → **Mitigation:** Not a problem for the owned test DB. Flagged as a follow-up for the `mcp-server` change when Sonar will be pointed at user-owned databases.
- **Risk:** `pytest-asyncio` mode drift between 0.21 and 0.24 — the `asyncio_mode = "auto"` setting behaves differently. → **Mitigation:** `pyproject.toml` pins `pytest-asyncio = "^0.24"`, which auto-collects async tests without the `@pytest.mark.asyncio` decorator.
- **Risk:** Port 5432 is commonly in use locally. → **Mitigation:** Compose maps the container's 5432 to host 5433, and the default `TEST_DATABASE_URL` uses 5433.
- **Trade-off:** Using the same pytest fixture for every test means each test operates on the same seed state. Tests must not mutate seed data. Acceptable — every spec requirement is read-only.
- **Trade-off:** Bytes coerced to a sentinel string lose information. Acceptable for LLM sampling context; a later change may revisit if MCP consumers need raw binary data.

## Open Questions

None blocking. All decisions resolved during proposal/design authoring.
