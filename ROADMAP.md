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
13. ~~`bigquery-connector`~~ — BigQuery adapter. REST API for schema discovery (regions-agnostic), `Semaphore(20)` per-table fan-out via `_discover_table`, per-dataset INFORMATION_SCHEMA for FK/PK with per-dataset failure isolation, ADC-only auth, `SELECT * LIMIT N` sampling. (archived 2026-05-11)

### Deferred

- `mcp-surface-hardening` — Parameter-level descriptions on tools, MCP Resource pattern for catalog content. Revisit when first external consumer integrates. SampleOutcome Literal gap fixed in Phase 3 cross-cutting audit.

## Phase 4 — Quality, drift, and developer experience

Ship-ready Sonar. Three workstreams executed in order: foundation, differentiator, stickiness. Exit criterion: all three workstreams archived against their own scope.

14. ~~`first-run-experience`~~ — done, archived 2026-05-11. Polished scan output. New `scan-output` capability owns rendering; `description-engine` gained an optional `on_progress` callback so the engine stays output-agnostic. Streaming per-table lines (`[i/N] schema.table ... ok (2.1s)`), inline failure reasons, final summary with success/failure counts and retry guidance, integrated cross-DB/cross-dataset FK warnings.

15. `description-quality-push` — Stress-test descriptions against six public databases of varying messiness, improve prompts and sampling strategy, measure via the eval toolkit. Core differentiator.

    **Stress-test database suite** (fixtures under `tests/fixtures/`):
    - **ChEMBL** (Postgres, port 5434) — clean baseline. 73 tables, declared FKs. Regression target.
    - **FAERS** (Postgres, port 5435) — FDA adverse events. 7 tables, max abbreviation mess (`ae_pt`, `role_cod`, `i_f_cod`), no FKs, medical jargon.
    - **TPC-DS** (Postgres, port 5436) — synthetic retail. 24 tables, systematic 2-letter prefixes (`ss_sold_date_sk`), wide fact tables.
    - **AdventureWorks** (Postgres, port 5437) — enterprise multi-schema. 68 tables across 5 schemas, audit/junk mixed with real.
    - **Lahman** (Postgres, port 5438) — baseball stats. 27 tables, mixed naming (`playerID` next to `GIDP`, `BFP`).
    - **CMS SynPUF** (DuckDB) — Medicare claims. 5 tables, all-caps abbreviated columns (`BENE_ESRD_IND`, `AT_PHYSN_NPI`).

    **Eval framework** (extends `sonar eval` description-scoring mode):
    - **Cross-provider judging.** Generator is the model under test (Haiku, local Qwen, etc.); judge is from a different provider (GPT-4o) to eliminate same-family bias. New `--judge-model` flag routes through the existing `create_llm_client()` factory.
    - **Dimensions, each 1-5 with reasoning:** accuracy (correct reflection of content), specificity (useful detail, not generic filler), domain inference (correct domain identification and terminology).
    - **Iteration metric: relative.** Per-table score deltas vs previous run. Improvement = positive delta on the fixed sample.
    - **Exit metric: absolute.** Per-database thresholds, set as baseline data comes in. All six databases must clear their threshold before this workstream archives.
    - **Sample strategy:** fixed 10 tables per database for iteration loops (comparable across runs); periodic full-database eval to guard against overfitting.
    - **Artifacts.** Each run writes a versioned JSON: scores, judge reasoning, prompt version, generator + judge model versions, sample tables. Committed to repo for reproducibility.
    - **Future extension (deferred):** LLM-as-jury — both Sonnet and GPT-4o judge; disagreements flag genuinely ambiguous descriptions for human spot-check. Doubles cost; start with single judge.

16. `schema-drift` — Keep descriptions in sync as schemas evolve. Makes Sonar a tool you keep running, not a one-shot.

    **Three commands:**
    - `sonar scan` — unchanged. Full scan, re-describes everything. First-run case.
    - `sonar rescan` — smart re-scan. Re-describes only tables whose column structure changed OR whose stored description was generated with a different model/prompt version. `--force` overrides to full re-describe.
    - `sonar diff` — comparison output, no writes. Two modes:
        - **Bundle vs live DB:** "What would change if I rescanned?" Common case.
        - **Bundle vs bundle:** Compare two stored snapshots.
        - Output: markdown by default (terminal/PR-friendly), `--format=json` for piping.

    **Schema diff scope:** tables added/removed, columns added/removed/renamed/typed. Row-count delta surfaces as a warning section, not a structural change. Sample data drift is not detected.

    **Re-describe triggers (smart rescan):** column structure change, OR stored description's model/prompt version mismatches current config. Bundle records `model` and `prompt_version` per description for this check.

    **Bundle history is user-managed.** Sonar produces comparable bundles; storing them is the user's job (git, backup folder, CI artifact). No internal version directory or symlinks.

    **Out of scope:** semantic-staleness detection without schema changes (e.g., a `status` enum growing from 3 to 12 values whose description still says "3 statuses"). Phase 5+ if real usage surfaces it.

### Deferred (Phase 4+)

- `mysql-connector` — MySQL/MariaDB adapter. Follows established connector patterns. Contribution-friendly.
- `sqlite-connector` — SQLite adapter. Trivial scope, good first-contributor target.
- `connector-config-profiles` — `~/.sonar/profiles.toml` for managing multiple connection targets. Revisit when users report env-var path is too painful.
- `relationship-overlap-tiebreaker` — Value-overlap disambiguator for naming-ambiguous FK candidates. Revisit when eval surfaces missing relationships caused by naming ambiguity.

## Rules

- One change in flight at a time. Propose, apply, archive — then start the next.
- `openspec/specs/` grows one capability at a time. After each archive, the accumulated spec is the source of truth for that capability's behaviour.
