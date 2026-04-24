## Why

Sonar's value to agents is realised only when the discovered context is reachable through the interface the agent ecosystem has converged on — MCP. Phases 1 M1–M4 build the bundle; without `sonar serve`, that bundle is inert, and the whole "data context for AI agents" thesis is unproven end-to-end. This change closes the loop: point Claude Code, Cursor, or any MCP client at a Sonar bundle and the agent can discover tables, read semantic descriptions, trace relationships, search, and sample live rows.

## What Changes

- New `sonar serve` CLI command: starts a FastMCP server over stdio, backed by a `.sonar/` bundle loaded at startup.
- Five MCP tools exposed: `discover`, `describe`, `relationships`, `search`, `sample`.
- Conditional tool registration — `sample` is registered only when operator passes a DSN. Bundle-only mode serves four tools with no credentials and unlocks shareable-bundle workflows (Layer 2 distribution) without retrofit cost.
- `sample` tool queries a live Postgres connection per call using `psycopg.sql.Identifier` composition, server-capped at 50 rows per call (default 5), rejecting over-cap requests rather than silently clamping.
- Default PII-in-flight policy: columns with `pii_risk=high` in the bundle's descriptions are null-stripped from sample results. An `--allow-pii` CLI flag on `sonar serve` restores full pass-through for operator-authorised environments.
- Structured audit logging under a dedicated `sonar.mcp.audit` logger for every sample invocation (schema, table, limits, row count — never row content), suitable for routing into GxP audit sinks.
- Shared `scrub_dsn()` helper extracted into `sonar/_dsn.py` so both `cli.py` (scan error path) and `mcp/sample_tool.py` (tool error path) strip credentials from surfaced exception messages.
- Startup failure modes are loud: missing bundle, corrupt bundle, or schema-version mismatch cause `sonar serve` to exit non-zero with a clear stderr message before the MCP handshake; a server that can't answer tool calls cleanly never starts.
- Uses `mcp.server.fastmcp.FastMCP` from the already-installed `mcp` SDK — zero new top-level dependencies.

## Capabilities

### New Capabilities
- `mcp-server`: Expose a Sonar bundle (and optionally a live DB) as MCP tools over stdio. Owns the tool surface, conditional registration, sample-call safeguards (LIMIT cap, PII stripping, identifier quoting, audit logging), and bundle-loading failure semantics.

### Modified Capabilities
- `description-engine`: Extend the `PIIRisk` enum with a fourth member `MEDIUM` so the classifier can express the "plausible PII" middle bucket that mcp-server's default sample-stripping policy treats as protected. Touches the enum definition, the classifier prompt, and the accumulated spec — an additive, forward-compatible change (existing `none`/`low`/`high` bundles still parse).

## Impact

- **New code**: `src/sonar/mcp/server.py`, `src/sonar/mcp/tools/bundle_tools.py`, `src/sonar/mcp/tools/sample_tool.py`, `src/sonar/mcp/audit.py`, `src/sonar/_dsn.py`.
- **Modified code**: `src/sonar/cli.py` (flesh out `serve` subcommand; import `scrub_dsn` from `sonar/_dsn.py` and replace the inline scrub in `_run_scan`); `src/sonar/engine/describe.py` (add `PIIRisk.MEDIUM`); `src/sonar/engine/_prompts.py` (document the `medium` bucket in the classifier system prompt and the expected-output example).
- **Dependencies**: Zero new top-level deps. `mcp ^1.0` is already pinned.
- **Downstream**: `.sonar/` bundle shape (`context-index` capability) becomes the MCP server's consumed contract — any future bundle shape change needs an MCP compatibility check.
- **Security surface**: First code path in Sonar that holds a DSN across an agent-facing boundary. All credential-containment decisions (DSN stays in process, scrubbing on error paths, audit logging) apply here first and set the pattern for future connectors.
