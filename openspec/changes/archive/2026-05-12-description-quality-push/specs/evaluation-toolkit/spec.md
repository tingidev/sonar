## MODIFIED Requirements

### Requirement: sonar eval scores description quality via LLM-as-judge

When invoked with `--descriptions`, the system SHALL load the bundle, and for each table with a non-null description, send the table schema and generated description to an LLM judge that scores three dimensions:

- **Accuracy** (1-5): does the description correctly reflect what the schema shows? Claims must be supported by column names, types, and structural signals.
- **Specificity** (1-5): does the description add useful detail beyond restating column names?
- **Domain inference** (1-5): does the description correctly identify the table's domain and use appropriate terminology?

Each dimension SHALL include a reasoning string from the judge.

The system SHALL report per-table scores and aggregate means across all scored tables. Tables scoring below 3 on any dimension SHALL be flagged in the report. The judge SHALL NOT receive row samples -- scoring is based on schema and description only.

This mode is advisory. The system SHALL NOT define pass/fail thresholds.

#### Scenario: High-quality descriptions score well

- **WHEN** `sonar eval --descriptions` is invoked against a bundle with accurate, specific, domain-aware descriptions
- **THEN** aggregate mean scores SHALL each be at least 4

#### Scenario: Tables with null descriptions are skipped

- **WHEN** `sonar eval --descriptions` is invoked and a table has a null description
- **THEN** that table SHALL be excluded from scoring
- **AND** the report SHALL note the count of skipped tables

#### Scenario: Low-scoring table is flagged

- **WHEN** a table's description scores below 3 on the accuracy dimension
- **THEN** that table SHALL appear in the flagged list with its per-dimension scores and reasoning

#### Scenario: LLM judge failure is non-fatal

- **WHEN** the LLM judge fails to return valid scores for one table
- **THEN** that table SHALL be excluded from aggregate metrics
- **AND** the report SHALL note the count of judge failures
- **AND** the command SHALL still exit 0 if other tables were scored successfully
