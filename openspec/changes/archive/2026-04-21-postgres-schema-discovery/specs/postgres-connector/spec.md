## ADDED Requirements

### Requirement: Connection Lifecycle

The connector SHALL manage its Postgres connection as an async context manager. The connection MUST open on enter and close on exit. Public methods MUST fail fast if invoked without an active connection.

#### Scenario: Connection opens and closes with the context manager

- **WHEN** a `PostgresConnector` is entered via `async with`
- **THEN** a Postgres connection is established before the block begins
- **AND** the connection is closed when the block exits, regardless of whether the block raised

#### Scenario: Methods called outside the context manager raise

- **WHEN** a public method is called on a `PostgresConnector` that has not been entered
- **THEN** a `RuntimeError` is raised with a message naming the async-context-manager requirement
- **AND** no database connection is attempted

### Requirement: Schema Introspection

The connector SHALL enumerate all base tables in user-controlled schemas and return each table with its full column list. For every column, the connector MUST expose name, data type, nullability, default expression, and primary-key membership. System schemas MUST be excluded unless explicitly requested.

#### Scenario: All user tables are discovered

- **WHEN** `discover_tables()` is called without a schemas filter against a database containing seven user tables across the `public` schema
- **THEN** the result contains exactly seven tables, each with `schema="public"`
- **AND** each table's columns list matches the columns defined in the database, in ordinal position order

#### Scenario: System schemas are excluded by default

- **WHEN** `discover_tables()` is called without a schemas filter
- **THEN** no table is returned with a schema name of `pg_catalog`, `information_schema`, or any name starting with `pg_`

#### Scenario: Schemas filter scopes the result

- **WHEN** `discover_tables(schemas=["public"])` is called against a database with tables in `public` and `other`
- **THEN** only tables from `public` are returned
- **AND** calling with a non-existent schema name returns an empty list

#### Scenario: Primary-key membership is flagged, including composite keys

- **WHEN** `discover_tables()` is called against a database containing a table with a single-column primary key and a table with a composite two-column primary key
- **THEN** the single-column primary key's column is returned with `is_primary_key=True`
- **AND** both columns of the composite primary key are returned with `is_primary_key=True`
- **AND** no non-key column is flagged as primary key

### Requirement: Data Type Representation

Column data types SHALL be returned as Postgres type identifiers suitable for inclusion in LLM prompts. When the SQL-standard `data_type` value is `ARRAY` or `USER-DEFINED`, the connector MUST substitute the underlying `udt_name` so the LLM sees the concrete type.

#### Scenario: Standard types pass through

- **WHEN** a column has SQL type `text`, `timestamp with time zone`, `numeric`, or `uuid`
- **THEN** the `Column.data_type` value is that Postgres type identifier

#### Scenario: Array types surface the element type

- **WHEN** a column is declared as `integer[]`
- **THEN** the `Column.data_type` value is `_int4` (the `udt_name`), not the literal string `ARRAY`

#### Scenario: User-defined types surface the type name

- **WHEN** a column is declared with an enum or domain type named `order_status`
- **THEN** the `Column.data_type` value is `order_status`, not the literal string `USER-DEFINED`

### Requirement: Foreign Key Extraction

The connector SHALL enumerate every foreign-key constraint in the connected database as a list of per-column-pair entries. Each entry MUST carry the source schema, table, and column and the target schema, table, and column. Composite foreign keys MUST produce one entry per column pair with correct positional alignment.

#### Scenario: Simple foreign keys are returned with correct endpoints

- **WHEN** `discover_relationships()` is called against a database containing a foreign key from `orders.user_id` referencing `users.user_id`
- **THEN** the result contains an entry with `source_schema="public"`, `source_table="orders"`, `source_column="user_id"`, `target_schema="public"`, `target_table="users"`, `target_column="user_id"`

#### Scenario: Composite foreign keys align source and target columns

- **WHEN** `discover_relationships()` is called against a database containing a two-column foreign key where `(a, b)` in one table references `(a, b)` in another
- **THEN** the result contains two entries for that constraint
- **AND** the entry for source column `a` points to target column `a`
- **AND** the entry for source column `b` points to target column `b`

### Requirement: Row Sampling

The connector SHALL return up to N rows from a named table as a list of column-name-keyed dictionaries. The default N MUST be 5. Returned values MUST be JSON-serialisable: UUIDs as strings, datetimes and dates as ISO 8601 strings, `Decimal` as `float`, `bytes` as a short sentinel. Identifier quoting MUST use parameterised SQL identifiers so that reserved words, mixed case, and special characters in schema or table names do not break the query.

#### Scenario: Default limit returns at most five rows

- **WHEN** `sample_table(schema, table)` is called against a table containing more than five rows, with no `limit` argument
- **THEN** exactly five rows are returned

#### Scenario: Caller-specified limit is respected

- **WHEN** `sample_table(schema, table, limit=3)` is called against a table containing at least three rows
- **THEN** exactly three rows are returned

#### Scenario: Row shape matches the column set

- **WHEN** `sample_table(schema, table)` is called against a table with columns `(a, b, c)`
- **THEN** every returned row is a dict with keys `{"a", "b", "c"}`

#### Scenario: Sampled values are JSON-serialisable

- **WHEN** `sample_table(schema, table)` returns rows containing UUID, timestamp-with-time-zone, and numeric columns
- **THEN** the UUID value is a string
- **AND** the timestamp value is an ISO 8601 string
- **AND** the numeric value is a float
- **AND** `json.dumps(row)` succeeds for every returned row

#### Scenario: Identifier quoting handles unusual names

- **WHEN** `sample_table` is called with a schema or table name that is a reserved word or contains mixed case
- **THEN** the query executes successfully against a correctly-named table in the database
- **AND** no SQL syntax error is raised due to unquoted identifiers
