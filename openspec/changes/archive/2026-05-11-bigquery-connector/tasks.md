## 1. Setup

- [x] 1.1 Add `google-cloud-bigquery` as an optional dep to `pyproject.toml` and create the `bigquery` extras group (mirrors the `snowflake` and `duckdb` pattern)
- [x] 1.2 Create `src/sonar/connectors/_bigquery_sql.py` — per-dataset FK/PK INFORMATION_SCHEMA query builder function `constraints_query(project, dataset)` returning backtick-quoted SQL
- [x] 1.3 Create `src/sonar/connectors/bigquery.py` with module docstring and imports only (no implementation yet)

## 2. Type rendering and column assembly

- [x] 2.1 Implement `_render_bq_type(field: SchemaField) -> str`: flat types pass through unchanged; RECORD types render as `RECORD<field1 TYPE1, field2 TYPE2>` recursively; all REPEATED fields append ` REPEATED`
- [x] 2.2 Implement `_column_from_schema_field(field: SchemaField, pk_columns: set[str]) -> Column`: maps `field_type` via `_render_bq_type`, sets `nullable = field.mode != "REQUIRED"`, sets `is_primary_key = field.name in pk_columns`

## 3. Core connector — lifecycle and discovery

- [x] 3.1 Implement `BigQueryConnector.__init__(project_id, dataset_id=None)`: store project + optional dataset, set `_client = None`
- [x] 3.2 Implement `__aenter__` / `__aexit__`: create/close `google.cloud.bigquery.Client(project=project_id)` via `asyncio.to_thread`; raise `RuntimeError` on out-of-context method calls
- [x] 3.3 Implement `_resolve_datasets(schemas: list[str] | None) -> list[str]`: return `schemas` if provided; else `[self._dataset_id]` if set; else enumerate all datasets via `client.list_datasets()`
- [x] 3.4 Implement `_discover_table(sem, dataset_id, table_ref) -> Table`: named coroutine gated by `asyncio.Semaphore(20)`; calls `client.get_table()` via `asyncio.to_thread`; assembles `Table` from `SchemaField` list + `table.num_rows` + PK flags from constraint data
- [x] 3.5 Implement `discover_tables(schemas=None) -> list[Table]`: resolve datasets, enumerate tables per dataset via `client.list_tables()`, gather `_discover_table` coroutines with shared semaphore
- [x] 3.6 Implement `_fetch_constraints(dataset_id) -> tuple[set[str...], list[dict]]`: runs `constraints_query` via `client.query()` wrapped in `asyncio.to_thread`; returns `(pk_column_names, fk_rows)` for the dataset
- [x] 3.7 Implement `discover_relationships() -> list[ForeignKey]`: iterate target datasets, call `_fetch_constraints` per dataset, drop cross-dataset FKs, return `list[ForeignKey]`

## 4. Sampling

- [x] 4.1 Implement `_bq_quote(name: str) -> str`: wrap in backticks, escape internal backtick with `` \` ``, reject null bytes
- [x] 4.2 Implement `sample_table(schema, table, limit=5) -> list[dict]`: apply `_reject_dotted_identifier` + `_bq_quote` to schema and table, apply `_bq_quote` to the project ID as well (project IDs can contain hyphens and colons), build `SELECT * FROM \`project\`.\`dataset\`.\`table\` LIMIT N`, run via `client.query()` in `asyncio.to_thread`; convert each result `Row` to a plain dict via `dict(row)` before passing to `_serialize_row`

## 5. CLI dispatch

- [x] 5.1 Add `_BIGQUERY_INSTALL_HINT` constant and `_ensure_bigquery_driver()` function to `cli.py`
- [x] 5.2 Add `bigquery://PROJECT[/DATASET]` URL dispatch branch in `_select_connector`: strip prefix, split path to project + optional dataset, handle trailing slash as no-dataset, call `_ensure_bigquery_driver()` first
- [x] 5.3 Add bare `bigquery` keyword dispatch: read `BIGQUERY_PROJECT` (required, error if missing) + `BIGQUERY_DATASET` (optional), construct `BigQueryConnector`
- [x] 5.4 Implement `_bigquery_label(project_id, dataset_id=None) -> str`: returns `"project"` or `"project.dataset"`
- [x] 5.5 Update `_ACCEPTED_FORMS` tuple to include `bigquery://PROJECT_ID/DATASET_ID` and `bigquery` forms

## 6. Tests

- [x] 6.1 Write unit tests for `_render_bq_type`: flat STRING, flat INTEGER, RECORD with two sub-fields, deeply nested RECORD (at least 3 levels: RECORD containing a RECORD containing a RECORD), REPEATED RECORD, REPEATED scalar
- [x] 6.2 Write unit tests for `_column_from_schema_field`: NULLABLE column, REQUIRED column (not nullable), column in pk_columns set, REPEATED mode
- [x] 6.2a Write unit test for `_fetch_constraints` failure handling: monkeypatch `_fetch_constraints` to raise an exception for one dataset; assert that a warning is logged, that dataset's constraints are absent from the result, and that constraints for the remaining datasets are still returned without error
- [x] 6.3 Write unit tests for `_bq_quote`: normal identifier, identifier with backtick, null-byte rejection
- [x] 6.4 Write unit tests for CLI dispatch: `bigquery://project`, `bigquery://project/dataset`, `bigquery://project/` (trailing slash), bare `bigquery` with env vars, bare `bigquery` missing `BIGQUERY_PROJECT`
- [x] 6.5 Write unit test for missing-dep guard: monkeypatch `importlib.util.find_spec` to hide `google.cloud.bigquery`; assert CLI exits 2 with install hint in stderr
- [x] 6.6 Add `bigquery_live` marker to `pyproject.toml` `[tool.pytest.ini_options]` markers list
- [x] 6.7 Write integration test fixture in `tests/test_bigquery_connector.py`: skip unless `BIGQUERY_TEST_PROJECT` env var is set; construct `BigQueryConnector` for the test project; where a specific known table is required, target `bigquery-public-data.samples.shakespeare` which is publicly accessible and has a stable schema
- [x] 6.8 Write integration test: `discover_tables` returns at least one `Table` with correct schema and column shapes
- [x] 6.9 Write integration test: `discover_relationships` returns a `list[ForeignKey]` (may be empty) without error
- [x] 6.10 Write integration test: `sample_table` returns up to 5 serialized dicts for `bigquery-public-data.samples.shakespeare`
