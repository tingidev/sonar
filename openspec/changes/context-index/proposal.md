## Why

Three capabilities (`postgres-connector`, `description-engine`, `relationship-mapping`) each produce a piece of the context map, but nothing composes them into a single artifact on disk. The next change (`mcp-server`, #5) needs to load a static bundle and serve tools against it — it will not re-scan on every request. Without a context index, `sonar scan` has nowhere to write and `sonar serve` has nothing to read.

## What Changes

- Introduce `context-index` capability at `src/sonar/index/`. Replaces the `ContextStore(dict) -> Path` scaffold with a typed `ContextBundle` frozen dataclass composing the three existing shapes.
- **BREAKING (internal):** Remove the placeholder `ContextStore.save(dict) -> Path` / `load() -> dict | None` from `src/sonar/index/store.py`. Not yet used in production, so no external consumers break. Replaced with `ContextStore(bundle_dir).write(bundle: ContextBundle) -> None` and `.read() -> ContextBundle | None`.
- Persist the bundle as three per-capability JSON files plus one `meta.json` header in `.sonar/`: `meta.json`, `tables.json`, `descriptions.json`, `relationships.json`. Descriptions for failed tables are persisted as JSON `null` entries keyed by `[schema, name]` so the distinction between "never scanned" and "scanned but failed" is preserved.
- No row samples are persisted. Matches the PII logging discipline already adopted by `description-engine` (no sample values in log records).
- Wire `sonar scan <dsn>` end-to-end in `src/sonar/cli.py`: connect → discover → sample → describe → map relationships → write bundle. First real integration of the pipeline. DSN is a positional argument; `--url` accepted as an alias; `--bundle-dir` defaults to `.sonar/`.
- Extend `postgres-connector`: `discover_tables` and `discover_relationships` SHALL raise `ValueError` if any returned schema or table identifier contains a literal `.`. The guard keeps the bundle's `"<schema>.<name>"` key encoding unambiguous without a more verbose on-disk shape (see design D7).
- Version the bundle with a top-level `schema_version: 1` in `meta.json`. Bundle-wide event on bump.

## Capabilities

### New Capabilities
- `context-index`: Typed composition of discovered schema, LLM descriptions, and relationship edges; disk persistence as a versioned per-capability-file bundle under `.sonar/`.

### Modified Capabilities
- `postgres-connector`: `discover_tables` / `discover_relationships` reject identifiers containing `.` so the `context-index` on-disk key encoding stays unambiguous.

## Impact

- **Code:** New module `src/sonar/index/bundle.py` (dataclasses). Rewrite of `src/sonar/index/store.py` (I/O). Rewrite of `src/sonar/cli.py scan` command body.
- **Disk:** New `.sonar/` directory under the process CWD. Adds `.sonar/` to `.gitignore` (bundles contain database metadata; may contain LLM-generated descriptions of internal table names).
- **Dependencies:** None added. `json` (stdlib) is the only serialiser. Phase 1 scale does not warrant a format upgrade.
- **Downstream:** Unblocks `mcp-server` (#5). The bundle's on-disk shape is the contract `mcp-server` will parse.
- **Tests:** New unit tests for `ContextBundle` (frozen, JSON round-trip), `ContextStore` (write-then-read, partial-file recovery rules), and the `sonar scan` wiring (integration test against the already-running Docker fixture database).
