## Context

Phase 1 M1–M4 produced a typed `ContextBundle` on disk under `.sonar/` (four per-capability JSON files governed by a single `schema_version`). Nothing consumes that bundle yet. `mcp-server` is the first consumer — point an MCP client at `sonar serve` and the agent gains discover / describe / relationships / search / sample tools over the bundle.

Two external constraints shape the design:

- **MCP client lifecycle.** Claude Code and Cursor launch MCP servers as child processes over stdio, one process per configured server. They re-list tools on connect. Lifecycle ownership sits with the client, not Sonar.
- **Pharma security posture.** `sample` is the first agent-facing code path that touches a live DB. Pharma operators face a compliance conversation the moment raw rows flow from a regulated DB into an agent context. The default mitigations must be defensible at that conversation; opting out is the operator's call, not the architecture's.

The change introduces Sonar's first code path that holds DB credentials across an agent boundary, and the first where the LLM-generated PII classifications in the bundle become an enforcement surface rather than descriptive metadata.

## Goals / Non-Goals

**Goals:**
- Wire `sonar serve` end-to-end against a `.sonar/` bundle over stdio MCP.
- Ship the four bundle-backed tools as a stateless, credential-free surface — preserves the Layer 2 artifact-sharing use case without retrofit cost.
- Ship `sample` with pharma-defensible defaults: server-side cap, identifier-safe composition, PII-high stripping, audit logging.
- Extract `scrub_dsn()` once, share it across `scan` and `serve` error paths.

**Non-Goals:**
- Multi-bundle serving. One `.sonar/` per process (matches context-index's one-bundle-per-DSN invariant).
- Live bundle reload. Re-scan ⇒ restart `sonar serve`. Matches the single-writer, no-concurrent-reader assumption from context-index D2.
- SSE / HTTP transports. Stdio only; other transports land when a named consumer asks.
- Auth, tenancy, session-scoped permissions. Layer 3 (hosted), deferred.
- Per-table allow/deny lists (`--allow-schemas`, `--deny-tables`). Phase 1.5; the spec shape does not preclude adding them.
- Pre-joined enriched tables in the MCP tool responses. Join happens at call time (context-index D1), trivial at Phase 1 scale.

## Decisions

### D1 — Framework: `mcp.server.fastmcp.FastMCP` (in-tree)

Use the `FastMCP` class from the already-installed `mcp ^1.0` package. Decorator-based tool registration, schemas derived from type hints, stdio transport out of the box.

Alternatives considered:
- **Low-level `mcp.server.Server`.** Gives manual control over `list_tools` / `call_tool` dispatch. Rejected because Phase 1 has a fixed, statically-known tool set; the extra control surface is ceremony.
- **`fastmcp` 2.0 (`jlowin/fastmcp`) as separate dep.** Adds server composition, auth, cloud-deploy helpers. Rejected for Phase 1 — none of those features are needed in bundle-over-stdio mode, and a new top-level dep is the kind of commitment we'd prefer to defer.

Revisit when: Layer 2 (multi-bundle composition) or Layer 3 (hosted auth) lands and fastmcp 2.0's surface becomes directly useful.
Reversibility: cheap (both libraries are decorator-based; swap is a mechanical refactor of the server bootstrap).
Speculative: no.

### D2 — Conditional tool registration by DSN presence

`sonar/mcp/server.py::build_server(bundle, dsn)` registers the four bundle-backed tools unconditionally and the `sample` tool only when `dsn is not None`. Same binary, one CLI flag, zero duplication. Bundle-only mode yields a stateless, credential-free server suitable for Layer 2 artifact sharing.

```python
def build_server(bundle: ContextBundle, dsn: str | None) -> FastMCP:
    app = FastMCP("sonar")
    app.tool()(partial(discover_tool, bundle))
    app.tool()(partial(describe_tool, bundle))
    app.tool()(partial(relationships_tool, bundle))
    app.tool()(partial(search_tool, bundle))
    if dsn is not None:
        app.tool()(make_sample_tool(bundle, dsn, allow_pii=...))
    return app
```

Alternatives considered:
- **Two subcommands (`sonar serve-bundle` / `sonar serve-live`).** Doubles CLI surface and documentation. Rejected — the conditional is three lines.
- **Always register `sample`; raise at call time if no DSN.** Tool appears in the list but fails when invoked. Worse agent UX; the tool list should be honest about what the server can do.

Revisit when: Layer 2 ships and the bundle-only mode grows its own flags (e.g. `--read-only-mode` becomes the primary path) — at that point the conditional may invert.
Reversibility: cheap (one branch in `build_server`).
Speculative: no — bundle-only mode is a named use case (Layer 2), not speculation.

### D3 — Bundle loaded once at startup; no live reload

`ContextStore(bundle_dir).read()` is called exactly once, before the MCP transport opens. The resulting `ContextBundle` is captured in tool closures. No mtime watching, no per-call re-read.

Alternatives considered:
- **Per-call re-read.** Always fresh after a re-scan, at the cost of ~4 file reads + JSON parse per tool call. Rejected — Phase 1 workflow is "scan, then serve"; staleness within one serve lifetime is not a real failure mode.
- **mtime-based reload.** Middle ground, but introduces a partial-write race against `sonar scan` (which writes four files non-atomically per context-index D2 trade-off). Rejected.

Revisit when: always-on writer patterns emerge (e.g. a scheduled re-scan cron on the same box as `sonar serve`).
Reversibility: cheap (reload wiring is a decorator-like pass-through; tool closures can accept a `get_bundle()` callable instead of a captured value).
Speculative: no.

### D4 — Startup failures are loud and pre-transport

Bundle load failures (missing directory, `BundleIntegrityError`, `BundleVersionError`) abort `sonar serve` with a clear stderr message and non-zero exit code **before** the MCP handshake begins. An MCP server that 500s on every call is worse than one that never started.

Revisit when: operators need a "degraded mode" server (e.g. serve `discover` even if `descriptions.json` is corrupt). No current consumer.
Reversibility: cheap (move error handling inside server main loop).
Speculative: no.

### D5 — Sample tool row-count caps: DEFAULT=5, MAX=20, reject-don't-clamp

`sample` honours a caller-supplied `limit` subject to a hard server-side cap of **20**. Calls with `limit > 20` are **rejected** with a clear tool-level error; they are not silently clamped. The default (when `limit` is omitted) is **5**, matching the description-engine's sampling N.

Rationale:
- Five rows is enough to disambiguate table shape, not enough for meaningful exfiltration.
- Twenty rows is the upper bound for "pattern recognition without becoming a data-pull pipe" in a pharma context where process IP and patient data may both live in the target DB. Tighter than a generic `50` to keep the cap defensible in the compliance conversation: at twenty rows per call, even a compromised agent loop burns a lot of calls before extracting anything structurally interesting, and every call is audited.
- Rejecting rather than clamping lets the agent learn from the error and self-correct; silent clamp masks the cap and invites subsequent higher-limit attempts.

Rejected alternatives:
- **No cap** — unacceptable pharma posture.
- **Higher cap (50, 100)** — defensible in a generic dev-data context, harder to defend in pharma where the target DB may hold regulated data the operator didn't pre-classify. Start tight; raise if an operator reports a legitimate workflow that twenty breaks.
- **Soft clamp with warning header** — MCP tool responses are payloads, not HTTP-style headers; there's no well-known channel for warnings. Reject via tool error is the idiomatic MCP shape.

Revisit when: an operator reports that 20 is insufficient for a legitimate workflow, or that even 20 is too permissive for their sensitivity level. At that point, either raise the cap or make it operator-configurable per-serve.
Reversibility: cheap (two integer constants in one module).
Speculative: no.

### D6 — PII-in-flight policy: strip `pii_risk` ∈ {`high`, `medium`} by default, `--allow-pii` to bypass

The `sample` tool's default behaviour is to replace values in columns flagged `pii_risk=high` **or** `pii_risk=medium` in the bundle's `ColumnDescription` with JSON `null`. Row shape is preserved — the same column appears in every row, PII-flagged columns are uniformly null. An `--allow-pii` flag on `sonar serve` restores full pass-through.

The threshold covers both `high` and `medium`, excluding `low` and `none`. Rationale:
- Pharma is the named target audience, and pharma deployments may hold patient data (PHI) — the most sensitive data class we deal with. A false negative at deployment time (the LLM classified a patient-identifier column as `medium` instead of `high`) is a regulatory incident; a false positive (the LLM classified a generic field as `medium` when it's harmless) is operator friction routed around via `--allow-pii`. The asymmetry of consequences dictates the asymmetry of defaults.
- The description engine's `medium` bucket is intentionally a "plausible PII" catch-all. It is added as part of this change (see Modified Capability in the proposal — `PIIRisk` gains `MEDIUM` alongside the existing `NONE`/`LOW`/`HIGH`), so the default threshold has a value to match. Treating it as protected means we fail toward safety when the classifier is uncertain. Operators who verify the classifier is overcautious in their domain set `--allow-pii` — an informed opt-out with a clear audit trail (the flag is visible in the serve invocation).
- `low` and `none` stay pass-through. `low` is the classifier's "probably not PII but mentioning for completeness" bucket; conflating it with hard protection would make `--allow-pii` the default operator reflex, which defeats the mitigation.
- The choice is documented in spec as "a documented threshold" and named concretely here; if the description engine's taxonomy later expands (e.g. `regulated`, `health_identifier`), the threshold rule lives here in one place.

The flag lives on `sonar serve`, not per-call, because:
- Per-call flagging lets the agent turn off its own safeguards. Unacceptable.
- Per-serve is the operator's explicit consent point, matching the existing pattern for DSN containment (operator starts the process, agent operates within).

Rejected alternatives:
- **V1 pass-through.** Unacceptable pharma default.
- **V3 configurable per-level policy.** No named consumer for the flexibility; freeze-discipline defers until one arrives.

Revisit when: first real pharma/regulated deployment reports either false positives (legitimate fields getting stripped) or false negatives (sensitive fields slipping through). At that point, either tune the description engine's classification or introduce a richer policy (V3).
Reversibility: **expensive** — this is the tool's return contract. Changing default behaviour later means agents built against the current defaults start seeing data that wasn't there before, which is a security-visible change. The `--allow-pii` escape hatch gives operators a reversal path today without breaking the default's promise.
Speculative: no — pharma is the named consumer context.

### D7 — Identifier safety is a spec-level requirement, not a guideline

`sample`'s SQL composition uses `psycopg.sql.Identifier` for both `schema` and `table` — elevated from implementation guideline to spec requirement (see delta spec). Rationale: agent-controlled arguments flow directly into SQL; a regression here is a SQL-injection vector. Making it a spec requirement means an audit against the spec catches any future refactor that would substitute `f""`-string composition.

Corollary: `sample`'s SQL template is authored using `psycopg.sql.SQL("SELECT * FROM {}.{} LIMIT {}").format(Identifier(schema), Identifier(table), Literal(limit))` (or the equivalent idiom). The `LIMIT` value is bound via `Literal`, not interpolation, for consistency.

Revisit when: never — this is a hard security contract.
Reversibility: expensive (would require loosening a spec requirement that protects a known attack surface).
Speculative: no.

### D8 — `scrub_dsn()` helper extracted to `sonar/_dsn.py`

The DSN-scrubbing pattern introduced in `cli.py`'s scan error path (audit F1 fix from `context-index`) has its second concrete consumer in `sample`'s error path. Extract now, per freeze discipline ("minimum interface for the next consumer" — the consumer is here).

```python
# sonar/_dsn.py
def scrub_dsn(message: str, dsn: str) -> str:
    """Replace occurrences of `dsn` in `message` with its password-stripped label."""
```

`cli.py::_run_scan` and `mcp/sample_tool.py` both use it. `format_database_label` stays in `sonar/index/bundle.py` (it's about bundle metadata shape), imported by `scrub_dsn` from there. The `_dsn.py` module name is chosen over `sonar/security.py` — scrubbing is narrow, a broader security module invites scope creep.

Revisit when: a third credential-containing boundary appears (e.g. a config file holding API keys, a future MCP authentication layer).
Reversibility: cheap (one helper, two callers, clear rename path).
Speculative: no.

### D9 — Audit logging: `sonar.mcp.audit` logger, structured records, no row content

Every `sample` call (successful or rejected) emits exactly one record to `sonar.mcp.audit` with an `extra` dict of structured fields. Generic server-ops logging (tool dispatch, startup) goes to `sonar.mcp`. The separation lets operators route the audit logger to a separate sink (file, syslog, Splunk) without capturing noise.

Record fields:
- `tool` — always `"sample"` in Phase 1
- `schema`, `table` — the requested target
- `limit_requested`, `limit_effective` — the caller's request and what was applied (the latter is `null` on rejection)
- `rows_returned` — integer, or `null` on rejection / connection failure
- `outcome` — `"ok"` / `"rejected_cap"` / `"rejected_unknown_table"` / `"db_error"`

Record fields explicitly **excluded**:
- Row content, column values, query text beyond identifier names.
- DSN or any credential fragment (`scrub_dsn` applied to error strings before they reach the audit sink too).

Revisit when: operators need per-tool audit (describe, relationships, search) for a compliance framework beyond the sample-specific surface. Then generalise the logger name to `sonar.mcp.audit` covering all live-backed tools, and extend to bundle-backed tools only if the framework requires it.
Reversibility: cheap (logger name is a string; fields are additive).
Speculative: no.

### D10 — Module layout under `src/sonar/mcp/`

```
src/sonar/mcp/
  __init__.py
  server.py          # build_server(bundle, dsn) → FastMCP; run_stdio(server)
  audit.py           # audit logger + record helper
  tools/
    __init__.py
    bundle_tools.py  # discover, describe, relationships, search — pure functions of (bundle, args)
    sample_tool.py   # make_sample_tool(bundle, dsn, allow_pii) → callable; imports psycopg
src/sonar/_dsn.py    # scrub_dsn — shared with cli.py
```

Decisions within:
- **Tools are plain functions**, not classes. Testable without an MCP client — import the function, call with a bundle and args, assert the return.
- **`sample_tool.py` isolates `import psycopg`** inside `make_sample_tool`. Bundle-only mode never imports psycopg at server-build time. Phase 1 this is aesthetic; it's also a seam for a future slimmer distribution if bundle-sharing users don't want the DB driver.
- **`bundle_tools.py` is one file**, not four. The four tools are small (5–30 LOC each) and share the same closure-over-`bundle` pattern; one file keeps the diffs tight and the imports minimal. Split when one grows past ~80 LOC.

Revisit when: a tool's implementation exceeds ~80 LOC or needs its own helpers that don't belong in a sibling tool.
Reversibility: cheap (module splits are mechanical).
Speculative: no.

### D11 — `sonar serve` CLI shape: `--bundle-dir` option, optional positional DSN

```
sonar serve [--bundle-dir PATH] [--allow-pii] [DSN]
```

- `--bundle-dir` defaults to `.sonar/`, matching `sonar scan`.
- DSN is a positional argument, absent means bundle-only mode. No `--url` alias (unlike `scan`), because `serve` doesn't offer the same operator-ergonomics story — the DSN is scripted into MCP client config, not typed at a shell prompt.
- `--allow-pii` is a bare flag (off by default); there is no `--allow-pii=true|false`. Boolean presence is unambiguous and avoids accidental double-negatives in scripts.

Revisit when: a second transport (SSE, HTTP) lands and its bootstrap needs different CLI shape.
Reversibility: cheap (argparse).
Speculative: no.

### D12 — Tests: unit for tools, integration for server

- **Unit**: each tool function is tested against a hand-built `ContextBundle` fixture. No MCP client involved. Fast, deterministic.
- **Integration**: one test per mode (bundle-only, live) that starts `build_server(...)`, inspects the registered tool list, and calls each tool via the FastMCP in-process test harness. The live-mode test uses the existing `TEST_DATABASE_URL` fixture (docker compose Postgres on `:5433`), guarded by `@pytest.mark.integration`.
- **Sample tool tests**: PII-strip behaviour verified against a fixture bundle with known `pii_risk=high` columns; cap-reject and cap-accept paths with parametrised `limit` values; error-scrub verified by triggering a psycopg connection failure against `127.0.0.1:1`.

Revisit when: an SSE/HTTP transport needs a separate integration harness.
Reversibility: cheap.
Speculative: no.

## Risks / Trade-offs

- **Risk: `sample` with `--allow-pii` becomes the normal operator posture and the default stripping never fires.** → Mitigation: D6's choice of exactly `pii_risk=high` as threshold keeps false-positives low, reducing the incentive to flip the flag. Monitor deployments; revisit if `--allow-pii` is universally set.
- **Risk: PII classifier (`description-engine`) mislabels a sensitive column as `low` or `medium`, so `sample` exposes it even in default mode.** → Mitigation: accepted trade-off. The classifier is best-effort LLM output; hard protection requires the operator to use `--deny-tables` / `--allow-schemas` (Phase 1.5). Document this explicitly in the README so operators don't over-trust defaults.
- **Risk: Bundle-shape change (new `schema_version`) breaks `sonar serve` without a migration path.** → Mitigation: D4 guarantees loud startup failure via `BundleVersionError`; re-scanning with a matching Sonar version is the path.
- **Risk: Agent learns to circumvent the cap by making many smaller calls.** → Mitigation: accepted. The cap is a per-call shape guarantee, not a rate limit. Rate limiting is out of scope for Phase 1; if the deployment risk profile demands it, it belongs in a separate capability.
- **Trade-off: Bundle-only mode advertises four tools, live mode advertises five. Agents built against one mode see a different tool list.** → Accepted. MCP's `list_tools` is dynamic by design, and this is the idiomatic use of that dynamism. Agents that need `sample` can detect its absence and fall back to describe-only workflows.
- **Trade-off: `sample` joins PII classifications from the bundle at call time. If the bundle is stale relative to the live DB (new column added since last scan), the new column has no classification and passes through.** → Accepted. The known failure mode is a column exists live but is absent from the bundle; in that case the row dict includes the new column unfiltered. Operators re-scan to close the gap. Documented in the README.

## Migration Plan

- No existing `sonar serve` functionality to migrate — the current subcommand is a stub that prints "Starting Sonar MCP server...".
- `sonar/cli.py::_run_scan` loses its inline DSN-scrub replacement in favour of `scrub_dsn` from `sonar/_dsn.py`. Test in `tests/test_scan.py::test_unreachable_db_exits_nonzero_and_writes_nothing` continues to pass unchanged — the scrubbing behaviour is preserved, only the implementation moves.
- `tests/test_cli.py` (if present) adds a `sonar serve --help` smoke test. A minimal integration test verifying bundle-only mode's four-tool list lands in `tests/test_mcp_server.py`.

## Open Questions

- **Should `describe` also surface the bundle's relationships touching the table?** Arguably yes (one call for the full picture), but the join composes two collections at call time and the `relationships` tool already handles this shape. Lean: keep `describe` = tables ⋈ descriptions only; agents compose `describe` + `relationships` if they want the full view. Settle during implementation if the in-test agent UX feels noisy.
- **Does `search` need a ranking tiebreaker beyond match-type priority?** Table-name match > column-name match > description-body match gives a three-tier ordering; within a tier, lean alphabetical on `(schema, table)` for determinism. Confirm when writing the search test.
- **`--audit-log` flag to route `sonar.mcp.audit` to a specific file.** Deferrable — Python logging config is the existing escape hatch. Revisit when an operator asks for the one-flag path.
