# Sonar Roadmap

Phase 1 goal: functional end-to-end Postgres-to-MCP context pipeline. Each line below is a planned OpenSpec change — propose with `/opsx:propose <name>` in order.

## Planned Changes

1. `postgres-schema-discovery` — Connect to Postgres, enumerate tables/columns/PKs, extract FKs, sample rows.
2. `llm-description-engine` — Thin Anthropic wrapper and per-table semantic description generation.
3. `relationship-mapping` — FK-derived relationship graph plus naming-heuristic inference (`user_id` to `users.id`).
4. `context-index` — Persist discovered context (schema + descriptions + relationships) as JSON under `.sonar/`. Wire end-to-end `sonar scan`.
5. `mcp-server` — Expose 5 tools (discover, describe, relationships, search, sample) over MCP. Wire `sonar serve`.
6. `release-polish` — README examples, GitHub Actions CI (lint + test), one end-to-end demo.

## Rules

- One change in flight at a time. Propose, apply, archive — then start the next.
- Changes 2 and 3 both depend on change 1 but not on each other. Pick whichever unblocks faster.
- `openspec/specs/` grows one capability at a time. After each archive, the accumulated spec is the source of truth for that capability's behaviour.
