# description-engine Specification (delta: first-run-experience)

## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Per-table timing

The engine SHALL track wall-clock elapsed time per table from the moment it enters `_bounded` to the moment it produces a result or exhausts retries. This timing SHALL be available in the `DescribeProgress` completion event and SHALL be measured in milliseconds.

#### Scenario: Elapsed time reflects actual duration

- **WHEN** `describe_database` is invoked with a table whose LLM call takes approximately 2 seconds
- **THEN** the completion event's `elapsed_ms` SHALL be >= 2000
- **AND** the "started" event's `elapsed_ms` SHALL be None
