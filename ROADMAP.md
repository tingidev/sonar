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

## Phase 2 — Depth and breadth (complete)

Inferred relationships, second connector, evaluation toolkit.

7. ~~`inferred-relationships`~~ — Two-rule combined heuristic (direct PK-name match + role-prefix) with catch-all PK filter. Recall 8.8% to 68.1% at 92.5% precision on ChEMBL. Value-overlap piece deferred as `relationship-overlap-tiebreaker`. (archived 2026-04-28)
8. ~~`row-count-discovery`~~ — Populate `row_count` during schema discovery. (archived 2026-04-28 — `pg_class.reltuples`, no side effects on the user's DB)
9. ~~`snowflake-connector`~~ — Snowflake data source adapter. Shared connector types extracted, INFORMATION_SCHEMA discovery, optional dependency with dispatch-time guard, two-tier test strategy (fakesnow + live). (archived 2026-05-04)
10. ~~`evaluation-toolkit`~~ — `sonar eval` subcommand with five modes: bundle quality report (default), relationship recall/precision against declared FKs, search relevance against curated YAML ground truth, structural bundle diff, LLM-as-judge description scoring. Reads bundles only; never mutates the pipeline. Ships with a 26-query ChEMBL search ground-truth file. (archived 2026-05-04)

### Deferred (Phase 2+)

- `relationship-overlap-tiebreaker` — Use small-sample value overlap as a **disambiguator** when the enriched naming heuristic from #7 finds multiple same-schema PK candidates (e.g. `compound_records.molregno` could point at `molecule_dictionary` or `biotherapeutics`, both expose `molregno` as a PK). Estimated to recover most of the residual ~25pp recall gap on ChEMBL after #7 ships. Deferred because: (a) #7 alone may suffice for the schemas users actually bring; (b) per-pair value-sampling adds scan-time cost we shouldn't pay speculatively; (c) overlap-on-5-row-samples is asymmetric — it works as a positive tiebreaker but says nothing on absence, which only matters once we see real residual ambiguity. **Revisit when** a user (or `evaluation-toolkit` #10) surfaces a missing relationship whose cause is naming ambiguity rather than FK absence. Reversibility: cheap (additive scan-time pass).
- `connector-config-profiles` — `~/.sonar/profiles.toml` profile-config system mirroring `dbt`'s `profiles.yml` and `snowsql`'s `~/.snowsql/config`. Each profile names a connector (postgres/snowflake/...) and its full connection config; `sonar scan @profile-name` resolves it. Currently `snowflake-connector` (#9) ships with two auth paths: positional URL (password-only) and bare keyword `snowflake` reading a curated env-var set. **Revisit when** a user reports the env-var path is too painful for managing multiple Snowflake targets, or when `evaluation-toolkit` (#10) needs to iterate over a registered list of data sources in CI. Reversibility: cheap — the profile path is additive on top of URL + env vars.

## Phase 3 — Provider flexibility and connector breadth

Multi-provider LLM support, two additional connectors.

11. ~~`llm-multi-provider`~~ — Two-SDK dispatcher: `openai` SDK for OpenAI + any OpenAI-compat endpoint (Ollama, Groq, vLLM via `SONAR_LLM_BASE_URL`), `anthropic` SDK natively. Slash-prefix routing (`anthropic/model-id`), `--model` CLI flag, factory function as sole public entry point. (archived 2026-05-06)
12. ~~`duckdb-connector`~~ — DuckDB data source adapter. `asyncio.to_thread` wrapping the sync driver, schema enumeration over default-to-main, row counts via `duckdb_tables().estimated_size`, `read_only=True` for files (skipped for `:memory:`). (archived 2026-05-08)
13. `bigquery-connector` — BigQuery adapter. GCP credentials, dataset/table enumeration, sampling via `TABLESAMPLE`.

### Deferred (Phase 3+)

- `description-quality-push` — Better prompting, length calibration, multi-pass critique. Parked pending real-user feedback on current quality.

- `mcp-surface-hardening` — Three MCP-standard gaps identified in a review against the official MCP reference server implementations (May 2026, https://github.com/modelcontextprotocol/servers):
  1. **Parameter-level descriptions missing.** Tool descriptions in `server.py` are tool-level strings only. FastMCP supports `Annotated[str, Field(description="...")]` on parameters; adding these would make the tool manifest self-documenting to agents making cold calls — especially important for `describe` and `relationships` where `schema` has a specific meaning (PostgreSQL schema name, e.g. `"public"`) that agents cannot infer.
  2. **`SampleOutcome` Literal is incomplete.** `audit.py:16` defines `SampleOutcome = Literal["ok", "rejected_cap", "rejected_unknown_table", "db_error"]` but `sample_tool.py:49` emits `outcome="rejected_invalid_limit"` which is not in the Literal. Works at runtime; mypy/pyright will flag it. Fix: add `"rejected_invalid_limit"` to the Literal.
  3. **Bundle data fits the MCP Resource pattern better than Tools.** `discover` and `describe` expose static catalog content — the right MCP primitive for browsable, addressable content is a Resource (e.g. `sonar://schema/public/batch_records`), not a Tool. Tools are for actions; Resources are for content agents navigate. This would make the server composable with a wider range of MCP clients and allow agents to browse the catalog without burning tool-call budget. Requires FastMCP Resource support and a URI scheme design.
  Deferred because: (a) item 2 is low severity (runtime behaviour is correct); (b) items 1 and 3 have no external consumers yet to validate the right description language or URI scheme against; (c) item 3 is an additive surface change — the Tool surface continues to work after Resources are added. **Revisit when** the first external consumer integrates against the MCP server, or when evaluation surfaces agent confusion caused by thin tool descriptions. Reversibility: cheap for 1 and 2 (additive / type-only); moderate for 3 (new URI scheme becomes a public contract once external consumers depend on it).

## Rules

- One change in flight at a time. Propose, apply, archive — then start the next.
- `openspec/specs/` grows one capability at a time. After each archive, the accumulated spec is the source of truth for that capability's behaviour.
