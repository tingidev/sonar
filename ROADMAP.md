# Phase 1 Roadmap — Sonar MVP (May 2026)

## Milestone 1: Postgres Discovery

Connect to a real database and extract full schema.

- Implement `PostgresConnector.discover_tables()` — enumerate schemas, tables, columns, types, PKs
- Implement `PostgresConnector.discover_relationships()` — extract all FK constraints
- Implement `PostgresConnector.sample_table()` — fetch N sample rows
- Test against a real Postgres instance (local Docker or existing DB)

Depends on: nothing.

## Milestone 2: LLM Description Engine

Feed schema + samples to Haiku, get back semantic descriptions.

- Implement `LLMClient.generate()` — Anthropic Haiku calls
- Implement `DescriptionEngine.describe_table()` — prompt design for single table
- Implement `DescriptionEngine.describe_database()` — batch all tables efficiently
- Design the prompt (what makes a good semantic description?)

Depends on: Milestone 1 (needs real schema + samples as input).

## Milestone 3: Relationship Mapping

FK extraction + naming heuristic produces a relationship graph.

- Implement `RelationshipMapper.map_from_foreign_keys()` — direct FK to relationship
- Implement `RelationshipMapper.infer_from_naming()` — `user_id` to `users.id` pattern matching

Depends on: Milestone 1.

## Milestone 4: Context Index

Persist the full context (descriptions + relationships) as JSON.

- Define the JSON schema for the context index
- Implement `ContextStore.save()` / `ContextStore.load()`
- Wire the CLI `sonar scan` command end-to-end: connect, discover, describe, store

Depends on: Milestones 1-3.

## Milestone 5: MCP Server

Agent connects, queries the context, gets useful answers.

- Implement all 5 tools against the stored context index
- Wire `sonar serve` to start the MCP server
- Test with Claude Code as the MCP client

Depends on: Milestone 4.

## Milestone 6: Polish and Ship

Public-ready on GitHub.

- README with real usage examples
- CI (GitHub Actions: lint + test)
- One end-to-end demo (scan a sample DB, agent navigates it)

Depends on: Milestone 5.

## Notes

- Milestones 2 and 3 can run in parallel (both depend on 1, not each other)
- Each milestone gets a planner agent before implementation
- Timeline: ~4-5 focused sessions
