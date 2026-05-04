# evaluation-toolkit Specification

## Purpose

The evaluation toolkit measures the quality of Sonar's context artifacts — relationship inference, semantic descriptions, and search results — against ground truth and structural metrics. It exposes a `sonar eval` CLI subcommand with five evaluation modes, each producing structured metrics and human-readable reports.

## Requirements

### Requirement: sonar eval prints a bundle quality report by default

The system SHALL expose a `sonar eval` CLI subcommand. When invoked with no mode flags, it SHALL load the bundle from the configured bundle directory and print a quality report covering:

- Description coverage: fraction of tables with a non-null description.
- Relationship coverage: fraction of tables with at least one relationship (inbound or outbound).
- Orphan tables: tables with no relationships at all.
- Connected components: number of connected components in the undirected relationship graph, size of the largest component.
- Mean reachability: average number of tables reachable from any starting table via relationship traversal.
- PII classification distribution: count of columns per PII risk level.
- Confidence summary: mean, minimum, and maximum table-level and column-level confidence scores across non-null descriptions.

The command SHALL exit 0 on success and non-zero if the bundle cannot be loaded.

#### Scenario: Quality report on a complete bundle

- **WHEN** `sonar eval` is invoked against a bundle where every table has a non-null description and at least one relationship
- **THEN** description coverage SHALL be 1.0
- **AND** relationship coverage SHALL be 1.0
- **AND** orphan count SHALL be 0

#### Scenario: Quality report on a bundle with partial descriptions

- **WHEN** `sonar eval` is invoked against a bundle where 3 of 5 tables have non-null descriptions
- **THEN** description coverage SHALL be 0.6

#### Scenario: Quality report identifies orphan tables

- **WHEN** `sonar eval` is invoked against a bundle where one table has no inbound or outbound relationships
- **THEN** that table SHALL appear in the orphan tables list

#### Scenario: Graph reachability reflects connected components

- **WHEN** `sonar eval` is invoked against a bundle with two disconnected groups of tables (e.g., 3 tables connected to each other, 2 tables connected to each other)
- **THEN** connected components SHALL be 2
- **AND** largest component size SHALL be 3

#### Scenario: Missing bundle fails gracefully

- **WHEN** `sonar eval` is invoked and no bundle exists at the configured bundle directory
- **THEN** the command SHALL exit with a non-zero status
- **AND** a single error line SHALL be printed to stderr

### Requirement: sonar eval measures relationship recall and precision against declared FKs

When invoked with `--relationships <dsn>`, the system SHALL connect to the specified database, discover tables and declared foreign keys, run the relationship inference heuristic against the discovered tables with an empty FK list, and compare the inferred edges against the declared FKs.

The system SHALL report: recall (fraction of declared FKs that were inferred), precision (fraction of inferred edges that match a declared FK), and F1 score. The system SHALL also report a per-table breakdown and lists of missed declared FKs and false-positive inferred edges.

An inferred edge SHALL be considered a match for a declared FK when the six-tuple `(source_schema, source_table, source_column, target_schema, target_table, target_column)` is identical. Comparison SHALL be case-sensitive.

#### Scenario: Perfect inference on a database with simple naming

- **WHEN** `sonar eval --relationships <dsn>` is invoked against a database where the inference heuristic can recover all declared FKs
- **THEN** recall SHALL be 1.0
- **AND** precision SHALL be 1.0
- **AND** missed list SHALL be empty
- **AND** false-positive list SHALL be empty

#### Scenario: Partial inference reports correct metrics

- **WHEN** `sonar eval --relationships <dsn>` is invoked against a database with 10 declared FKs and the heuristic infers 7 correct edges plus 1 false positive
- **THEN** recall SHALL be 0.7
- **AND** precision SHALL be 7/8 (0.875)
- **AND** missed list SHALL contain the 3 unrecovered FKs
- **AND** false-positive list SHALL contain the 1 spurious edge

#### Scenario: Database with no declared FKs

- **WHEN** `sonar eval --relationships <dsn>` is invoked against a database with no declared foreign keys
- **THEN** recall SHALL be reported as undefined (no ground truth)
- **AND** inferred count SHALL be reported
- **AND** the command SHALL exit 0

### Requirement: sonar eval measures search relevance against a ground-truth file

When invoked with `--search <ground-truth-path>`, the system SHALL load the bundle, parse the ground-truth file, and for each query in the file, run the search tool logic and compare results against expected tables.

The ground-truth file SHALL be a YAML file containing a list of queries, each with a query string and a list of expected table keys in `schema.table` format.

The system SHALL report per-query precision@k and recall@k (where k is the number of results returned by the search tool), and aggregate mean reciprocal rank (MRR) across all queries.

#### Scenario: Search returns all expected tables

- **WHEN** `sonar eval --search <file>` is invoked and for a given query all expected tables appear in the search results
- **THEN** recall@k for that query SHALL be 1.0

#### Scenario: Search returns expected tables at top positions

- **WHEN** `sonar eval --search <file>` is invoked and for a given query the first expected table appears at position 1
- **THEN** reciprocal rank for that query SHALL be 1.0

#### Scenario: Search misses expected tables

- **WHEN** `sonar eval --search <file>` is invoked and for a given query 2 of 3 expected tables appear in the results
- **THEN** recall@k for that query SHALL be approximately 0.67

#### Scenario: Invalid ground-truth file

- **WHEN** `sonar eval --search <file>` is invoked with a file that does not parse as valid YAML or does not match the expected schema
- **THEN** the command SHALL exit with a non-zero status
- **AND** a descriptive error message SHALL be printed to stderr

### Requirement: sonar eval compares two bundles structurally

When invoked with `--diff <other-bundle-dir>`, the system SHALL load the current bundle (from `--bundle-dir`) and the other bundle, and report structural differences:

- Tables: added, removed.
- Relationships: added, removed, grouped by kind (declared vs inferred).
- Descriptions: added (null to non-null), removed (non-null to null), changed (text or confidence delta). For changed descriptions, the system SHALL report whether the description text changed (boolean) and the confidence delta.

The diff SHALL NOT perform deep text comparison of description bodies — a boolean "changed" flag SHALL suffice.

#### Scenario: Identical bundles produce empty diff

- **WHEN** `sonar eval --diff <path>` is invoked with two identical bundles
- **THEN** tables added/removed SHALL both be empty
- **AND** relationships added/removed SHALL both be empty
- **AND** descriptions changed SHALL be empty

#### Scenario: New table detected in diff

- **WHEN** the current bundle has a table `(public, orders)` that the other bundle does not
- **THEN** the diff SHALL report `public.orders` as an added table

#### Scenario: Description confidence change detected

- **WHEN** a table exists in both bundles with non-null descriptions but the confidence changed from 0.85 to 0.72
- **THEN** the diff SHALL report a confidence delta of -0.13 for that table

#### Scenario: Relationship added by inference improvement

- **WHEN** the current bundle has an inferred relationship that the other bundle does not
- **THEN** the diff SHALL report it as an added relationship with kind `inferred`

### Requirement: sonar eval scores description quality via LLM-as-judge

When invoked with `--descriptions`, the system SHALL load the bundle, and for each table with a non-null description, send the table schema and generated description to an LLM judge that scores three dimensions:

- **Accuracy** (0.0 - 1.0): does the description correctly reflect the schema?
- **Completeness** (0.0 - 1.0): are all important aspects of the table covered?
- **Specificity** (0.0 - 1.0): does the description add domain knowledge beyond column names?

The system SHALL report per-table scores and aggregate means across all scored tables. Tables scoring below 0.5 on any dimension SHALL be flagged in the report. The judge SHALL NOT receive row samples — scoring is based on schema and description only.

This mode is advisory. The system SHALL NOT define pass/fail thresholds.

#### Scenario: High-quality descriptions score well

- **WHEN** `sonar eval --descriptions` is invoked against a bundle with accurate, complete, specific descriptions
- **THEN** aggregate mean scores SHALL each exceed 0.7

#### Scenario: Tables with null descriptions are skipped

- **WHEN** `sonar eval --descriptions` is invoked and a table has a null description
- **THEN** that table SHALL be excluded from scoring
- **AND** the report SHALL note the count of skipped tables

#### Scenario: Low-scoring table is flagged

- **WHEN** a table's description scores below 0.5 on the accuracy dimension
- **THEN** that table SHALL appear in the flagged list with its per-dimension scores

#### Scenario: LLM judge failure is non-fatal

- **WHEN** the LLM judge fails to return valid scores for one table
- **THEN** that table SHALL be excluded from aggregate metrics
- **AND** the report SHALL note the count of judge failures
- **AND** the command SHALL still exit 0 if other tables were scored successfully

### Requirement: sonar eval supports machine-readable JSON output

When any evaluation mode is invoked with `--json`, the system SHALL output structured JSON to stdout instead of the human-readable summary. The JSON SHALL follow a common envelope with `mode`, `bundle` (path), `metrics` (aggregate numbers), and `details` (per-item breakdown) fields.

#### Scenario: JSON output for bundle quality

- **WHEN** `sonar eval --json` is invoked
- **THEN** stdout SHALL contain valid JSON with `mode` set to `"quality"`
- **AND** `metrics` SHALL contain numeric values for description coverage, relationship coverage, orphan count, component count, and mean reachability

#### Scenario: JSON output for relationship evaluation

- **WHEN** `sonar eval --relationships <dsn> --json` is invoked
- **THEN** stdout SHALL contain valid JSON with `mode` set to `"relationships"`
- **AND** `metrics` SHALL contain `recall`, `precision`, and `f1` as floats

#### Scenario: JSON output is parseable

- **WHEN** any eval mode is invoked with `--json`
- **THEN** the output SHALL be valid JSON parseable by `json.loads`
- **AND** the output SHALL contain exactly one JSON object (not a stream)

### Requirement: sonar eval reads the bundle directory from --bundle-dir

The system SHALL accept `--bundle-dir <path>` to override the default bundle location (`.sonar/`). This flag SHALL apply to all evaluation modes that read a bundle (quality report, search relevance, description quality, and the "current" side of bundle diff). The `--relationships` mode SHALL NOT read from the bundle — it connects to a live database.

#### Scenario: Custom bundle directory

- **WHEN** `sonar eval --bundle-dir /tmp/my-bundle/` is invoked
- **THEN** the bundle SHALL be loaded from `/tmp/my-bundle/`

#### Scenario: Default bundle directory

- **WHEN** `sonar eval` is invoked without `--bundle-dir`
- **THEN** the bundle SHALL be loaded from `.sonar/` relative to the current working directory
