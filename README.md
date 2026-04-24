# Sonar

Data context for AI agents. Auto-discovers database schemas, generates semantic descriptions, and exposes everything as MCP tools — so agents understand your data without manual configuration.

## Status

Early development (Phase 1). Not yet functional.

## Architecture

```
Data Source (Postgres)  →  Context Engine  →  MCP Server
  schema discovery           LLM descriptions      sonar/discover
  sample rows                relationship map      sonar/describe
  foreign keys               context index         sonar/relationships
                                                   sonar/search
                                                   sonar/sample
```

## Usage

```bash
# Scan a database and generate a context bundle under .sonar/
sonar scan postgresql://user:pass@localhost/mydb

# Start the MCP server
sonar serve
```

## Start the MCP server

`sonar serve` exposes a previously-scanned `.sonar/` bundle as MCP tools over stdio.

**Bundle-only mode** — stateless, credential-free. Four tools: `discover`, `describe`, `relationships`, `search`.

```bash
sonar serve --bundle-dir .sonar/
```

**Live mode** — adds the `sample` tool, which opens short-lived connections to the DSN per call.

```bash
sonar serve --bundle-dir .sonar/ postgresql://user:pass@host/db
```

`sample` enforces a hard cap (20 rows max, 5 by default) and strips values from columns whose `pii_risk` classification in the bundle is `high` or `medium`. Columns without a classification in the bundle — for example, columns added to the live DB after the last `sonar scan` — pass through unredacted; re-scan to close the gap.

**Bypass PII stripping** with `--allow-pii` in operator-authorised environments:

```bash
sonar serve --bundle-dir .sonar/ --allow-pii postgresql://user:pass@host/db
```

Warning: `--allow-pii` causes `sample` responses to include raw values from columns the LLM classified as `high` or `medium` PII risk. Every `sample` call is audited to the `sonar.mcp.audit` logger regardless of this flag.

## Development

Sonar uses spec-driven development via [OpenSpec](https://github.com/Fission-AI/OpenSpec). Feature work starts with a proposal in `openspec/changes/`, not code. See `CLAUDE.md` for the workflow and `ROADMAP.md` for planned changes.

```bash
poetry install
poetry run pytest
```

## License

Apache-2.0
