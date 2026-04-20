# Sonar

Open-source data context layer for AI agents. Connects to data sources, auto-discovers schemas, generates semantic descriptions via LLM, exposes context through MCP.

## Project Structure

```
src/sonar/
  connectors/     # Data source adapters (Phase 1: Postgres)
    postgres.py   # Schema discovery, sampling, FK extraction
  engine/         # Context generation
    llm.py        # Thin LLM abstraction (Anthropic Haiku for dev)
    describe.py   # Semantic description generation
    relationships.py  # FK + naming heuristic relationship mapping
  index/          # Context storage
    store.py      # JSON file persistence (.sonar/ directory)
  mcp/            # Agent interface
    server.py     # MCP server with 5 core tools
  cli.py          # CLI entrypoint (scan, serve)
tests/
```

## Commands

```bash
poetry install          # Install dependencies
poetry run pytest       # Run tests
poetry run sonar scan   # Discover + describe a database
poetry run sonar serve  # Start MCP server
```

## Technical Decisions

- Python 3.11+, Poetry for dependency management
- Async throughout (psycopg3, MCP server)
- LLM calls go through `engine/llm.py` — single point of provider abstraction
- Context stored as JSON in `.sonar/` directory (versionable, human-readable)
- MCP tools: discover, describe, relationships, search, sample
- Relationships: FK extraction + naming heuristic (e.g., `user_id` → `users.id`)
- Discovery: schema introspection + 3-5 sample rows per table for LLM context

## Conventions

- Immutable data structures (frozen dataclasses)
- No mutation — return new objects
- Error handling at boundaries only (DB connections, LLM API calls)
- Tests in `tests/`, pytest, 80% coverage target
- Ruff for linting (line-length 100)
