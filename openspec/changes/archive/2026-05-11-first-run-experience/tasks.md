# first-run-experience Tasks

## Implementation

- [x] 1. Add `DescribeProgress` dataclass to `describe.py` with fields: index, total, schema, table, event, elapsed_ms, error_reason
- [x] 2. Add `on_progress` callback parameter to `describe_database` (default None). Fire "started" event when a table enters `_bounded`, fire completion event (ok/parse_retry/failed/provider_error) with elapsed_ms and error_reason when it exits. Preserve backward compatibility when callback is None.
- [x] 3. Create `src/sonar/scan_output.py` with:
  - `print_discovery(table_count, schema_count)` — discovery line
  - `print_table_progress(event: DescribeProgress)` — per-table start/completion line
  - `print_scan_summary(...)` — final summary with timing, counts, failures, bundle path, warnings
- [x] 4. Update `_scan_pipeline` in `cli.py` to: track scan start time, wire `on_progress` callback to `print_table_progress`, replace inline prints with `scan_output` functions, replace `_print_scan_summary` with `scan_output.print_scan_summary`
- [x] 5. Write tests for `DescribeProgress` callback contract: fires on start/complete, includes error_reason on failure, backward-compatible when None
- [x] 6. Write tests for `scan_output.py` formatting: discovery line, progress lines, summary with/without failures
- [x] 7. Run full test suite, verify coverage stays above 80%
