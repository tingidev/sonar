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

7. `inferred-relationships` — Enrich the naming heuristic with PK-column-name matching (canonical shared columns like `molregno`, `tid`, `record_id`) and role-prefix patterns (`enzyme_tid` → `tid`). Adds a precision filter for catch-all PKs (e.g. `version.name`). Extends `relationship-mapping`; keeps the existing binary `declared | inferred` kind. ChEMBL evidence: current `<col>_id` → `<table>` rule recovers 8.8% of declared FKs (8/91); the enriched rule recovers ~68% (62/91) at ~73% precision. The original line conflated this with value-overlap; that piece moved to Deferred below.
8. ~~`row-count-discovery`~~ — Populate `row_count` during schema discovery. (archived 2026-04-28 — `pg_class.reltuples`, no side effects on the user's DB)
9. `snowflake-connector` *(in flight)* — Snowflake data source adapter. Introduces connector abstraction if not already clean enough, then implements Snowflake-specific discovery (INFORMATION_SCHEMA queries, stage/pipe objects, Snowflake-native FKs). Requires `snowflake-connector-python` dependency.
10. `evaluation-toolkit` — Tools to measure how well agents navigate the context layer. Discovery accuracy, context relevance, coverage metrics. Depends on having two connectors and richer relationships to evaluate against.

### Phase 2 sequencing

- 9 depends on reviewing the connector abstraction (may need a refactor to make `postgres_connector` pluggable before adding Snowflake).
- 10 comes last — it evaluates what the other changes produce.

### Deferred (Phase 2+)

- `relationship-overlap-tiebreaker` — Use small-sample value overlap as a **disambiguator** when the enriched naming heuristic from #7 finds multiple same-schema PK candidates (e.g. `compound_records.molregno` could point at `molecule_dictionary` or `biotherapeutics`, both expose `molregno` as a PK). Estimated to recover most of the residual ~25pp recall gap on ChEMBL after #7 ships. Deferred because: (a) #7 alone may suffice for the schemas users actually bring; (b) per-pair value-sampling adds scan-time cost we shouldn't pay speculatively; (c) overlap-on-5-row-samples is asymmetric — it works as a positive tiebreaker but says nothing on absence, which only matters once we see real residual ambiguity. **Revisit when** a user (or `evaluation-toolkit` #10) surfaces a missing relationship whose cause is naming ambiguity rather than FK absence. Reversibility: cheap (additive scan-time pass).
- `connector-config-profiles` — `~/.sonar/profiles.toml` profile-config system mirroring `dbt`'s `profiles.yml` and `snowsql`'s `~/.snowsql/config`. Each profile names a connector (postgres/snowflake/...) and its full connection config; `sonar scan @profile-name` resolves it. Currently `snowflake-connector` (#9) ships with two auth paths: positional URL (password-only) and bare keyword `snowflake` reading a curated env-var set. **Revisit when** a user reports the env-var path is too painful for managing multiple Snowflake targets, or when `evaluation-toolkit` (#10) needs to iterate over a registered list of data sources in CI. Reversibility: cheap — the profile path is additive on top of URL + env vars.

## Rules

- One change in flight at a time. Propose, apply, archive — then start the next.
- `openspec/specs/` grows one capability at a time. After each archive, the accumulated spec is the source of truth for that capability's behaviour.
