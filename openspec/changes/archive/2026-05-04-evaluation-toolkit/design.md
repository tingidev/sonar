## Context

Sonar produces context artifacts across five capabilities: schema discovery, semantic descriptions, relationship mapping, bundle persistence, and MCP serving. Quality measurement today is ad-hoc — the inferred-relationships change (#7) measured recall/precision on ChEMBL in an explore session with throwaway code. No other capability has quantitative evaluation. Operators have no built-in signal for "how complete and useful is my context?"

The pipeline now has two connectors (Postgres, Snowflake), a two-rule relationship heuristic, and LLM-generated descriptions with confidence scores, semantic types, and PII classifications. All of these fields are already persisted in the bundle. The evaluation toolkit reads these artifacts and produces quality metrics — it never writes bundles or modifies the pipeline.

## Goals / Non-Goals

**Goals:**
- Reproducible, quantitative evaluation of the three core artifact types: relationships, descriptions, and search results.
- A zero-dependency bundle quality report any operator can run immediately after `sonar scan`.
- Regression detection between two bundles (before/after a change).
- A machine-readable output mode for scripting and CI integration.

**Non-Goals:**
- Agent-in-the-loop evaluation (multi-turn tool-calling traces). Requires an agent harness and task specification framework — different order of complexity. Revisit when the static evaluation primitives are stable.
- CI pass/fail gating. The toolkit produces metrics; threshold assertions are a downstream scripting concern.
- Multi-database benchmark aggregation. We have one real corpus (ChEMBL). Infrastructure for N databases is premature at N=1.
- Prompt optimization loops. Measuring quality is a prerequisite to optimizing it — this change builds the measurement layer only.

## Decisions

### D1: Module layout — `src/sonar/eval/` package

New package with one module per evaluation mode:
- `quality.py` — bundle quality report (coverage, reachability, confidence)
- `relationships.py` — relationship recall/precision against declared FKs
- `search.py` — search relevance against curated ground truth
- `diff.py` — structural bundle comparison
- `descriptions.py` — LLM-as-judge scoring
- `_report.py` — shared output formatting (JSON and human-readable)

The eval package imports from `sonar.connectors.types`, `sonar.index`, `sonar.relationships`, and `sonar.engine.llm` but never from `sonar.mcp`. Evaluation reads artifacts; it does not serve them.

Alternatives considered: (a) flat module `src/sonar/eval.py` — five modes in one file would exceed 400 lines by the second mode; (b) evaluation functions inside existing modules (e.g., relationship eval inside `relationships.py`) — conflates pipeline logic with measurement logic, muddies the import graph.

Revisit when: a sixth evaluation mode is added and the package structure no longer maps cleanly to one-module-per-mode.
Reversibility: cheap — internal package structure, no external consumers.

### D2: CLI shape — `sonar eval` with flag-based mode selection

```
sonar eval                                         # bundle quality (default)
sonar eval --relationships <dsn>                   # relationship recall/precision
sonar eval --search <ground-truth.yaml>            # search relevance
sonar eval --diff <bundle-b>                       # diff against another bundle
sonar eval --descriptions                          # LLM-as-judge
sonar eval --json                                  # machine-readable output (combinable with any mode)
sonar eval --bundle-dir <path>                     # override bundle location (default .sonar/)
```

Default mode (no flags) runs the bundle quality report. Flags select a different mode. `--json` is orthogonal and combinable with any mode. Only one evaluation mode per invocation — combining `--relationships` and `--search` in one call is rejected as unnecessary complexity.

`--diff` takes a single path argument — the "other" bundle. The "current" bundle is loaded from `--bundle-dir` (default `.sonar/`). This keeps the common case simple: `sonar eval --diff /path/to/old/.sonar/` compares against the current bundle.

Alternatives considered: (a) five subcommands (`sonar eval quality`, `sonar eval relationships`, etc.) — heavier CLI surface, harder to discover, same implementation; (b) positional mode argument — less self-documenting than flags.

Revisit when: mode count exceeds 6-7 and the flag list becomes unwieldy.
Reversibility: cheap — CLI surface is internal tooling, no external consumers parse the flag names.

### D3: Ground-truth format for search relevance — YAML

```yaml
queries:
  - query: "molecule"
    expected:
      - public.molecule_dictionary
      - public.compound_structures
      - public.compound_records
  - query: "target"
    expected:
      - public.target_dictionary
      - public.target_components
```

One file per database. Ships with a ChEMBL ground-truth file at `eval/chembl_search.yaml` (or similar path — exact location is an implementation detail). Expected tables use `schema.table` dot-encoding matching the bundle key format.

PyYAML dependency for parsing. Alternative: `tomllib` (stdlib in 3.11+) — avoids the dep but TOML's `[[array]]` syntax is less natural for lists of query-table mappings. PyYAML is stable, widely used, and the ground-truth file is the only consumer.

Revisit when: a second ground-truth format consumer appears and the YAML shape needs formalization beyond a simple list.
Reversibility: cheap — the YAML schema is internal. Changing it means updating one parser function and re-curating the ground-truth files.

### D4: Relationship evaluation — declared-FK hold-out methodology

Connect to the database via the appropriate connector, call `discover_tables()` and `discover_relationships()`. The declared FKs from `discover_relationships()` are the ground truth. Run `map_relationships(tables, [])` (empty FK list) to get pure-inference output. Compare inferred edges against declared edges.

Metrics: recall (what fraction of declared FKs were inferred), precision (what fraction of inferred edges match a declared FK), F1. Per-table breakdown and lists of missed/false-positive edges in the detailed output.

Matching rule: an inferred edge matches a declared FK when `(source_schema, source_table, source_column, target_schema, target_table, target_column)` is identical. Case-sensitive — the connector preserves case as-returned.

No separate ground-truth file needed — the database is the ground truth. This is the same methodology from the #7 explore session, now reproducible as a command.

Alternatives considered: (a) synthetic hold-out (hide a random subset of FKs, test recall on hidden ones) — more rigorous for measuring inference generalization, but adds complexity without a clear consumer for the statistical rigor; (b) external ground-truth file listing expected relationships — redundant when the database already declares them.

Revisit when: a database with known undeclared relationships needs evaluation (e.g., Snowflake where FKs are informational-only and often missing). At that point an external ground-truth file becomes necessary.
Reversibility: cheap — the methodology is a function signature, not a persisted format.

### D5: Graph reachability — BFS with connected-component summary

Build an undirected adjacency list from the bundle's relationships (both declared and inferred). For each table, BFS to count reachable tables. Report:

- Number of connected components
- Size of largest component (tables and fraction of total)
- Orphan tables (component size = 1, no relationships at all)
- Mean reachable tables per starting node

Undirected because an agent can traverse relationships in either direction (the `relationships` MCP tool accepts `direction=both`). O(V * (V + E)) worst case — trivially fast at our scale (< 1000 tables).

Alternative: directed reachability (outgoing only). Rejected because the agent's navigation model is bidirectional — following an incoming FK to find parent tables is a core use case.

Revisit when: table count exceeds 10K and the O(V^2) computation becomes noticeable. At that point, switch to connected-component sizes (O(V + E)) without per-node BFS.
Reversibility: cheap — internal metric computation.

### D6: Bundle diff — structural comparison by key

Load two bundles via `ContextStore.read()`. Compare:

- **Tables:** match on `(schema, name)`. Report added, removed. For matched tables, report column additions/removals and type changes.
- **Relationships:** match on the full 6-tuple `(source_schema, source_table, source_column, target_schema, target_table, target_column)`. Report added, removed. Group by kind (declared vs inferred) for clarity.
- **Descriptions:** match on `(schema, name)`. Report added, removed, null-to-present, present-to-null. For matched non-null descriptions: report text changes (changed/unchanged flag — not a text diff), confidence delta, grain change, domain_hints added/removed.

No deep text diffing of description bodies — the descriptions are LLM-generated and will always have minor wording variation between runs. The useful signal is "did the confidence change?" and "did the description go from null to present or vice versa?" For text, a boolean "changed" flag suffices.

Alternatives considered: (a) line-level diff of description text — noisy for LLM output, obscures the structural signal; (b) semantic similarity scoring — reintroduces an LLM dependency into what should be a pure-computation mode.

Revisit when: a user reports that description text changes are the primary regression signal and the boolean flag is insufficient.
Reversibility: cheap — adding richer comparison later is additive.

### D7: LLM-as-judge — structured rubric, advisory only

Reuse `sonar.engine.llm.LLMClient` (same Haiku model, same retry behaviour). For each table with a non-null description, send a scoring prompt containing:

- The table schema (columns, types, PKs)
- The generated description, grain, domain hints, column descriptions
- A rubric asking the judge to score three dimensions (0.0 - 1.0 each):
  - **Accuracy** — does the description correctly reflect the schema and column content?
  - **Completeness** — are all important aspects of the table covered?
  - **Specificity** — does the description add domain knowledge beyond what the column names say?

Judge returns JSON with three float scores per table. Aggregate mean across tables for each dimension. Tables scoring below 0.5 on any dimension are flagged in the report.

The rubric prompt lives in `src/sonar/eval/_prompts.py`, mirroring the description engine's prompt placement.

Implemented last. Advisory only — no pass/fail threshold in the toolkit. The scores are structured (JSON-diffable across runs) so bundle diff can track them over time if an operator stores eval results alongside bundles.

Row samples are NOT sent to the judge — the bundle doesn't persist samples (PII-off-disk posture). The judge scores based on schema + description only, which is the same information an agent would see through the MCP tools.

Alternatives considered: (a) human annotation — gold standard but doesn't scale and isn't automatable; (b) reference-based scoring (compare against a "gold" description) — requires writing gold descriptions, which is the task we're evaluating; (c) embedding similarity — less interpretable than dimension scores and still needs an LLM or embedding model.

Revisit when: the judge's inter-run variance is measured and exceeds 0.15 on repeated evaluations of the same bundle. At that point, multi-run averaging or a different rubric structure may be needed.
Reversibility: cheap — advisory mode with no downstream consumers parsing the scores.

### D8: Output format — human-readable default, `--json` for machines

Default output is a human-readable summary to stdout. `--json` flag switches to structured JSON to stdout. No stderr/stdout split — the eval command is a report generator, not a pipeline step.

JSON schema is mode-specific but follows a common envelope:

```json
{
  "mode": "quality",
  "bundle": ".sonar/",
  "metrics": { ... },
  "details": [ ... ]
}
```

`metrics` carries the aggregate numbers (coverage percentage, F1, mean confidence). `details` carries the per-table/per-query breakdown. Human-readable mode formats the same data as aligned text tables.

Revisit when: a third output format is requested (HTML, CSV).
Reversibility: cheap — output formatting is isolated in `_report.py`.

### D9: PyYAML dependency

Added as a main dependency (not an extra) because the evaluation toolkit is part of the core `sonar` package. Lightweight, stable, no security concerns for a file-parsing dependency.

Alternative: `tomllib` (stdlib). Avoids the dependency but TOML is less natural for the ground-truth schema (repeated `[[queries]]` blocks vs YAML's list syntax). Ground truth files are hand-curated; readability matters.

Alternative: JSON ground-truth files (stdlib). Loses readability — JSON requires quoting every key, no comments, nested arrays are visually dense. The file is meant to be edited by humans.

Revisit when: PyYAML causes a dependency conflict or security advisory.
Reversibility: cheap — swapping YAML for TOML means rewriting one parser function and converting the ground-truth files.

### D10: Implementation order

1. Bundle quality report — zero external dependencies, validates module structure and CLI wiring
2. Relationship evaluation — adds connector dependency, reuses existing `map_relationships`
3. Search relevance — adds YAML dependency and ground-truth loading
4. Bundle diff — adds cross-bundle comparison, pure computation
5. LLM-as-judge — adds LLM dependency, implemented last per explore-phase decision

Each mode is independently testable. Tests for modes 1-4 need no LLM client or API key. Mode 5 uses the same `FakeLLMClient` pattern from the description engine tests.

Revisit when: implementation reveals a dependency between modes that changes the optimal order.
Reversibility: cheap — implementation order is a plan, not a commitment.

## Risks / Trade-offs

**[LLM-as-judge noise]** The judge model may score inconsistently across runs. Mitigation: structured scores (not prose), advisory-only status, documented inter-run variance expectation. The explore conversation explicitly positioned this as the weakest signal — it catches the "technically correct but vague" failure mode that search relevance and bundle diff miss.

**[ChEMBL as sole corpus]** All evaluation metrics are calibrated against one database. ChEMBL is a canonical-name schema; app-style schemas (Rails/Django with `id` PKs) have known different characteristics. Mitigation: the toolkit accepts any database/bundle, not just ChEMBL. The ChEMBL ground-truth file is a shipped example, not the only option.

**[PyYAML dependency surface]** New runtime dependency for all users, not just eval users. Mitigation: PyYAML is mature and lightweight. If it becomes a concern, the ground-truth parser can be swapped to `tomllib` with no spec change.

**[Relationship evaluation requires live DB]** The `--relationships` mode needs a database connection to get declared FKs as ground truth. Operators without DB access can't run it. Mitigation: all other modes work against the bundle alone. An external ground-truth file format for relationships is the escape hatch if this becomes limiting (see D4 revisit trigger).

## Resolved Questions

- **Ground-truth file location:** `eval/` at project root. The ChEMBL file is a reference example and development artifact, not something pip users import programmatically. `sonar eval --search` expects users to bring their own ground-truth YAML for their own database. No `importlib.resources` boilerplate. If distribution demand surfaces (someone asks "can I pip install and run ChEMBL eval out of the box"), move to package data then.
- **Judge model:** Haiku judging Haiku is acceptable. The feature is advisory and primarily for regression detection — self-reinforcement bias cancels out in diffs (both bundles generated by the same model, bias is constant). Sonnet at 5x cost would make the feature too expensive for casual use, defeating the purpose. Document the limitation explicitly: "same-model judge — use for relative comparison between runs, not absolute quality assessment."
- **ChEMBL query count:** 20-30 queries. Exact number resolves during curation based on domain diversity.
