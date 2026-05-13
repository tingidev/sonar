## MODIFIED Requirements

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
