## Why

Sonar's entire value proposition — semantic descriptions, relationship graphs, MCP context — depends on a trustworthy machine-readable view of the underlying database. Without schema discovery there is no input to any downstream stage. Postgres is the chosen first connector because it covers the majority of analytical and operational workloads Sonar targets, and its `information_schema` exposes the metadata Sonar needs without vendor-specific extensions.

This is the foundational change of Phase 1. Every subsequent change in `ROADMAP.md` depends on it.

## What Changes

- Introduce a `postgres-connector` capability that Sonar uses to scan a live Postgres database.
- Provide three behaviours against a connected database:
  - Enumerate schemas, tables, columns, data types, nullability, default values, and primary-key membership.
  - Extract all foreign-key constraints, including composite FKs, with correct source/target column alignment.
  - Sample a small number of rows from a named table, with result values coerced to JSON-serialisable Python types suitable for LLM prompts.
- Establish an async connection lifecycle managed as an async context manager, using psycopg3.
- Establish the project's first integration-test harness: a local Dockerised Postgres with a seeded sample schema, plus pytest fixtures reusable by later changes.

## Capabilities

### New Capabilities

- `postgres-connector`: Connects to a Postgres database and exposes schema introspection, foreign-key extraction, and row sampling as three async methods on an `async`-context-managed connector.

### Modified Capabilities

None. `openspec/specs/` is empty; this change establishes the first capability.

## Impact

- **Code:** `src/sonar/connectors/postgres.py` (existing stub file will be implemented against the spec). New SQL-string module may be introduced under `src/sonar/connectors/` for readability.
- **Dependencies:** No new runtime dependencies — `psycopg[binary]` is already declared. `pytest-cov` to be added to dev dependencies for coverage measurement.
- **Infrastructure:** New `docker-compose.yml` at repo root providing a test Postgres on port 5433. New `tests/fixtures/init.sql` seeding a small e-commerce schema with at least one composite PK, one multi-hop FK chain, and a range of column types (UUID, TIMESTAMPTZ, NUMERIC, TEXT, SERIAL).
- **Tests:** New `tests/conftest.py` with a session-scoped async connector fixture; new `tests/test_postgres_connector.py` with integration tests marked `@pytest.mark.integration`. Tests requiring the live DB are skippable via `-m "not integration"`.
- **CLI:** No CLI surface exposed yet. `sonar scan` remains a placeholder until the `context-index` change wires it end-to-end.
- **Public API surface:** `PostgresConnector`, frozen dataclasses `Table`, `Column`, `ForeignKey`.
