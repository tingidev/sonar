## Why

The scan command is the first thing every Sonar user runs, and the current output is near-silent: two print statements bookending minutes of LLM work. Users have no visibility into which tables are being described, how long each takes, or why specific tables fail. This makes iterating on description quality (Phase 4, #15) painful and debugging rate-limit failures opaque.

## What Changes

- Per-table streaming progress lines printed to stdout as each description starts and completes, with index, table name, and elapsed time.
- Failed tables report their specific error reason inline and continue the scan.
- Final summary replaces the current bare output with total time, success/failure counts, bundle path, and a list of failed tables with retry guidance.
- The description engine gains an optional progress callback so the CLI layer can render events without the engine knowing about output formatting.

## Capabilities

### New Capabilities
- `scan-output`: Per-table streaming progress format, final scan summary format, and error rendering for the `sonar scan` command.

### Modified Capabilities
- `description-engine`: Adding an optional progress callback to `describe_database` that fires on table-start and table-complete events. Enriching error reporting to surface the failure reason (rate limit, auth, parse failure, etc.) alongside the outcome.

## Impact

- `src/sonar/engine/describe.py` — callback parameter on `describe_database`, per-table timing, error reason capture.
- `src/sonar/cli.py` — new `_scan_pipeline` output flow, summary rewrite, progress callback wiring.
- New file `src/sonar/scan_output.py` — progress rendering and summary formatting (extracted from CLI to keep cli.py lean and testable).
- Tests for progress callback contract and output formatting.
