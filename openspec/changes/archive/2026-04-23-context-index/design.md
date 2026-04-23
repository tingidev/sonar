## Context

Three capabilities produce disjoint pieces of the context map:

- `postgres-connector` returns `list[Table]` and `list[ForeignKey]` (raw schema).
- `description-engine` returns `dict[(schema, name), TableDescription | None]` (LLM semantic layer; failed tables are `None`).
- `relationship-mapping` returns `list[Relationship]` (declared + inferred edges).

`mcp-server` (#5) will load a static bundle and serve tools against it. Phase 1 scale: at most a few hundred tables in a typical operator database; total bundle size O(100 KB) JSON.

The existing `src/sonar/index/store.py` scaffold (`save(dict) -> Path`, `load() -> dict | None`) predates the three typed shapes above and treats the bundle as an opaque dict. It is replaced wholesale — not preserved as API.

## Goals / Non-Goals

**Goals:**
- Compose the three existing shapes into a typed `ContextBundle` frozen dataclass with a minimal, obvious interface.
- Persist the bundle under `.sonar/` in a shape that diffs cleanly across re-runs and grows additively when new capabilities land.
- Wire `sonar scan --dsn <dsn>` end-to-end: connect → discover → sample → describe → map → write.
- Unblock `mcp-server` (#5) with a stable on-disk contract.

**Non-Goals:**
- Incremental / partial scans. Full overwrite is the only write mode in Phase 1.
- A search index (tokenised or embeddings-based). `mcp-server`'s search tool can do in-memory substring match at Phase 1 scale.
- Persisting row samples. PII-off-disk is a first-principle, not a limitation.
- Multi-database bundles. One `.sonar/` per DSN scanned. Multi-tenant structure is speculative.
- Atomic / concurrent-writer safety. `sonar scan` is operator-run, single-writer, no concurrent readers at Phase 1.

## Decisions

### D1 — Thin composition in memory

`ContextBundle` is a frozen dataclass holding three parallel collections plus a `BundleMeta`:

```python
@dataclass(frozen=True)
class ContextBundle:
    meta: BundleMeta
    tables: tuple[Table, ...]
    descriptions: dict[tuple[str, str], TableDescription | None]
    relationships: tuple[Relationship, ...]
```

Consumers (MCP describe tool) join on `(schema, name)` at call time — trivial at Phase 1 table counts. Rejected: a "fat" `EnrichedTable` that pre-merges column + description fields. Fat requires a new type that duplicates `Column` and `ColumnDescription` fields and invents merge rules when either side is missing. Thin lets every future capability land as a sibling collection without rewriting the merged type.

Revisit when: MCP `describe` tool's per-call join shows up in a profile, or a second consumer wants a materialised joined view we're already computing inside MCP.
Reversibility: expensive (in-memory shape bleeds into the public bundle dataclass signature).

### D2 — Per-capability on-disk files

```
.sonar/
  meta.json           # schema_version, timestamps, connector identity
  tables.json         # list of Table (postgres-connector shape)
  descriptions.json   # {"[schema, name]": TableDescription | null, ...}
  relationships.json  # list of Relationship (relationship-mapping shape)
```

Distinct from D1 — the in-memory shape does not determine the on-disk one. Three reasons:

1. Re-runs on a stable schema churn `descriptions.json` (LLM drift) but leave `tables.json` untouched. That signal is lost in a single-file layout.
2. When partial rebuild arrives (`sonar scan --only descriptions`), per-capability files give a natural seam. Single-file needs a format migration.
3. On-disk grain matches the code's capability boundaries — one mental model for both readers and operators.

Relationships are inherently global (edges span tables), so a per-table layout is a hybrid anyway. That weakens the per-table alternative's "symmetry" pitch. `meta.json` is the bundle's version header — a single `schema_version` integer governs all three capability files; they are not versioned independently.

Revisit when: partial rebuild lands and cross-file consistency bites, or a consumer wants per-table streaming reads.
Reversibility: expensive (downstream consumers — starting with `mcp-server` — parse these files).

### D3 — `schema_version: 1` from day one

`meta.json` carries `schema_version: int`. `ContextStore.read()` raises `BundleVersionError` on mismatch with the bundle version it understands. No migration logic in v1 — unsupported versions fail loudly.

Alternative considered: omit the field, assume current shape. Rejected because the first breaking shape change then needs a version field *and* a migration tool at the same time; one integer now averts that.

Revisit when: the first shape change lands that is not purely additive (i.e. cannot be read by v1 readers). At that point add migration logic alongside the version bump.
Reversibility: cheap (adding validation later is a pure expansion; the field itself is one line).

### D4 — `meta.json` minimum fields

```json
{
  "schema_version": 1,
  "generated_at": "2026-04-21T14:30:00Z",
  "connector": "postgres",
  "database": "postgres@localhost:5433/sonar_test"
}
```

- `schema_version` — D3.
- `generated_at` — ISO 8601 UTC. Operator-visible "when did we last scan?" signal. `mcp-server` (#5) can surface it in a tool response.
- `connector` — string identifier for the source type. Today `"postgres"`; more land when a second connector does.
- `database` — human-readable host/db label with **no credentials**. DSN password is stripped before writing.

Rejected for v1: `sonar_version` (reintroduce when first breaking engine change ships), `llm_model` (description-engine internal — surface inside `TableDescription` if ever needed), `scan_duration_ms` (observability concern, not a contract field).

Revisit when: `mcp-server` or an operator requests a field not in this set (e.g. "which Haiku version wrote these descriptions").
Reversibility: cheap (meta is additive; new fields default to absent on old bundles).

### D5 — Failed descriptions persist as `null`, not omitted

`descriptions.json` keys **every** table in `tables.json`. Tables the LLM failed on serialise as JSON `null`. The key/value mapping makes "scanned but failed" distinguishable from "never scanned" — the latter being: table present in `tables.json` but absent from `descriptions.json`, which is an integrity violation and SHALL raise on read.

Alternative considered: omit failures entirely. Rejected because it hides a real state (`description-engine` already supports partial-success by design) and collapses two different failure modes into one absence.

Revisit when: the "never scanned" state becomes legitimate — e.g. when `sonar scan --only tables` lands and it is valid for `descriptions.json` to be missing entirely.
Reversibility: cheap (policy change in `ContextStore.read`; file shape unaffected).

### D6 — No row samples on disk

`sonar scan` reads samples and hands them to `description-engine`. Samples are not persisted to `.sonar/`. `mcp-server`'s `sample` tool (change #5) will open a live DB connection per call — that means `sonar serve` accepts a DSN.

This matches `description-engine`'s log discipline (no sample values in log records). Consistent PII-off-disk posture across the pipeline.

Rejected: caching samples at N=5 per table in the bundle. Smaller operator footprint at the cost of writing raw row data — including PII — to a file the operator then stores/copies/backs up. The price is too high for the ergonomic win.

Revisit when: `mcp-server` performance profile shows per-call DB connection overhead dominates, or when a clear "offline read-only bundle" use case appears (training-time context, air-gapped review).
Reversibility: expensive (bundle shape change + PII posture change). Defer until there is a named consumer that justifies writing PII to disk.

### D7 — Dict key encoding for descriptions

In memory `descriptions` is keyed by `tuple[str, str]`. JSON has no tuple-key support. On disk `descriptions.json` uses a JSON object keyed by `"<schema>.<name>"`. `ContextStore` encodes/decodes on the I/O boundary.

The `"."` ambiguity that would otherwise make this reversal-expensive is closed upstream: `postgres-connector`'s `discover_tables` and `discover_relationships` raise `ValueError` on any identifier containing a `"."`. The bundle's on-disk format never sees a dotted identifier, so decode is unambiguous and the spec-delta on `postgres-connector` in this change pins that invariant. Postgres identifiers can legally contain a dot but operator databases effectively never do (`pg_catalog` and `information_schema` use plain identifiers throughout), and surfacing a clear error at the connector boundary is preferable to silently corrupting a bundle later.

Rejected: a JSON array of `{"schema": ..., "name": ..., "description": ...}` objects. More robust in theory, more verbose and less grep-friendly. The upstream guard gives the same robustness without the verbosity cost.

Revisit when: a real operator database ships with an identifier containing `"."` and the connector's `ValueError` blocks a scan. At that point, either relax the guard and switch to the array-of-objects form (format migration, `schema_version` bump) or document the restriction as permanent.
Reversibility: cheap (the upstream guard means the on-disk format has a pinned invariant; changing it later is a well-scoped migration triggered by a concrete failure, not a race against silent corruption).

### D8 — Bundle I/O is synchronous

`ContextStore.write()` and `.read()` are plain sync functions. `sonar scan` awaits the async pipeline (connector + description engine), then calls `store.write(bundle)` synchronously once. No async I/O around the file ops.

Alternative considered: `aiofiles` for consistency with the rest of the pipeline. Rejected — Phase 1 bundle size is O(100 KB), write latency is irrelevant, and sync file I/O is one less moving part.

Revisit when: bundles exceed ~10 MB and scan completion feels laggy, or concurrent readers appear.
Reversibility: cheap (internal I/O — not a public shape).

### D9 — `sonar scan` CLI orchestration lives in `src/sonar/cli.py`

The `scan` subcommand owns pipeline orchestration directly — it instantiates the connector, description engine, and context store, runs them in sequence, and handles errors at the CLI boundary (exit codes, stderr messages). No separate `Pipeline` / `Orchestrator` class.

CLI surface: `sonar scan <dsn> [--url <dsn>] [--bundle-dir <path>]`. The DSN is a positional argument; `--url` is accepted as a named alias for operators who prefer flag form. `--bundle-dir` defaults to `.sonar/`. Rejected `--dsn` as the primary flag — it is the weakest of the three options (jargon-leaking, abbreviated, less discoverable than `--url`).

Alternative considered: a `ScanPipeline` class in `src/sonar/pipeline.py`. Rejected because (a) there's one caller, (b) it's a linear data flow with no reuse surface, (c) an abstraction here would be pure ceremony. If a second entry point ever needs the same flow (background daemon, test harness), extract then.

Revisit when: a second caller needs the same end-to-end flow.
Reversibility: cheap (extract a function or class later without touching the bundle shape or capability modules).

### D11 — Integration-test LLM injection via monkeypatch on `sonar.cli.AnthropicClient`

`sonar scan` instantiates `AnthropicClient` directly inside the CLI body from `ANTHROPIC_API_KEY`. The integration test in `tests/test_scan.py` substitutes a `FakeLLMClient` via `monkeypatch.setattr("sonar.cli.AnthropicClient", FakeLLMClientFactory)` rather than a production injection seam. The patch target — `sonar.cli.AnthropicClient` — is the documented seam.

Rejected alternatives:
- Env-flag switch (`SONAR_LLM=fake`) — introduces a test-only code path into production, violates the "boundary-follows-I/O" principle established in `relationship-mapping`'s LEARNINGS write-up.
- Constructor-injection factory imported into `cli.py` — cleaner, but costs a module-level seam whose only consumer is the test. Speculative abstraction under freeze discipline.

The monkeypatch approach keeps the production import graph unchanged and documents the seam as a convention (import `AnthropicClient` at module scope in `cli.py`, never from inside the `scan` function body) rather than a type-level interface.

Speculative: no — the alternative abstractions would be speculative.

Revisit when: a second test entry point needs the same substitution, or when a non-test caller (e.g. a programmatic API) needs to inject its own `LLMClient`.
Reversibility: cheap (swap to constructor injection; only the CLI module and one test file change).

### D10 — `.sonar/` is gitignored

Scanning writes schema metadata and LLM-generated descriptions of internal table names — low-but-nonzero sensitivity. Add `.sonar/` to `.gitignore` in this change. Operators opt in to committing if they want a shared bundle artefact.

Revisit when: a shared-bundle workflow becomes the documented path.
Reversibility: cheap (one-line gitignore edit either way).

## Risks / Trade-offs

- **Risk: `mcp-server` parses `descriptions.json` directly and the `"<schema>.<name>"` key encoding breaks on a pathological name.** → Mitigation: D7 revisit trigger is first operator-reported bug; the single code path for encoding/decoding lives in `ContextStore`, so a format fix is localised.
- **Risk: Writing four files non-atomically leaves the bundle in a half-written state on crash.** → Mitigation: Phase 1 scale + single-writer makes this rare. Acceptable failure mode: operator re-runs `sonar scan`. Not worth fs-level transactions now. Revisit when an always-on writer appears.
- **Risk: `schema_version` bump becomes a flag day for `mcp-server`.** → Mitigation: D3 plus "bundle versions govern all files together" rule (D4) keeps the bump predictable — one `meta.json` change, `mcp-server` has a single version check.
- **Trade-off: Operators lose "offline describe" without persisted samples.** → Accepted per D6. The PII posture cost of caching samples outweighs the ergonomic gain until a named consumer asks.
- **Trade-off: Thin composition means MCP `describe` tool joins at call time.** → Accepted per D1. O(columns-per-table) join at in-memory Phase 1 scale is trivial.

## Migration Plan

- `src/sonar/index/store.py` is rewritten. No production callers (the `dict`-based scaffold was never wired up). No deprecation window.
- `.gitignore` picks up `.sonar/` in the same commit as the new `ContextStore`. Any existing local `.sonar/` directories on developer machines are harmless.
- `sonar scan --dsn ...` becomes the first working end-to-end CLI command. Previously a stub.

## Open Questions

- **Whether `meta.json` should also carry a count summary (`n_tables`, `n_failed_descriptions`, `n_relationships`).** Operator-friendly, zero risk. Parked because no concrete consumer asks for it yet — `mcp-server`'s planned tools compute these from the files they already read. Revisit when an operator-facing "scan summary" command lands.
- **Whether `.sonar/` path is configurable.** Today it's `Path(".sonar")` relative to CWD. Configurable via CLI flag or env var is an obvious extension, but no named consumer needs it. Revisit when the first multi-database developer workflow lands.
- **Whether `ContextStore.read()` should return `ContextBundle | None` (missing = None) or raise.** Currently leaning `None` for missing, raise for corrupted. Settle during implementation of `mcp-server` when the caller contract is visible. Parked: marked as `Open Question` rather than freezing now.
