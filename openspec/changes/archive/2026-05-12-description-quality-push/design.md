## Context

Sonar's description engine has a single system prompt (`_prompts.py`) designed against ChEMBL -- a clean, well-structured biomedical database. The existing `sonar eval --descriptions` mode runs an LLM-as-judge using the same model family as the generator, scoring accuracy/completeness/specificity on a 0-1 float scale without reasoning. There is no way to compare runs across prompt iterations, and no infrastructure for fixed-sample iteration loops.

Five new database fixtures (FAERS, TPC-DS, AdventureWorks, Lahman, CMS SynPUF) are already staged in the working tree with Docker configs and seed scripts.

## Goals / Non-Goals

**Goals:**
- Measure description quality across six databases covering the major messiness patterns (abbreviations, wide fact tables, multi-schema, mixed naming, coded columns)
- Enable iterative prompt improvement with comparable, versioned eval runs
- Use cross-provider judging to eliminate same-family scoring bias
- Improve the description engine's prompts and sampling until all six databases clear quality thresholds
- Produce debuggable output (per-dimension judge reasoning)

**Non-Goals:**
- Changing the `TableDescription` or `ColumnDescription` dataclass shapes (those are stable)
- Adding new eval modes beyond descriptions (relationship, search, diff, quality are unchanged)
- Automated CI eval pipeline (manual iteration this change; CI later)
- Multi-jury (Sonnet + GPT-4o both judging) -- deferred, doubles cost, start with single judge
- Changing the description engine's retry/concurrency architecture

## Decisions

### D1: Cross-provider judging via --judge-model flag

The judge model is separate from the generator model. `--judge-model` routes through the existing `create_llm_client()` factory. Default judge is the existing `--model` value (backward-compatible). When `--judge-model` is set, the generator model is whatever produced the bundle's descriptions; the judge uses the specified model.

Alternative: hard-code judge to GPT-4o. Rejected -- the factory already supports any model, and users may want to judge with different models.

Revisit when: multi-jury mode is needed (multiple judges for disagreement detection).
Reversibility: cheap

### D2: Three refined dimensions with 1-5 integer scoring and reasoning

Replace the three existing dimensions (accuracy, completeness, specificity) with:
- **Accuracy** (1-5): correct reflection of content -- does the description match what the schema and samples show?
- **Specificity** (1-5): useful detail, not generic filler -- would this description help distinguish this table from others?
- **Domain inference** (1-5): correct identification of the table's domain and use of appropriate terminology

Each dimension returns an integer score (1-5) and a reasoning string. The integer scale is easier to threshold than 0-1 floats and maps naturally to rubric-style prompting. Reasoning is stored in the eval artifact for debugging but not displayed in the console summary.

Alternative: keep 0-1 float scale. Rejected -- 1-5 with explicit rubric anchors produces more stable LLM scores across models and is standard in LLM-as-judge literature.

Alternative: merge accuracy and domain-inference into a single dimension. Held in reserve. Conceptually they are distinct (a description can be schema-faithful but domain-blind, e.g. correctly listing column types while missing that the table is a medical adverse-event report), but the dimensions may correlate tightly in practice -- a description that misidentifies the domain is likely also flagged as inaccurate by the same judge. Decision deferred to baseline data (see Open Questions).

Revisit when: baseline data shows Pearson correlation between accuracy and domain-inference >= 0.85 across all six databases, indicating the dimensions are not measuring distinct things; or a dimension proves too coarse to distinguish good from bad descriptions.
Reversibility: cheap

### D3: Versioned JSON eval artifacts

Each eval run writes a JSON file to an `eval-runs/` directory (gitignored by default) with: run timestamp, generator model, judge model, prompt version hash, per-table scores with reasoning, aggregate metrics, and the list of tables evaluated. The prompt version hash is a SHA-256 of the system prompt + table prompt template, providing automatic change detection without manual version bumping.

Alternative: append to a single JSONL file. Rejected -- individual files are easier to diff and commit selectively.
Alternative: version via a manual counter in `_prompts.py`. Rejected -- hash is automatic and never stale.

Revisit when: run count grows large enough that directory listing is slow (hundreds of runs).
Reversibility: cheap

### D4: Fixed sample via --sample flag, round-robin across schemas

`sonar eval --descriptions --sample N` evaluates a deterministic subset of N tables (default: all). Selection round-robins across schemas: sort tables within each schema by name, then interleave by schema (in sorted schema order) until N tables are reached. For single-schema databases this degenerates to sorted top-N. For multi-schema databases (AdventureWorks: 5 schemas, 68 tables) this ensures the sample exercises every domain the schemas represent. Repeated runs evaluate the same tables. The `--sample` flag is orthogonal to `--judge-model`.

Alternative: sorted top-N across all `(schema, table)` keys. Rejected -- for AdventureWorks, sorted top-10 yields all `HumanResources.*` tables and zero from Production/Sales/Person/Purchasing, so prompt iteration optimises for one domain and silently degrades the others. Round-robin is determinism-preserving and trivial to implement.

Alternative: random sample with seed. Rejected -- comparability between iterations matters, and randomness adds a seed-management burden for no benefit when the deterministic round-robin already covers diversity.

Revisit when: a single schema is so large (or so internally diverse) that even round-robin gives an unrepresentative slice, requiring stratification by row count, table type, or column count.
Reversibility: cheap

### D5: Prompt improvements are iterative, not pre-designed

Rather than designing prompt changes upfront, the workflow is: establish baseline with current prompts, identify weak patterns from judge reasoning, improve prompts, re-evaluate. The specific prompt changes cannot be specified in advance because they depend on what the baseline reveals.

The description engine's system prompt (`SYSTEM_PROMPT`) and table prompt (`build_table_prompt`) are the two iteration surfaces. Sampling strategy (how many rows, which rows) is a third knob.

Revisit when: prompt iteration stalls (scores plateau without reaching thresholds).
Reversibility: cheap

### D6: Exit thresholds set after baseline

Per-database thresholds for the exit metric cannot be set before seeing baseline scores. The workflow is: run baseline, set thresholds at baseline + reasonable improvement margin, iterate until met. Thresholds are documented in `design.md` Open Questions and updated as a decision once baselines are in.

Revisit when: baselines are in.
Reversibility: cheap

(Future decision: per-database thresholds will be added once baseline scores are available.)

### D7: CMS SynPUF uses DuckDB connector, not Postgres

CMS SynPUF is a DuckDB fixture (Parquet files loaded via `setup.sh`), not a Docker Postgres service. It exercises the DuckDB code path and adds connector diversity to the stress test.

Revisit when: a DuckDB-specific description issue surfaces that needs isolation from the prompt-quality question.
Reversibility: cheap

### D8: Judge sees schema + description, not samples

The judge receives the table's schema (column names, types, nullability, PK flags) and the generated description, but not the row samples the generator was given. This preserves the existing `evaluation-toolkit` spec constraint and is a deliberate asymmetry, not an oversight.

Rationale: a downstream MCP agent reading the bundle gets exactly what the judge sees -- schema + description, no samples until it calls the `sample` tool. The judge therefore measures the right thing: "is this description self-contained and useful on its own?" If the description leans on sample-derived facts that the schema cannot confirm (e.g. "this `pt` column holds adverse-event preferred terms" inferred from sample values like "Nausea, Headache"), the judge has no evidence to validate the claim and may dock accuracy or specificity. That is the same disadvantage the downstream agent faces, so it is correct that the score reflects it.

Trade-off: sample-driven inferences are structurally undervalued by the judge even when correct. A description that says "stores ISO 3166-1 alpha-2 country codes" based on samples showing `"US"`, `"GB"`, `"DE"` will get the same accuracy treatment whether the inference is right or wrong, because the judge cannot see the samples. If the engine relies heavily on sample-driven inference to produce good descriptions, the judge will systematically underrate the engine on databases where samples are the only signal (FAERS, CMS SynPUF).

Alternative: give the judge access to samples. Rejected -- collapses the independence of the judge ("does this description hold up against what the generator already saw?" is a weaker check than "does this description hold up against just the schema?"), doubles the judge prompt size and cost, and stops modelling the downstream MCP agent's read.

Revisit when: baseline judge reasoning systematically penalizes correct, sample-derived inferences with comments like "claim not supported by schema" on descriptions that are in fact accurate -- visible by spot-checking flagged tables against the underlying database.
Reversibility: cheap (the eval module would gain a flag to include samples; no persisted format changes).

## Risks / Trade-offs

- [Cross-provider judge cost] LLM judge calls cost money per eval run. Mitigation: `--sample 10` keeps iteration cheap; full-database runs are periodic checkpoints.
- [Judge model availability] If OpenAI API is down, eval runs fail. Mitigation: `--judge-model` is configurable; fall back to an Anthropic model if needed (losing the cross-provider benefit temporarily).
- [Prompt overfitting] Improving prompts against the six test databases may not generalize. Mitigation: the six databases span diverse messiness patterns; periodic full-database eval (not just the fixed sample) guards against sample-specific overfitting.
- [Fixture data licensing] Public databases have varying licenses. Mitigation: all six are established public datasets widely used in benchmarks; seed scripts download from official sources.

## Open Questions

- **Exit thresholds**: What per-database minimum scores constitute "good enough"? Blocked on baseline data. Added as a later numbered decision once baseline scores are in (see D6).
- **Sample row strategy**: Current engine samples 5 rows via `LIMIT 5`. Is this enough for messy databases? May need diversity-aware sampling (distinct values, edge cases). Will evaluate after seeing baseline judge feedback.
- **Dimension correlation**: Do accuracy and domain-inference measure distinct properties, or do they collapse to one signal in practice? Compute Pearson correlation across all baseline per-table scores. If >= 0.85 across all six databases, merge them into a single accuracy-and-domain dimension and re-run eval to confirm cleaner discrimination. If correlation varies by database (e.g. high for clean schemas like ChEMBL where domain is obvious, low for messy ones like FAERS where domain inference is the hard part), keep them split -- the variance itself is a useful signal about where the engine struggles.
  - Resolved 2026-05-12: Pearson(accuracy, domain_inference) varies widely across the six baselines: chembl +0.886, lahman +0.873, faers +0.354, tpcds -0.111, adventureworks -0.167, cms-synpuf n/a (only 2 evaluable tables at baseline). Not >= 0.85 everywhere, so dimensions stay split. The variance itself confirms the design hypothesis: clean schemas correlate; messy ones decouple.

- **FAERS sparse-schema regression on `rpsr` (and softer on `indi`)**: After the iter1 prompt change, FAERS `public.rpsr` deterministically regresses from accuracy=4 (baseline) to accuracy=3 (4/4 re-judge runs). FAERS `public.indi` softly regresses 5 -> 4 in 3/4 re-judge runs. Judge reasoning: "infers more detail about relationships and domain than the schema strictly supports." Mean FAERS accuracy across 4 re-judge runs is 4.32 +/- 0.18 vs baseline 4.43 -- inside the noise band on aggregate, but the per-table pattern is a real, reproducible regression on two sparse-schema tables. Hypothesis: the new "anchor claims to schema evidence" guidance pushes the generator toward longer, more explanatory descriptions when the schema is sparse, and the extra surface area gives the judge more to flag. This is D8 manifesting via verbosity rather than niche-domain inference. Decision (2026-05-12): accept and ship -- the chembl/lahman/cms-synpuf wins outweigh, and FAERS aggregate is within baseline noise. Revisit when: D8 itself is revisited (giving the judge access to samples), or a sparse-schema-specific prompt iteration is scheduled. Reversibility: cheap (one prompt edit + re-eval).
