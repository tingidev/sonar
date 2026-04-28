## 1. Connector query

- [x] 1.1 Extend `_sql.TABLES_AND_COLUMNS` to project `pg_class.reltuples::bigint` (or `NULL` when negative) per `(schema, table)`, joined via `pg_namespace` and `pg_class`. Preserve existing column ordering and the `c.table_schema = ANY(%(schemas)s)` filter.
- [x] 1.2 Verify the modified query still returns one row per `(schema, table, column)` tuple (no fan-out from the new join). Existing column-level integration tests (composite-PK, array type, user-defined type) all pass unchanged — proves no per-column duplication.
- [x] 1.3 Update `_tables_from_rows` in `src/sonar/connectors/postgres.py` to read the new `row_count` column from each row and pass it into the `Table(...)` constructor in both `current_key`-flush sites. Map negative values (and `None` from the SQL `NULL` case) to Python `None`; map non-negative integers through unchanged.
- [x] 1.4 Confirm no other call site mutates `Table.row_count` and no existing code path depends on it being `None`. (`grep -rn row_count` shows only read-side consumers in `index/store.py` and `mcp/tools/bundle_tools.py`.)

## 2. Tests

- [x] 2.1 Add a unit test in `tests/test_postgres_connector.py` covering the row-factory mapping: rows with `row_count` of `0`, positive integer, `-1`, and SQL `NULL` should yield `0`, `int`, `None`, `None` respectively.
- [x] 2.2 Add an integration test asserting that every returned table has `row_count is not None` after a fixture-side `ANALYZE`, and that each table's count is within an order of magnitude of its true row count. (Run against the `sonar_test` Docker fixture, which is the actually-pytest-wired DB; the originally-tasked ChEMBL fixture is for manual demos and isn't pytest-wired.)
- [x] 2.3 Add an integration test that creates a fresh table, runs `discover_tables` immediately (no `ANALYZE`), and asserts `row_count is None`.
- [x] 2.4 Add an integration test that creates an analysed empty table and asserts `row_count == 0` (the empty-vs-unknown distinction from the spec).
- [x] 2.5 Verify the discovery query does not issue `ANALYZE`/`VACUUM`. Static check on `_sql.TABLES_AND_COLUMNS` (string contains neither token, case-insensitive).

## 3. Validation and docs

- [x] 3.1 Run `poetry run pytest`. All 178 tests pass; coverage 97% (above 80%).
- [x] 3.2 Run `poetry run ruff check .` and `poetry run ruff format --check .`. Both clean.
- [x] 3.3 Update the `Implementation details` and `What goes wrong` subsections of the `postgres-connector` entry in `LEARNINGS.md` to note the `reltuples` source and the negative-as-`None` convention.
- [x] 3.4 Tick the `Populate row_count during discovery` line off the launch checklist in `~/Documents/Vault/project-data-context-os.md`.

## 4. Wrap

- [x] 4.1 Run `openspec validate row-count-discovery` and resolve any issues.
- [x] 4.2 Self-review diff before commit; confirm no row_count handling leaked outside the connector. (Verified: only `_sql.py`, `connectors/postgres.py`, tests, and docs touched. Pre-existing read-side consumers in `index/store.py` and `mcp/tools/bundle_tools.py` already handle the field; their behaviour is unchanged.)
