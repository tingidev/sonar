## ADDED Requirements

### Requirement: Structured output dataclasses

The system SHALL expose two frozen dataclasses that are the sole return shape for description generation:

- `ColumnDescription` with fields `name: str`, `description: str`, `semantic_type: SemanticType`, `pii_risk: PIIRisk`, `confidence: float`.
- `TableDescription` with fields `schema: str`, `name: str`, `description: str`, `grain: str`, `domain_hints: tuple[str, ...]`, `columns: tuple[ColumnDescription, ...]`, `confidence: float`.

`SemanticType` SHALL be a `StrEnum` with members `IDENTIFIER`, `DIMENSION`, `MEASURE`, `OTHER`. `PIIRisk` SHALL be a `StrEnum` with members `NONE`, `LOW`, `HIGH`. Both enums SHALL use lowercase string values so instances round-trip through JSON without custom encoders.

#### Scenario: TableDescription is immutable

- **WHEN** a `TableDescription` instance is constructed
- **THEN** any attempt to assign to one of its fields SHALL raise `dataclasses.FrozenInstanceError`
- **AND** the `columns` field SHALL be a `tuple`, not a `list`
- **AND** the `domain_hints` field SHALL be a `tuple`, not a `list`

#### Scenario: SemanticType round-trips through JSON

- **WHEN** a `TableDescription` is serialised with `json.dumps(..., default=str)` and deserialised with `json.loads`
- **THEN** the `semantic_type` values SHALL appear as lowercase strings matching the enum value
- **AND** passing such a string to `SemanticType(value)` SHALL reconstruct the enum member

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
- **AND** SHALL describe the expected output JSON shape including the `SemanticType` and `PIIRisk` enum values

#### Scenario: PII columns are classified by the LLM

- **WHEN** `describe_table` is called with a table containing an `email` column and an `ssn` column and the mocked LLM returns corresponding `pii_risk` values of `"high"`
- **THEN** the returned `ColumnDescription.pii_risk` for those columns SHALL be `PIIRisk.HIGH`
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

The system SHALL emit one log record per completed `describe_table` on the logger `sonar.engine.describe` at level `INFO`. The record SHALL include the schema, table name, column count, and outcome (`ok`, `parse_retry`, or `failed`). The record SHALL NOT include prompt content, response content, or sample values.

#### Scenario: Ok outcome logged on first-try success

- **WHEN** `describe_table` returns successfully without retrying
- **THEN** one record SHALL be emitted on `sonar.engine.describe` at level `INFO` with outcome `"ok"`

#### Scenario: parse_retry outcome logged on second-try success

- **WHEN** `describe_table` succeeds on the second attempt after a parse-retry
- **THEN** one record SHALL be emitted with outcome `"parse_retry"`

#### Scenario: failed outcome logged on permanent failure

- **WHEN** `describe_table` raises `DescriptionParseError`
- **THEN** one record SHALL be emitted with outcome `"failed"` before the exception propagates
