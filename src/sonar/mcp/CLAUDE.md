# MCP Server

Agent-facing interface. Exposes the context bundle as MCP tools over stdio.

## Tool surface

Five tools, intentionally minimal:

| Tool | Bundle-only | Description |
|------|:-----------:|-------------|
| `discover` | Yes | List tables, optionally filtered by schema |
| `describe` | Yes | Full semantic description of a table |
| `relationships` | Yes | Foreign keys incident on a table |
| `search` | Yes | Substring search across names and descriptions |
| `sample` | No | Return live rows with PII redaction |

The first four are stateless reads over the JSON bundle — no database connection, no credentials. `sample` requires a live DSN and is only registered when one is provided. When a DSN is provided but the connector doesn't support live sampling, print a warning (not a silent degradation).

Resist tool sprawl. Adding a tool raises the cognitive load for every agent consumer. Justify new tools by demonstrating that existing tools cannot serve the use case.

## PII handling

During the scan, every column gets a PII risk classification (`none`, `low`, `medium`, `high`). The `sample` tool automatically nulls columns classified as `medium` or `high` before returning rows to the agent. The `--allow-pii` flag overrides this for operator-authorized environments.

PII stripping is non-negotiable in the default path. New tools that return data must respect the same classification.

## Audit logging

All `sample` tool invocations are logged to the `sonar.mcp.audit` logger via `emit_sample_audit` in `audit.py`. The audit record includes outcome, identifiers, and limits — never row content, query text, or credentials.

The `SampleOutcome` Literal in `audit.py` is the contract for downstream audit consumers. Every outcome string emitted by the tool must be a member of this type. When adding new rejection paths, add the outcome to the Literal first.

Current outcomes: `ok`, `rejected_cap`, `rejected_invalid_limit`, `rejected_unknown_table`, `db_error`.

## Error handling

Tools raise `ToolError` (from `bundle_tools.py`) for expected failures (unknown table, invalid input). This surfaces a clean error to the agent. Unexpected failures (DB connection errors) are caught, scrubbed of credentials via `scrub_dsn`, and re-raised as `ToolError`.

## Adding a new tool

1. Determine if it's bundle-only or requires a live connection
2. Implement in `tools/` — bundle tools in `bundle_tools.py`, live tools in their own file
3. Register in `server.py` (conditionally if it requires a DSN)
4. If the tool returns data, apply PII stripping
5. If the tool touches the database, add audit logging with a defined outcome type
6. Update the tool table in the project README

## Testing

- Bundle tools are tested with in-memory `ContextBundle` fixtures — no database needed
- `sample` tool tests use monkeypatched async connection functions
- `asyncio_mode = "auto"` — do not add `@pytest.mark.asyncio` decorators
- Test both happy paths and every `SampleOutcome` variant
