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

## Usage (planned)

```bash
# Scan a database and generate context
sonar scan postgresql://user:pass@localhost/mydb

# Start the MCP server
sonar serve
```

## Development

```bash
poetry install
poetry run pytest
```

## License

Apache-2.0
