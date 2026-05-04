## ADDED Requirements

### Requirement: Snowflake connector implements the data-source connector contract

The Snowflake connector SHALL provide the same observable contract as `PostgresConnector`: an async-context-manager lifecycle, a `discover_tables` method returning `Table` instances, a `discover_relationships` method returning `ForeignKey` instances, and a `sample_table` method returning a list of dict rows. The shapes returned (`Column`, `Table`, `ForeignKey`, sampled row dicts) MUST be the same dataclasses and key conventions used elsewhere in Sonar so downstream consumers (`relationship-mapping`, `description-engine`, `context-index`, `mcp-server`) operate on Snowflake output without case-by-case branching.

#### Scenario: Discovery returns shared Table shape

- **WHEN** the connector's `discover_tables` is called against a Snowflake schema
- **THEN** it returns `list[Table]` from the shared connector types module, with each Table carrying the same field set used by Postgres-derived tables

#### Scenario: Foreign-key extraction returns shared ForeignKey shape

- **WHEN** the connector's `discover_relationships` is called
- **THEN** it returns `list[ForeignKey]` from the shared connector types module, with `(source_schema, source_table, source_column)` and `(target_schema, target_table, target_column)` populated for every declared constraint Snowflake exposes through INFORMATION_SCHEMA

#### Scenario: Sample rows match Postgres serialization rules

- **WHEN** the connector's `sample_table` is called
- **THEN** it returns a list of dicts whose values are coerced through the shared row-serialization helper, so dates become ISO strings, UUIDs become strings, decimals become floats, and binary becomes the placeholder `<binary>`

### Requirement: CLI dispatches Snowflake invocations from a positional argument

The `sonar scan` and `sonar serve` commands SHALL accept Snowflake invocations through their existing positional connection argument, with no new flags. Two forms are supported: a `snowflake://...` URL for password authentication, and the bare keyword `snowflake` to indicate environment-variable-based authentication. Postgres dispatch (`postgresql://...`, `postgres://...`) is unchanged.

#### Scenario: Snowflake URL dispatches to the Snowflake connector

- **WHEN** the user runs `sonar scan snowflake://USER:PASS@ACCOUNT/DATABASE/SCHEMA?warehouse=W&role=R`
- **THEN** the CLI constructs a `SnowflakeConnector` configured for password authentication using the URL fields

#### Scenario: Bare keyword dispatches to env-var authentication

- **WHEN** the user runs `sonar scan snowflake` with `SNOWFLAKE_*` environment variables set
- **THEN** the CLI constructs a `SnowflakeConnector` whose authentication parameters are read from the environment

#### Scenario: Unrecognized argument fails with the accepted forms

- **WHEN** the user supplies a positional argument that matches none of `postgresql://...`, `postgres://...`, `snowflake://...`, or the bare keyword `snowflake`
- **THEN** the CLI exits non-zero with an error message naming all accepted forms

### Requirement: Snowflake driver is an optional dependency with dispatch-time guard

The `snowflake-connector-python` package SHALL be an optional installation extra (not pulled in by a default `pip install sonar`). When the extra is not installed, attempts to dispatch a Snowflake invocation MUST fail at the CLI dispatch point — before any credentials are read or connection is attempted — with an actionable error pointing to the install command for the extra.

#### Scenario: Snowflake URL dispatch with missing extra fails fast

- **WHEN** the user runs `sonar scan snowflake://...` and `snowflake-connector-python` is not installed
- **THEN** the CLI exits non-zero before reading credentials or opening any connection, with an error message that includes the install command for the Snowflake extra

#### Scenario: Bare keyword dispatch with missing extra fails fast

- **WHEN** the user runs `sonar scan snowflake` and `snowflake-connector-python` is not installed
- **THEN** the CLI exits non-zero before reading environment variables or opening any connection, with the same actionable install message

### Requirement: Snowflake authentication accepts URL-supplied password and a curated env-var set

When dispatched via the `snowflake://...` URL, the connector SHALL authenticate using the username and password embedded in the URL and pull database, schema, warehouse, and role from URL fields and query parameters. When dispatched via the bare `snowflake` keyword, the connector SHALL read configuration from a curated set of `SNOWFLAKE_*` environment variables — enumerated in `design.md` (D3) — and forward them to the driver, supporting password, key-pair, OAuth, and externalbrowser authentication mechanisms. Environment variables outside the curated set MUST be silently ignored, so that future driver-side parameter renames do not silently change Sonar's user-facing contract.

#### Scenario: URL form authenticates with password

- **WHEN** the connector is constructed from a `snowflake://USER:PASS@ACCOUNT/DB/SCHEMA` URL
- **THEN** it connects using `user=USER`, `password=PASS`, `account=ACCOUNT`, `database=DB`, `schema=SCHEMA`, plus any `warehouse` and `role` query parameters

#### Scenario: Keyword form authenticates from curated environment variables

- **WHEN** the connector is constructed from the bare `snowflake` keyword and the environment provides the required variables for any supported authentication mechanism (e.g. `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_PRIVATE_KEY_PATH`, `SNOWFLAKE_AUTHENTICATOR=externalbrowser`, or `SNOWFLAKE_TOKEN`) plus `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, and `SNOWFLAKE_DATABASE`
- **THEN** the connector forwards those values to the driver under their corresponding `connect()` keyword arguments

#### Scenario: Variables outside the curated set are ignored

- **WHEN** the environment includes a `SNOWFLAKE_*` variable that is not part of the curated set defined in `design.md` (D3)
- **THEN** the connector silently ignores it; the driver is not invoked with any kwarg derived from it

#### Scenario: Missing required env vars surface a configuration error

- **WHEN** the connector is constructed from the bare `snowflake` keyword and required variables (account, user, database, or any authentication-mechanism variable) are absent
- **THEN** the connector raises a configuration error naming the missing variables, before attempting a connection

### Requirement: Snowflake identifiers use 2-level (schema, table) shape

The connector SHALL bind the Snowflake database at connection time and emit `Table` and `ForeignKey` records with `(schema, table)` keys only. The database is connector configuration, not table shape. This keeps the bundle, context-index, and MCP tool surface byte-identical to the Postgres path.

#### Scenario: Tables emit 2-level keys

- **WHEN** the connector discovers tables in a Snowflake database
- **THEN** each Table's `schema` field holds the Snowflake schema name and `name` holds the table name; no field encodes the database

#### Scenario: ForeignKeys emit 2-level endpoints

- **WHEN** the connector extracts foreign keys
- **THEN** each ForeignKey's source and target endpoints are `(schema, table, column)` tuples within the connector's bound database

#### Scenario: Cross-database FK is dropped and surfaced in scan summary

- **WHEN** Snowflake INFORMATION_SCHEMA exposes one or more foreign keys whose targets live in a database other than the connector's bound database
- **THEN** the connector skips those FKs (no partial or cross-database ForeignKey record is emitted), AND the `sonar scan` summary report includes a one-line note stating the count of excluded foreign keys and the bound database name, so the user is informed without having to inspect logs

### Requirement: Connector dataclasses live in a shared module

`Column`, `Table`, and `ForeignKey` SHALL be defined in a connector-agnostic module that both the Postgres and Snowflake connectors import. The row-coercion helper used by the MCP sample tool SHALL also live in a connector-agnostic module. This eliminates the existing private cross-import (`from sonar.connectors.postgres import _coerce_value`) and any implicit assumption that one connector "owns" the type definitions.

#### Scenario: Both connectors import shared types

- **WHEN** either `PostgresConnector` or `SnowflakeConnector` returns a `Table`
- **THEN** the dataclass is the one defined in the shared types module, not a connector-private redefinition

#### Scenario: Sample tool imports coercion from the shared module

- **WHEN** the MCP sample tool serializes a sampled row
- **THEN** it imports the row-coercion helper from the shared module, not from a connector-specific module
