# first-run-experience Design

## Architecture

The progress system has two layers: the description engine emits structured events via a callback, and the CLI layer renders those events as terminal output. The engine stays output-agnostic; the renderer is a pure function of events.

```
describe_database(tables, samples, on_progress=callback)
    |
    v
DescribeProgress events  -->  scan_output.py  -->  stdout
    |
    v
dict[key, TableDescription | None]  -->  summary formatting
```

## Decisions

### D1: Callback, not async iterator

The progress events flow via a sync `Callable[[DescribeProgress], None]` parameter on `describe_database`, not an async iterator or event emitter.

Rationale: the consumer (print to stdout) is synchronous and trivial. An async iterator adds complexity (cancellation, backpressure) for no benefit. The callback is called from within the async task, which is fine because `print()` doesn't block meaningfully.

Revisit when: a consumer needs to aggregate events asynchronously (e.g. a web dashboard).
Reversibility: cheap — the callback signature can be widened to async without breaking sync callers by wrapping.

### D2: Output lives in scan_output.py, not cli.py

Progress rendering and summary formatting are extracted to `src/sonar/scan_output.py`. The CLI wires things together but doesn't contain formatting logic.

Rationale: cli.py is already 730 lines. Formatting logic is independently testable. Keeps the CLI as orchestration-only.

Revisit when: cli.py is split into per-command modules.
Reversibility: cheap — internal move.

### D3: Elapsed time uses monotonic clock

Per-table timing uses `time.monotonic()` (not `time.time()`). Wall-clock summary uses the same.

Rationale: monotonic is immune to NTP adjustments and is the standard for measuring durations.

Revisit when: never (this is a settled best practice).
Reversibility: n/a.

### D4: No sampling progress lines

The sampling phase (downloading sample rows) does not get per-table progress lines. Only the description phase does.

Rationale: sampling is fast (seconds total, bounded by `_DB_SAMPLE_CONCURRENCY=5`). Description is the slow phase (minutes). Adding sampling progress is noise. YAGNI.

Revisit when: users report sampling is slow on very large databases.
Reversibility: cheap — same callback pattern.

### D5: DescribeProgress is a frozen dataclass, not a TypedDict

Matches the project convention (frozen dataclasses everywhere). Provides type safety and immutability.

Revisit when: never.
Reversibility: n/a.
