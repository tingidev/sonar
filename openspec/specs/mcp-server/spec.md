# mcp-server Specification

## Purpose
TBD - created by archiving change mcp-server. Update Purpose after archive.
## Requirements
### Requirement: Serve subcommand starts an MCP server over a Sonar bundle

The `sonar serve` CLI command SHALL start a FastMCP server that exposes a loaded `ContextBundle` as MCP tools over stdio. The command accepts a `--bundle-dir` option (default `.sonar/`) and an optional positional DSN. It SHALL load the bundle from disk once at startup before the MCP transport is opened.

#### Scenario: Bundle-only mode starts with four tools

- **WHEN** an operator runs `sonar serve --bundle-dir .sonar/`
- **THEN** the server loads the bundle, registers the `discover`, `describe`, `relationships`, and `search` tools, and begins serving over stdio
- **AND** no connection to any live database is attempted

#### Scenario: Live mode registers the sample tool

- **WHEN** an operator runs `sonar serve --bundle-dir .sonar/ <dsn>`
- **THEN** the server registers the four bundle-backed tools plus the `sample` tool
- **AND** the DSN is retained in server process state and is never transmitted through any tool response or error message

#### Scenario: Missing bundle aborts startup

- **WHEN** `--bundle-dir` points at a directory that does not exist or does not contain a valid bundle
- **THEN** the server prints a clear error to stderr, exits non-zero, and never opens the MCP transport

#### Scenario: Corrupt or version-mismatched bundle aborts startup

- **WHEN** the bundle loader raises `BundleIntegrityError` or `BundleVersionError` during startup
- **THEN** the server prints the error to stderr, exits non-zero, and never opens the MCP transport

### Requirement: Discover tool lists tables in the bundle

The `discover` MCP tool SHALL return the set of tables present in the bundle, optionally filtered to a single schema. Each entry SHALL include the table's schema, name, and row count when known. The tool SHALL NOT open a database connection.

#### Scenario: Unfiltered discovery returns every table

- **WHEN** an agent calls `discover` with no arguments
- **THEN** the response contains every table in the bundle, each with its schema, name, and row count (or null if the bundle did not record one)

#### Scenario: Schema-filtered discovery

- **WHEN** an agent calls `discover` with a `schema` argument that matches at least one table
- **THEN** the response contains only tables whose schema equals the filter
- **AND** the response is an empty list if no table matches, not an error

### Requirement: Describe tool returns the joined semantic view of one table

The `describe` MCP tool SHALL return the full semantic description of a single table, composed at call time by joining the bundle's `tables` and `descriptions` collections. Addressing is by `schema` plus `table` arguments. The tool SHALL NOT open a database connection.

#### Scenario: Successful describe

- **WHEN** an agent calls `describe` with a `(schema, table)` pair present in the bundle
- **THEN** the response contains the table's columns (name, data type, nullability, primary-key flag, foreign-key target where known) and the LLM-generated description (table description, grain, domain hints, per-column semantic type, PII risk, confidence)

#### Scenario: Table with null description

- **WHEN** the bundle contains the table but the description slot is null (LLM previously failed)
- **THEN** the response returns the raw column shape with the description fields explicitly null, not omitted and not an error

#### Scenario: Unknown table

- **WHEN** the requested `(schema, table)` pair is not present in the bundle
- **THEN** the tool call returns a tool-level error distinguishable from "found but empty"

### Requirement: Relationships tool returns edges touching a table

The `relationships` MCP tool SHALL return the subset of bundle relationships incident on a given `(schema, table)` pair. It SHALL accept a `direction` argument taking values `outgoing`, `incoming`, or `both` (default `both`). Each edge SHALL be returned with its source, target, and relationship kind.

#### Scenario: Outgoing edges

- **WHEN** an agent calls `relationships(schema, table, direction="outgoing")`
- **THEN** the response contains only relationships whose source is the given table

#### Scenario: Both directions

- **WHEN** an agent calls `relationships(schema, table)` with no direction argument
- **THEN** the response contains edges in both directions

#### Scenario: Table with no relationships

- **WHEN** the target table has no incident edges
- **THEN** the response is an empty list, not an error

### Requirement: Search tool performs in-memory substring match over the bundle

The `search` MCP tool SHALL return ranked matches across table names, table descriptions, column names, and column descriptions by substring comparison. It SHALL accept a `query` argument and a `limit` argument (default 20). Matching SHALL be case-insensitive. The tool SHALL NOT open a database connection.

#### Scenario: Table-name match

- **WHEN** the query substring appears in a table's name (case-insensitive)
- **THEN** the response includes that table ranked as a table-name match

#### Scenario: Description-body match

- **WHEN** the query substring appears only in a table or column description
- **THEN** the response includes that entry ranked below table-name matches

#### Scenario: Limit enforcement

- **WHEN** the caller passes `limit=N`
- **THEN** the response contains at most N matches

### Requirement: Sample tool is registered only when a DSN is provided to serve

The `sample` MCP tool SHALL be registered and exposed to the MCP client if and only if `sonar serve` was invoked with a DSN. In bundle-only mode the tool MUST NOT appear in the tool list returned to the client.

#### Scenario: Tool absent in bundle-only mode

- **WHEN** `sonar serve` is started without a DSN
- **AND** an MCP client requests the tool list
- **THEN** the response does not contain `sample`

#### Scenario: Tool present in live mode

- **WHEN** `sonar serve` is started with a DSN
- **AND** an MCP client requests the tool list
- **THEN** the response contains `sample` alongside the four bundle-backed tools

### Requirement: Sample tool composes SQL using safe identifier quoting

The `sample` tool SHALL compose the `SELECT ... FROM <schema>.<table>` statement using `psycopg.sql.Identifier` for both the `schema` and `table` arguments. Literal string interpolation of identifier arguments into SQL is prohibited.

#### Scenario: Identifier containing a quote character

- **WHEN** an agent calls `sample` with an argument that would be a SQL-injection payload under string concatenation
- **THEN** the argument is safely quoted by `psycopg.sql.Identifier` and either resolves to a legitimate (existing or non-existing) identifier or raises a database error — never executing as SQL

### Requirement: Sample tool caps and default row limits

The `sample` tool SHALL enforce a server-side maximum row count. A call requesting more rows than the cap SHALL be rejected with an error, not silently clamped. When the caller does not pass a `limit` argument, the tool SHALL return a small default number of rows suitable for shape recognition rather than data exfiltration. The concrete default and cap values are documented in `design.md`.

#### Scenario: Request within cap

- **WHEN** an agent calls `sample(schema, table, limit=10)` and the cap is 20
- **THEN** the tool returns up to 10 rows

#### Scenario: Request above cap is rejected

- **WHEN** an agent calls `sample(schema, table, limit=1000)` and the cap is 20
- **THEN** the tool returns an error stating the cap; no query is executed with limit 1000

#### Scenario: No limit argument

- **WHEN** an agent calls `sample(schema, table)` with no limit
- **THEN** the tool returns the documented default number of rows

### Requirement: Sample tool strips PII-flagged columns unless operator opts in

The `sample` tool SHALL, by default, replace values in columns whose `pii_risk` classification in the bundle's description is above a documented threshold with JSON `null`, returning the same row shape but with PII fields redacted. When `sonar serve` is started with the `--allow-pii` flag, sample results SHALL pass through unmodified. The concrete threshold is documented in `design.md`.

#### Scenario: Default mode strips flagged columns

- **WHEN** `sonar serve` is started without `--allow-pii`
- **AND** a table has a column flagged as above-threshold PII risk in the bundle
- **THEN** values in that column are returned as JSON `null` in `sample` responses

#### Scenario: Allow-pii mode returns raw values

- **WHEN** `sonar serve` is started with `--allow-pii`
- **THEN** `sample` returns raw column values for all columns regardless of PII classification

#### Scenario: Column without classification

- **WHEN** a table has a column for which the bundle has no description entry (null description slot)
- **THEN** the tool's behaviour SHALL be documented and deterministic; values pass through by default

### Requirement: Sample tool emits a structured audit log record per invocation

Every successful or rejected `sample` invocation SHALL emit a structured log record to a dedicated audit logger distinct from the generic server logger. The record SHALL include the tool name, target schema and table, the limit requested and effective limit applied, and the number of rows returned. The record SHALL NOT include column values.

#### Scenario: Successful call is audited

- **WHEN** `sample` returns rows
- **THEN** a record is emitted to `sonar.mcp.audit` with the schema, table, requested limit, effective limit, and row count
- **AND** no row values appear in the record

#### Scenario: Rejected call is audited

- **WHEN** `sample` rejects a call for exceeding the row cap
- **THEN** a record is emitted to `sonar.mcp.audit` noting the rejection and the requested limit
- **AND** no connection to the database is opened

### Requirement: DSN credentials are scrubbed from all agent-visible error paths

Any exception surfaced from `sample` (or any future live-backed tool) back to the MCP client SHALL have the raw DSN replaced with the bundle-label form before transmission. The same scrubbing helper SHALL be used in the `sonar scan` CLI error path so that both boundaries share a single implementation.

#### Scenario: Database connection error

- **WHEN** the live DB connection fails during a `sample` call and the underlying exception embeds the full DSN
- **THEN** the error returned to the MCP client contains no password, username, or raw host-port string from the DSN
- **AND** it contains the bundle's database label for operator diagnostics
