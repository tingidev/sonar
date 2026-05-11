## ADDED Requirements

### Requirement: BigQuery connector implements the data-source connector contract

The system SHALL provide `BigQueryConnector` implementing the same observable contract as `PostgresConnector`, `SnowflakeConnector`, and `DuckDBConnector`: an async context manager lifecycle, a `discover_tables` method returning `list[Table]`, a `discover_relationships` method returning `list[ForeignKey]`, and a `sample_table` method returning `list[dict]`. Returned shapes SHALL use the shared `Column`, `Table`, and `ForeignKey` dataclasses so downstream consumers (`relationship-mapping`, `description-engine`, `context-index`, `mcp-server`) operate on BigQuery output without branching.

#### Scenario: Discovery returns shared Table shape

- **WHEN** `BigQueryConnector.discover_tables` is called against a BigQuery project
- **THEN** it returns `list[Table]` from the shared connector types module, with each Table carrying `schema` (dataset name), `name`, `columns`, and `row_count` fields

#### Scenario: Foreign-key extraction returns shared ForeignKey shape

- **WHEN** `BigQueryConnector.discover_relationships` is called
- **THEN** it returns `list[ForeignKey]` from the shared connector types module, populated for every FK constraint discoverable via per-dataset INFORMATION_SCHEMA

#### Scenario: Sample rows are serialized through the shared coercion helper

- **WHEN** `BigQueryConnector.sample_table` is called
- **THEN** it returns a list of dicts whose values are coerced through the shared row-serialization helper (dates → ISO strings, decimals → floats, binary → `<binary>` placeholder)

### Requirement: BigQuery connector uses Application Default Credentials and the async context manager lifecycle

The connector SHALL connect to a GCP project using Application Default Credentials (ADC) as resolved by the `google-cloud-bigquery` client. No credentials SHALL be embedded in the DSN or passed as connector arguments — authentication is delegated entirely to the ADC chain. The connector MUST be used as an async context manager; calling discovery or sampling methods outside the context manager SHALL raise `RuntimeError`.

#### Scenario: Connector opens to a GCP project

- **WHEN** `BigQueryConnector(project_id)` is used as an async context manager
- **THEN** it initialises a `google.cloud.bigquery.Client` for the specified project using ADC

#### Scenario: Methods called outside context manager raise RuntimeError

- **WHEN** any of `discover_tables`, `discover_relationships`, or `sample_table` is called without entering the async context manager first
- **THEN** a `RuntimeError` is raised with a message indicating the context manager requirement

### Requirement: Dataset enumeration discovers all datasets or a specified subset

When `discover_tables` is called with no schema filter, the connector SHALL enumerate all datasets in the project using `client.list_datasets()`. When called with an explicit dataset list, only those datasets SHALL be queried. The connector SHALL use the `dataset_id` scoping parameter from the DSN or env-var form when provided, treating it as an implicit schema filter.

#### Scenario: All datasets discovered without filter

- **WHEN** `BigQueryConnector("project").discover_tables()` is called with no schema argument
- **THEN** tables from all datasets in the project are returned

#### Scenario: Single dataset scoped via constructor

- **WHEN** `BigQueryConnector("project", dataset_id="my_dataset")` is used and `discover_tables` is called with no schema argument
- **THEN** only tables from `my_dataset` are returned

#### Scenario: Explicit schema filter overrides constructor scoping

- **WHEN** `discover_tables(schemas=["ds1", "ds2"])` is called
- **THEN** only tables from `ds1` and `ds2` are returned, regardless of the constructor's `dataset_id`

### Requirement: Nested RECORD and REPEATED fields are rendered in the column type string

The connector SHALL enumerate only top-level columns for each BigQuery table. For columns of type `RECORD` (struct), the nested sub-schema SHALL be rendered inline as `RECORD<field1 TYPE1, field2 TYPE2>`, recursively for deeply nested records. For columns with mode `REPEATED`, the type string SHALL include the `REPEATED` suffix. No dot-notation column names SHALL be produced; `_reject_dotted_identifier` invariants are unaffected.

#### Scenario: Flat column passes through unchanged

- **WHEN** a table contains a `STRING` column named `email`
- **THEN** `Column(name="email", data_type="STRING")` is returned

#### Scenario: RECORD column renders sub-schema inline

- **WHEN** a table contains a `RECORD` column named `address` with sub-fields `city STRING` and `zip STRING`
- **THEN** `Column(name="address", data_type="RECORD<city STRING, zip STRING>")` is returned

#### Scenario: REPEATED RECORD column includes REPEATED suffix

- **WHEN** a table contains a REPEATED RECORD column named `items` with sub-fields `sku STRING` and `qty INTEGER`
- **THEN** `Column(name="items", data_type="RECORD<sku STRING, qty INTEGER> REPEATED")` is returned

#### Scenario: REPEATED scalar column includes REPEATED suffix

- **WHEN** a table contains a REPEATED `STRING` column named `tags`
- **THEN** `Column(name="tags", data_type="STRING REPEATED")` is returned

### Requirement: Row counts sourced from BigQuery table metadata

The connector SHALL populate `Table.row_count` from `table.num_rows` as returned by `client.get_table()`. When `num_rows` is unavailable (e.g. external or streaming tables), `row_count` SHALL be `None`.

#### Scenario: Row count populated from num_rows

- **WHEN** `client.get_table()` returns a table with `num_rows` set
- **THEN** the corresponding `Table.row_count` is set to that integer value

#### Scenario: Missing num_rows yields None

- **WHEN** `client.get_table()` returns a table where `num_rows` is `None`
- **THEN** `Table.row_count` is `None`

### Requirement: FK and PK constraints discovered via per-dataset INFORMATION_SCHEMA

The connector SHALL attempt to discover FK and PK constraints for each target dataset by querying `project.dataset.INFORMATION_SCHEMA.TABLE_CONSTRAINTS` and `project.dataset.INFORMATION_SCHEMA.KEY_COLUMN_USAGE`. When no constraints are defined (the common case for BigQuery), an empty list is returned without error. Cross-dataset FK references SHALL be dropped. PK constraint information SHALL be used to set `Column.is_primary_key`.

#### Scenario: FK discovered within a dataset

- **WHEN** a dataset has a declared (non-enforced) FK constraint from `orders.user_id` to `users.id`
- **THEN** `discover_relationships` returns a `ForeignKey` with the correct source and target fields

#### Scenario: PK constraint flags column as primary key

- **WHEN** a dataset has a declared PK constraint on `users.id`
- **THEN** `discover_tables` returns a `Column(name="id", is_primary_key=True)` for that table

#### Scenario: Empty list returned when no constraints defined

- **WHEN** a dataset has no FK or PK constraints declared
- **THEN** `discover_relationships` returns an empty list and all `Column.is_primary_key` values are `False`

#### Scenario: INFORMATION_SCHEMA query raises an exception for one dataset

- **WHEN** `_fetch_constraints` raises an exception for one dataset (e.g. permission denied or query error) during either `discover_tables` or `discover_relationships`
- **THEN** a warning is logged, that dataset's constraints are skipped, and discovery continues for the remaining datasets without raising an error. For `discover_tables`, columns from the failing dataset are still returned with `is_primary_key=False`; for `discover_relationships`, only that dataset's FKs are dropped.

#### Scenario: Cross-dataset FK is dropped

- **WHEN** INFORMATION_SCHEMA exposes a FK whose target table lives in a different dataset
- **THEN** the FK is excluded from `discover_relationships` results and no error is raised

### Requirement: Scan summary surfaces the count of dropped cross-dataset foreign keys

The scan summary produced after `discover_relationships` completes SHALL include a `cross_dataset_foreign_keys_dropped` counter reporting the number of FK constraints that were excluded because their target table resided in a different dataset. The counter SHALL be `0` when no cross-dataset FKs were encountered. This mirrors the `cross_database_foreign_keys_dropped` counter used by the Snowflake connector.

#### Scenario: Cross-dataset FK drop count reported in scan summary

- **WHEN** `discover_relationships` drops one or more FKs because their target tables are in a different dataset
- **THEN** the scan summary includes `cross_dataset_foreign_keys_dropped` set to the number of dropped FKs

#### Scenario: No cross-dataset FKs yields zero count

- **WHEN** no cross-dataset FKs are encountered during `discover_relationships`
- **THEN** the scan summary includes `cross_dataset_foreign_keys_dropped` set to `0`

### Requirement: `discover_relationships` uses the same dataset scope as `discover_tables`

When `discover_relationships` is called, the set of datasets it queries for constraints SHALL be determined by the same `_resolve_datasets` path used by `discover_tables`: if no `dataset_id` was set on the connector, all project datasets are enumerated via `client.list_datasets()`; if `dataset_id` was set, only that dataset is queried. `discover_relationships` takes no schema filter argument and uses no separate dataset resolution logic. This ensures that relationship discovery is always consistent with table discovery for the same connector instance.

#### Scenario: `discover_relationships` enumerates all datasets when no `dataset_id` is set

- **WHEN** `BigQueryConnector("project")` is constructed with no `dataset_id` and `discover_relationships` is called
- **THEN** constraints are queried across all datasets in the project, using the same `_resolve_datasets` enumeration path as `discover_tables`

#### Scenario: `discover_relationships` is scoped to constructor `dataset_id`

- **WHEN** `BigQueryConnector("project", dataset_id="my_dataset")` is constructed and `discover_relationships` is called
- **THEN** only constraints from `my_dataset` are queried, consistent with the dataset scoping applied by `discover_tables`

### Requirement: CLI dispatches BigQuery invocations via `bigquery://` prefix and bare `bigquery` keyword

The `sonar scan` and `sonar serve` commands SHALL accept BigQuery invocations via two forms. The URL form `bigquery://PROJECT_ID[/DATASET_ID]` specifies the project and optionally scopes to a single dataset; a trailing slash with no dataset name is treated as all-datasets. The bare keyword `bigquery` reads the project from the required `BIGQUERY_PROJECT` env var and, if present, the dataset from the optional `BIGQUERY_DATASET` env var. Explicit URL form takes precedence; bare keyword is the env-var fallback. Existing Postgres, Snowflake, and DuckDB dispatch are unchanged.

#### Scenario: URL form with project only discovers all datasets

- **WHEN** the user runs `sonar scan bigquery://my-project`
- **THEN** the CLI constructs a `BigQueryConnector("my-project")` with no dataset scoping

#### Scenario: URL form with dataset scopes discovery

- **WHEN** the user runs `sonar scan bigquery://my-project/my_dataset`
- **THEN** the CLI constructs a `BigQueryConnector("my-project", dataset_id="my_dataset")`

#### Scenario: URL form with trailing slash treated as all-datasets

- **WHEN** the user runs `sonar scan bigquery://my-project/`
- **THEN** the CLI constructs a `BigQueryConnector("my-project")` with no dataset scoping

#### Scenario: Bare keyword with project and dataset env vars

- **WHEN** the user runs `sonar scan bigquery` with `BIGQUERY_PROJECT=my-project` and `BIGQUERY_DATASET=my_dataset` set
- **THEN** the CLI constructs `BigQueryConnector("my-project", dataset_id="my_dataset")`

#### Scenario: Bare keyword without BIGQUERY_PROJECT fails with clear error

- **WHEN** the user runs `sonar scan bigquery` and `BIGQUERY_PROJECT` is not set
- **THEN** the CLI exits non-zero with an error message naming the missing env var

### Requirement: BigQuery driver is an optional dependency with dispatch-time guard

The `google-cloud-bigquery` package SHALL be an optional installation extra. When the extra is not installed, dispatching a `bigquery://` or bare `bigquery` invocation MUST fail at the CLI dispatch point with an actionable error message pointing to the install command for the extra, before any project resolution or API call is attempted.

#### Scenario: BigQuery dispatch with missing extra fails fast

- **WHEN** the user runs `sonar scan bigquery://my-project` and `google-cloud-bigquery` is not installed
- **THEN** the CLI exits non-zero before any API call, with an error message that includes the install command for the BigQuery extra
