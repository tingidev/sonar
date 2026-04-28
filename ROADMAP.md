# Sonar Roadmap

Each line below is a planned OpenSpec change — propose with `/opsx:propose <name>` in order.

## Phase 1 — MVP (complete)

Postgres-to-MCP context pipeline, end-to-end.

1. ~~`postgres-schema-discovery`~~ — Connect to Postgres, enumerate tables/columns/PKs, extract FKs, sample rows.
2. ~~`llm-description-engine`~~ — Thin Anthropic wrapper and per-table semantic description generation.
3. ~~`relationship-mapping`~~ — FK-derived relationship graph plus naming-heuristic inference (`user_id` to `users.id`).
4. ~~`context-index`~~ — Persist discovered context (schema + descriptions + relationships) as JSON under `.sonar/`. Wire end-to-end `sonar scan`.
5. ~~`mcp-server`~~ — Expose 5 tools (discover, describe, relationships, search, sample) over MCP. Wire `sonar serve`.
6. ~~`release-polish`~~ — README examples, GitHub Actions CI (lint + test), one end-to-end demo.

## Phase 2 — Depth and breadth

Inferred relationships, second connector, evaluation toolkit.

7. `inferred-relationships` — Detect relationships from naming patterns (e.g. `user_id` to `users.id`) and column-value overlap when no FKs are declared. Extends the existing `relationship-mapping` capability. Current implementation only uses declared FKs.
8. ~~`row-count-discovery`~~ — Populate `row_count` during schema discovery. (archived 2026-04-28 — `pg_class.reltuples`, no side effects on the user's DB)
9. `snowflake-connector` — Snowflake data source adapter. Introduces connector abstraction if not already clean enough, then implements Snowflake-specific discovery (INFORMATION_SCHEMA queries, stage/pipe objects, Snowflake-native FKs). Requires `snowflake-connector-python` dependency.
10. `evaluation-toolkit` — Tools to measure how well agents navigate the context layer. Discovery accuracy, context relevance, coverage metrics. Depends on having two connectors and richer relationships to evaluate against.

### Phase 2 sequencing

- 7 and 8 are independent of each other and have no new dependencies. Start with either.
- 9 depends on reviewing the connector abstraction (may need a refactor to make `postgres_connector` pluggable before adding Snowflake).
- 10 comes last — it evaluates what the other changes produce.

## Rules

- One change in flight at a time. Propose, apply, archive — then start the next.
- `openspec/specs/` grows one capability at a time. After each archive, the accumulated spec is the source of truth for that capability's behaviour.
