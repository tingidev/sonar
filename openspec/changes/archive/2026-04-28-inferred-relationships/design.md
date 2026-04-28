## Context

The `relationship-mapping` capability ships with a single naming heuristic: a non-PK column whose name matches `^(.+)_id$` infers a relationship to a same-schema table named `<stem>` or `<stem>s` whose single-column PK is `id` or `<stem>_id`. Single-candidate-only — ambiguity emits nothing.

Against ChEMBL (73 tables, 91 declared FKs as ground truth, FKs hidden from input as the experiment), the rule recovers 8 of 91 declared edges — **8.8% recall**. Investigation showed the misses are not random:

- 42 misses: source column ends in `_id`, target column has the **same name** (`enzyme_tid` → `target_dictionary.tid`, `record_id` → `compound_records.record_id`).
- 31 misses: source column does not end in `_id` but exactly matches the target PK column name (`activities.action_type` → `action_type.action_type`).
- 7 misses: column names differ entirely (`bao_endpoint` → `bao_id`).
- 3 misses: `_id` suffix with differing names.

ChEMBL uses canonical column names (`molregno`, `tid`, `record_id`) that repeat as PKs across tables and never appear as table names. The current rule, which assumes app-style `<table>_id` → `<table>` conventions, simply doesn't fire.

This change replaces the single rule with a two-rule combined heuristic, keeping the existing function signature, dataclass shape, ordering and log contract.

## Goals / Non-Goals

**Goals:**
- Lift declared-FK recall on canonical-name schemas (ChEMBL evidence: 8.8% → 68.1% at 72.9% precision).
- Stay within the existing `relationship-mapping` capability shape — no new fields on `Relationship`, no new `kind` values, no new MCP tools, no new dependencies.
- Preserve all existing invariants: declared edges first; no inferred edge on a declared-source column; deterministic ordering; one INFO log per call with counts; no values logged.
- Add a precision filter so a single shared PK-name (e.g. `version.name` on ChEMBL, present as a non-PK column on 22 other tables) does not absorb every `*_name` column.

**Non-Goals:**
- Value-overlap inference (deferred as `relationship-overlap-tiebreaker` in ROADMAP — the natural ambiguity tiebreaker for the residual ~25pp gap on ChEMBL).
- Cross-schema inference.
- Multi-column PK targets.
- Semantic / LLM-based matching for the truly different-name case (`bao_endpoint` → `bao_id`, ~4% of ChEMBL).
- Confidence scores or per-rule provenance on the `Relationship` dataclass — no consumer needs them today.
- Changing the `kind` enum, the MCP tool surface, or the bundle JSON format.

## Decisions

### D1: Replace the single `<stem>_id` rule with a two-rule combined heuristic

The new heuristic generates candidates by applying two rules to every non-PK column `C` in table `T(schema)`:

- **Rule A — direct PK-name match.** If `C.name` equals the single-column PK name of one or more same-schema tables (excluding `T` itself), each is a candidate.
- **Rule B — role-prefix match.** If `C.name` ends in `_<P>` where `<P>` is the single-column PK name of one or more same-schema tables, each is a candidate. `C.name` itself must not equal `<P>` (Rule A's territory).

Candidates from both rules are deduplicated; an `INFERRED` edge is emitted only when the combined set has exactly one entry. Existing same-schema, single-column-PK, declared-source-skip, and PK-source-skip constraints carry over.

Rationale: On ChEMBL, the new heuristic emits 85 edges of which 62 hit the declared-FK ground truth (recall 68.1%, precision 72.9%). The original `<stem>_id` rule's 8 hits are all caught by the new heuristic on ChEMBL — confirmed by experiment, set difference is empty — so this is a clean replacement, not a union.

Alternatives considered:
- **Three-rule union (keep `<stem>_id` rule + add A + B).** Rejected: original rule's hits are subsumed on ChEMBL; keeping it adds code and reasoning surface for zero recall benefit on the only real schema we've measured.
- **Just Rule A (no role-prefix).** Direct-only gives 62.6% recall, 91.9% precision (62/91 hits, 5 spurious). Rule B adds 5.5pp recall (most ChEMBL examples actually fall to ambiguity rather than role-prefix uniquely catching them). Rejected to drop B because role-prefix is the right shape-level rule for canonical-named schemas with role qualifiers (`drug_record_id`, `enzyme_tid`).
- **Fuzzy / Levenshtein matching.** Rejected: no evidence-based threshold; high false-positive risk on short column names.

Revisit when: a non-canonical-name schema (Rails-style `users.id` with many `id` PKs) is added to the test corpus and the heuristic's recall drops materially below the previous baseline on that schema, OR `evaluation-toolkit` (#10) measures rule-level recall and shows Rule B contributing nothing.
Reversibility: cheap (single-file logic in `relationships.py`; no persisted format changes; existing `RelationshipKind.INFERRED` shape unchanged).

### D2: Single-candidate-only emission (preserved invariant)

When the deduplicated candidate set has more than one entry, no edge is emitted — same as today. This preserves the rule that `INFERRED` is conservative: false positives are worse than false negatives because downstream consumers (MCP, agents) trust the graph as correct context. The residual ~25pp recall gap on ChEMBL after D1 is exactly the ambiguity case (e.g. `compound_records.molregno` → both `molecule_dictionary` and `biotherapeutics` expose `molregno` as PK). That gap is the explicit job of the deferred `relationship-overlap-tiebreaker`.

Revisit when: the deferred tiebreaker change is proposed.
Reversibility: cheap.

### D3: Catch-all PK precision filter via combined match-pressure

A PK column whose name attracts many same-schema non-PK columns — directly or via the role-prefix suffix — creates a precision problem: every such column points at the PK as a candidate, but the relationship is semantically wrong. ChEMBL's clearest case is `version.name`: zero non-PK columns are literally named `name`, but 18 non-PK columns end in `_name` (`pref_name`, `who_name`, `compound_name`, ...) and Rule B routes all of them at `version.name`.

Apply-phase prototype showed the original framing — "filter Rule A only on direct-name non-PK occurrences" — was the wrong shape: it filtered zero `version.name` matches (because no non-PK column is named exactly `name`) and dropped 6 legitimate Rule B hits. Replaced with a unified pressure metric.

**Match-pressure** for a PK `(schema, pk_name)` is the count of same-schema non-PK columns whose name either equals `pk_name` (Rule A pressure) or ends in `_<pk_name>` (Rule B pressure). A PK is excluded as a candidate target — for **both rules** — when its match-pressure exceeds **15**.

ChEMBL pressure distribution: `version.name` = 18 (excluded); `molregno` = 13, `record_id` = 8, `tid` = 7, `chembl_id` = 6 (all kept); next tier ≤ 4. The chosen threshold (15) sits cleanly between `name` (18) and `molregno` (13). Threshold values from 14 to 17 produce identical results on ChEMBL — there is one large gap in the pressure distribution and the threshold lives inside it.

Final ChEMBL numbers with D1 + D3 applied: **62 hits / 67 emitted / 91 declared FKs = 68.1% recall, 92.5% precision** (versus the no-filter baseline of 62/85/91 = 68.1% / 72.9%). D3 removes 18 spurious `*_name` → `version.name` edges without dropping any hits.

Both threshold and metric are design-level. The spec says "a PK SHALL be excluded as a candidate target when its name is over-shared among same-schema tables (where 'over-shared' is defined in design.md by a combined match-pressure threshold)" and lets the metric and threshold move without spec churn.

Alternatives considered:
- **No filter.** Rejected: ChEMBL emits 18 spurious `*_name` → `version.name` edges and ~8 other Rule-B-via-suffix spurious. Precision drops to ~73%.
- **Rule-A-only filter on raw non-PK-name occurrences (the original D3).** Rejected by apply-phase prototype: filters nothing on `version.name` (count=0) and incorrectly excludes legitimate Rule A targets like `chembl_id` (count=6). Net effect was −6 hits, 0 spurious removed.
- **Filter by data type (string-PK heuristic).** Rejected: brittle and excludes legitimate string PKs (ChEMBL's `action_type.action_type`, `target_type`).
- **Filter by PK name length / generic-word list.** Rejected: language-dependent and brittle. `tid` is 3 chars and a real target; `name` is 4 chars and a catch-all.

Revisit when: a real schema either misclassifies a legitimate target (a heavily-referenced PK whose pressure exceeds 15 but which IS the right target) or fails to filter a real catch-all (a `version.name`-style PK with pressure ≤ 15).
Reversibility: cheap (single helper + constant in code).

### D4: Keep the `RelationshipKind` enum binary

No `inferred_direct` / `inferred_role_prefix` / `inferred_disambiguated` tiers. No `confidence: float`. No `inference_method: str`.

Per minimum-interface principle: today no consumer reads provenance. The MCP `relationships` tool returns `Relationship` records as-is; the agent treats `inferred` as "use with caution" without caring about which rule fired. The future consumer is `evaluation-toolkit` (#10), which may need rule-level recall for measurement — at that point we add the field additively.

Alternatives considered:
- **Add `inference_method` enum now.** Rejected: speculative split. The change is additive when needed; carrying it now means JSON output and tests pin to a value taxonomy that may not be the right one once #10 is real.
- **Add `confidence: float`.** Rejected: no consumer; semantics undefined (rule-firing is binary).

Revisit when: `evaluation-toolkit` (#10) needs to measure per-rule recall, OR an external MCP consumer asks for provenance (none exists today).
Reversibility: cheap (additive on the dataclass; existing JSON output stable; new field defaults to `None` for older bundles).

### D5: Preserve same-schema, single-column-PK, declared-skip, PK-source-skip constraints

All four constraints already hold in the current implementation; we keep them. Cross-schema inference, multi-column PKs, declared-source rewrites, and PK-as-source inference are each separate questions with their own evidence requirements. None has evidence today.

Revisit when: a real schema shows a meaningful relationship blocked by one of these constraints.
Reversibility: cheap.

## Risks / Trade-offs

- **App-style schemas with many `id` PKs lose the original rule's table-name disambiguation.** The original `<stem>_id` rule used `<stem>` table name to pick among multiple `id`-PK candidates; the new rules do not. On a Rails-style schema with 30 tables all keyed by `id`, Rule B (role-prefix `_id` → `id`) is ambiguity-blocked everywhere, AND the catch-all-PK filter excludes `id` as a Rule A target. Net: recall drops near zero on that schema shape. → Mitigation: this is the schema shape where users should declare FKs (every modern ORM does); the inference layer is the safety net for legacy or denormalised schemas. Documented limitation, not a blocker. If real usage hits this, add a table-stem preference rule as a follow-up change.

- **Spurious edges from genuinely unrelated PKs.** Even after D3, ChEMBL emits ~5 false positives (e.g. `assay_class_map.assay_class_id` → `assay_classification.assay_class_id` is a real semantic link the schema author chose not to declare; whether this is a true or false positive depends on whose definition you take). → Mitigation: D3 filters the obvious catch-all class; the residual is acceptable noise for an `INFERRED` edge that downstream consumers know to weight lower than `DECLARED`.

- **Single-schema test surface (ChEMBL only).** All percentages above come from one real schema. → Mitigation: the rules are evidence-based for ChEMBL-style canonical-name schemas; the design's `Revisit when` triggers explicitly call out adding a second test schema as the moment to re-evaluate. Don't over-fit further on ChEMBL alone.

- **Deferred work depends on this change's residual ambiguity.** The `relationship-overlap-tiebreaker` design hinges on what ambiguity looks like after D1+D3 ship. → Mitigation: ship and observe; the deferred entry in ROADMAP carries the empirical context.

## Open Questions

- **Catch-all PK threshold (D3).** 15 is calibrated to the ChEMBL pressure distribution (one large gap between `name` at 18 and `molregno` at 13). A second test schema may shift the right value. Defer adjustment until we have evidence.
- **Table-stem preference for app-style ambiguity.** If a user reports the app-style regression in practice, the fix is a new tiebreaker rule (when multiple Rule A/B candidates, prefer the one whose table name matches the column stem). Captured here as a known follow-up; not part of this change.
