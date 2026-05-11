# scan-output Specification

## Purpose

Streaming progress output and final summary for `sonar scan`. Renders description-engine progress events as human-readable terminal output. Designed for pipe/tee/CI-friendliness: no in-place overwrites, no cursor manipulation.

## Requirements

### Requirement: Discovery line

The system SHALL print a discovery summary line after schema enumeration completes. The line SHALL include the table count and schema count.

#### Scenario: Discovery output

- **WHEN** a scan discovers 15 tables across 3 schemas
- **THEN** the system SHALL print a line containing "Discovered 15 tables in 3 schemas"

### Requirement: Per-table progress lines

The system SHALL print one line per table as each description starts, and update with a completion line when the description finishes. Lines SHALL include:

- 1-based index and total count (e.g. `[3/15]`)
- Schema-qualified table name
- On completion: elapsed time in seconds (e.g. `2.1s`) and outcome

Multiple "started" lines SHALL be visible simultaneously when descriptions run concurrently. Lines SHALL be appended (newline-terminated), never overwritten.

#### Scenario: Successful table output

- **WHEN** table `public.orders` (3rd of 15) completes successfully in 2134ms
- **THEN** the output SHALL include a line matching the pattern `[3/15] public.orders ... ok (2.1s)`

#### Scenario: Failed table output

- **WHEN** table `public.events` fails with a rate-limit error
- **THEN** the output SHALL include a line showing the failure reason inline (e.g. `[5/15] public.events ... failed: rate limit exceeded (4.2s)`)

### Requirement: Final summary

The system SHALL print a final summary block after all descriptions complete. The summary SHALL include:

- Total tables and relationship counts
- Total scan wall-clock time
- Success and failure counts
- Bundle output path
- If any tables failed: a list of failed table names with their error reasons and guidance on how to retry

#### Scenario: Clean scan summary

- **WHEN** a scan of 15 tables completes with 0 failures in 34.2 seconds
- **THEN** the summary SHALL include: table count, relationship count, elapsed time, bundle path
- **AND** SHALL NOT include a failures section

#### Scenario: Partial failure summary

- **WHEN** a scan of 15 tables completes with 2 failures
- **THEN** the summary SHALL list the 2 failed tables with their error reasons
- **AND** SHALL include guidance on re-running the scan

### Requirement: Warnings section

The system SHALL append warnings for cross-database FK exclusions and cross-dataset FK exclusions, consistent with current behaviour but integrated into the summary block.

#### Scenario: Cross-database FK warning

- **WHEN** the connector reports 3 cross-database foreign keys were dropped
- **THEN** the summary SHALL include a line stating the count and that they were excluded
