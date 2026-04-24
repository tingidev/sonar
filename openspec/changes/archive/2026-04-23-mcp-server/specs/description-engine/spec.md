## MODIFIED Requirements

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

#### Scenario: PII columns are classified by the LLM

- **WHEN** `describe_table` is called with a table containing an `email` column and an `ssn` column and the mocked LLM returns corresponding `pii_risk` values of `"high"`
- **THEN** the returned `ColumnDescription.pii_risk` for those columns SHALL be `PIIRisk.HIGH`
- **AND** the engine SHALL NOT override the LLM's classification with its own rules

#### Scenario: Medium-risk column is classified by the LLM

- **WHEN** `describe_table` is called with a table containing a `city` column and the mocked LLM returns `pii_risk="medium"`
- **THEN** the returned `ColumnDescription.pii_risk` for that column SHALL be `PIIRisk.MEDIUM`
- **AND** the engine SHALL NOT override the LLM's classification with its own rules
