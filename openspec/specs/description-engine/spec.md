# description-engine Specification

## Purpose
TBD - created by archiving change llm-description-engine. Update Purpose after archive.
## Requirements
### Requirement: Structured output dataclasses

The system SHALL expose two frozen dataclasses that are the sole return shape for description generation:

- `ColumnDescription` with fields `name: str`, `description: str`, `semantic_type: SemanticType`, `pii_risk: PIIRisk`, `confidence: float`.
- `TableDescription` with fields `schema: str`, `name: str`, `description: str`, `grain: str`, `domain_hints: tuple[str, ...]`, `columns: tuple[ColumnDescription, ...]`, `confidence: float`.

`SemanticType` SHALL be a `StrEnum` with members `IDENTIFIER`, `DIMENSION`, `MEASURE`, `OTHER`. `PIIRisk` SHALL be a `StrEnum` with members `NONE`, `LOW`, `MEDIUM`, `HIGH`. Both enums SHALL use lowercase string values so instances round-trip through JSON without custom encoders. The `MEDIUM` bucket SHALL represent the classifier's "plausible PII" middle confidence band — downstream consumers (e.g. `mcp-server`'s `sample` tool) may treat `MEDIUM` as protected alongside `HIGH` by default.

#### Scenario: TableDescription is immutable

- **WHEN** a `TableDescription` instance is constructed
- **THEN** any attempt to assign to one of its fields SHALL raise `dataclasses.FrozenInstanceError`
- **AND** the `columns` field SHALL be a `tuple`, not a `list`
- **AND** the `domain_hints` field SHALL be a `tuple`, not a `list`

#### Scenario: SemanticType round-trips through JSON

- **WHEN** a `TableDescription` is serialised with `json.dumps(..., default=str)` and deserialised with `json.loads`
- **THEN** the `semantic_type` values SHALL appear as lowercase strings matching the enum value
- **AND** passing such a string to `SemanticType(value)` SHALL reconstruct the enum member

#### Scenario: PIIRisk enum includes medium

- **WHEN** `PIIRisk("medium")` is evaluated
- **THEN** it SHALL return `PIIRisk.MEDIUM` with value `"medium"`
- **AND** a persisted `ColumnDescription` with `pii_risk=MEDIUM` SHALL round-trip through `json.dumps`/`json.loads` and `PIIRisk(value)` without loss

### Requirement: Describe a single table

The system SHALL expose `DescriptionEngine.describe_table(table: Table, samples: list[dict]) -> TableDescription`. The engine SHALL compose a prompt from the `Table` metadata and `samples`, send it via the injected `LLMClient`, parse the JSON response, and return a fully-populated `TableDescription`.

#### Scenario: Successful description with well-formed LLM output

- **WHEN** `describe_table(users_table, users_samples)` is awaited with a mocked `LLMClient` that returns valid JSON
- **THEN** the engine SHALL return a `TableDescription` whose `schema` and `name` match the input `Table`
- **AND** whose `columns` tuple has one `ColumnDescription` per input column, in input order
- **AND** whose `confidence` is a float in `[0.0, 1.0]`

#### Scenario: Prompt composition includes column list and sample rows

- **WHEN** `describe_table` is called with a table having columns `(user_id uuid PK, email text, created_at timestamptz)` and two sample rows
- **THEN** the prompt passed to `LLMClient.generate` SHALL include the schema-qualified table name
- **AND** SHALL include each column's name, data_type, nullability, and PK flag
- **AND** SHALL include the sample rows serialised as JSON
- **AND** SHALL describe the expected output JSON shape including the `SemanticType` and `PIIRisk` enum values (including the `medium` bucket)

#### Scenario: Prompt handles abbreviated column names

- **WHEN** `describe_table` is called with a table whose columns use abbreviations (e.g., `ae_pt`, `role_cod`, `BENE_ESRD_IND`)
- **THEN** the prompt SHALL instruct the LLM to infer the likely expanded meaning of abbreviated column names
- **AND** the returned description SHALL include the inferred meaning, not just repeat the abbreviation

#### Scenario: Prompt handles domain-specific schemas

- **WHEN** `describe_table` is called with a table from a healthcare, retail, sports statistics, or enterprise domain
- **THEN** the prompt SHALL instruct the LLM to identify the domain from contextual clues (table name, column patterns, sample values)
- **AND** the returned description SHALL use domain-appropriate terminology

#### Scenario: PII columns are classified by the LLM

- **WHEN** `describe_table` is called with a table containing an `email` column and an `ssn` column and the mocked LLM returns corresponding `pii_risk` values of `"high"`
- **THEN** the returned `ColumnDescription.pii_risk` for those columns SHALL be `PIIRisk.HIGH`
- **AND** the engine SHALL NOT override the LLM's classification with its own rules

#### Scenario: Medium-risk column is classified by the LLM

- **WHEN** `describe_table` is called with a table containing a `city` column and the mocked LLM returns `pii_risk="medium"`
- **THEN** the returned `ColumnDescription.pii_risk` for that column SHALL be `PIIRisk.MEDIUM`
- **AND** the engine SHALL NOT override the LLM's classification with its own rules

### Requirement: Parse retry on malformed JSON

The system SHALL attempt to parse the LLM response as a single JSON object. If parsing fails, the engine SHALL re-invoke `LLMClient.generate` exactly once with a reminder appended to the prompt instructing the model to return only valid JSON. If the second attempt also fails to parse, the engine SHALL raise `DescriptionParseError` carrying the offending response text truncated to 500 characters.

#### Scenario: One retry recovers from transient malformed JSON

- **WHEN** the mocked `LLMClient.generate` returns malformed JSON on the first call and valid JSON on the second
- **THEN** `describe_table` SHALL return a valid `TableDescription`
- **AND** `LLMClient.generate` SHALL have been called exactly twice
- **AND** the second call SHALL include a reminder asking for valid JSON

#### Scenario: Permanent parse failure raises DescriptionParseError

- **WHEN** the mocked `LLMClient.generate` returns malformed JSON on both calls
- **THEN** `describe_table` SHALL raise `DescriptionParseError`
- **AND** the exception SHALL carry the offending text truncated to at most 500 characters on an attribute named `raw_text`
- **AND** `LLMClient.generate` SHALL have been called exactly twice

#### Scenario: Valid first response does not trigger a retry

- **WHEN** the mocked `LLMClient.generate` returns valid JSON on the first call
- **THEN** `describe_table` SHALL return the parsed `TableDescription`
- **AND** `LLMClient.generate` SHALL have been called exactly once

### Requirement: Describe all tables concurrently with bounded fan-out

The system SHALL expose `DescriptionEngine.describe_database(tables: list[Table], samples_per_table: dict[tuple[str, str], list[dict]]) -> dict[tuple[str, str], TableDescription | None]`. The engine SHALL invoke `describe_table` concurrently for each table, bounded by an `asyncio.Semaphore` sized to `LLMConfig.max_concurrent_calls`. Per-table exceptions SHALL NOT cancel sibling tasks; tables that failed SHALL appear in the result dict with value `None`.

#### Scenario: Concurrency cap is respected

- **WHEN** `describe_database` is invoked with 10 tables and an `LLMConfig(max_concurrent_calls=3)`
- **AND** the mocked `LLMClient.generate` records peak concurrent in-flight calls
- **THEN** the peak concurrent count SHALL never exceed 3
- **AND** all 10 tables SHALL be present in the returned dict

#### Scenario: One table's failure does not fail the batch

- **WHEN** `describe_database` is invoked with 5 tables and the mocked `LLMClient` raises `DescriptionParseError` on exactly one of them
- **THEN** the returned dict SHALL contain 5 entries
- **AND** the failing table's entry SHALL be `None`
- **AND** the other four SHALL be populated `TableDescription` instances
- **AND** no exception SHALL propagate to the caller

#### Scenario: Empty input returns an empty dict

- **WHEN** `describe_database([], {})` is awaited
- **THEN** the return value SHALL be `{}`
- **AND** `LLMClient.generate` SHALL NOT be called

### Requirement: Describe operations are observable

The system SHALL emit one log record per completed `describe_table` on the logger `sonar.engine.describe` at level `INFO`. The record SHALL include the schema, table name, column count, and outcome (`ok`, `parse_retry`, `failed`, or `provider_error`). The record SHALL NOT include prompt content, response content, or sample values.

#### Scenario: Ok outcome logged on first-try success

- **WHEN** `describe_table` returns successfully without retrying
- **THEN** one record SHALL be emitted on `sonar.engine.describe` at level `INFO` with outcome `"ok"`

#### Scenario: parse_retry outcome logged on second-try success

- **WHEN** `describe_table` succeeds on the second attempt after a parse-retry
- **THEN** one record SHALL be emitted with outcome `"parse_retry"`

#### Scenario: failed outcome logged on permanent failure

- **WHEN** `describe_table` raises `DescriptionParseError`
- **THEN** one record SHALL be emitted with outcome `"failed"` before the exception propagates

#### Scenario: provider_error outcome logged on LLM exception

- **WHEN** `LLMClient.generate` raises a non-parse exception (API error, network failure, etc.)
- **THEN** one record SHALL be emitted with outcome `"provider_error"` before the exception propagates

### Requirement: Progress callback on describe_database

The system SHALL accept an optional `on_progress` callback parameter on `describe_database`. The callback SHALL be `Callable[[DescribeProgress], None]` where `DescribeProgress` is a frozen dataclass with fields:

- `index: int` (0-based position in the table list)
- `total: int` (total table count)
- `schema: str`
- `table: str`
- `event: str` ("started" | "ok" | "parse_retry" | "failed" | "provider_error")
- `elapsed_ms: int | None` (milliseconds since table started; None for "started" events)
- `error_reason: str | None` (human-readable failure reason; None unless event is "failed" or "provider_error")

When `on_progress` is None, behaviour SHALL be identical to the current implementation (no side effects beyond logging).

#### Scenario: Callback fires on start and completion

- **WHEN** `describe_database` is invoked with 3 tables and an `on_progress` callback
- **THEN** the callback SHALL be invoked at least 6 times (once "started" and once completion event per table)
- **AND** each "started" event SHALL have `elapsed_ms=None` and `error_reason=None`
- **AND** each completion event SHALL have a non-None `elapsed_ms` >= 0

#### Scenario: Callback receives error reason on failure

- **WHEN** `describe_database` is invoked with a table whose LLM call raises a rate-limit error
- **THEN** the "provider_error" event's `error_reason` SHALL include the exception message
- **AND** the table SHALL still appear in the result dict with value `None`

#### Scenario: No callback is backward-compatible

- **WHEN** `describe_database` is invoked without `on_progress`
- **THEN** behaviour SHALL be identical to the pre-change implementation
- **AND** no additional side effects SHALL occur

### Requirement: Per-table timing

The engine SHALL track wall-clock elapsed time per table from the moment it enters `_bounded` to the moment it produces a result or exhausts retries. This timing SHALL be available in the `DescribeProgress` completion event and SHALL be measured in milliseconds.

#### Scenario: Elapsed time reflects actual duration

- **WHEN** `describe_database` is invoked with a table whose LLM call takes approximately 2 seconds
- **THEN** the completion event's `elapsed_ms` SHALL be >= 2000
- **AND** the "started" event's `elapsed_ms` SHALL be None

