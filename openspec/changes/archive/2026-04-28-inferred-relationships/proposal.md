## Why

The current naming heuristic in `relationship-mapping` recovers only 8.8% of declared foreign keys on ChEMBL (8 of 91). Its single rule — `<stem>_id` columns matched against same-schema tables named `<stem>` or `<stem>s`, with PK called `id` or `<stem>_id` — assumes app-style naming (`user_id` → `users.id`). Real-world schemas, even well-curated ones, use canonical column names (`molregno`, `tid`, `record_id`) that repeat across tables and never appear as table names. The graph that downstream consumers (MCP `relationships` tool, `search` tool, future `evaluation-toolkit`) read is therefore far thinner than it could be when FKs are absent or partially declared.

## What Changes

- Replace the single `<stem>_id` rule with two rules applied together:
  - **Direct PK-name match**: a non-PK column whose name equals the single-column PK name of exactly one same-schema table (excluding self).
  - **Role-prefix match**: a non-PK column whose name ends in `_<pk_name>` and matches exactly one same-schema table's single-column PK (e.g. `enzyme_tid` → `target_dictionary.tid`).
- Apply both rules in one pass, deduplicating candidates; emit one `INFERRED` edge only when the combined candidate set has exactly one entry.
- Add a precision filter for **catch-all PK columns** — a PK column shared as a non-PK column across many same-schema tables (the `version.name` pattern on ChEMBL): such a PK SHALL not be a candidate target for the direct-match rule. Threshold and exact rule documented in `design.md`.
- Keep all existing invariants: declared FKs always emitted first; declared source columns never produce an inferred edge; inferred edges sorted deterministically; one INFO log per call with declared/inferred/tables-scanned counts; no logging of values.
- Keep the `RelationshipKind` enum binary (`declared | inferred`). No new fields on `Relationship`. No new MCP tools.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `relationship-mapping`: replaces the current `<stem>_id` rule with the two-rule heuristic plus the catch-all-PK precision filter. The capability's shape (function signature, dataclass, enum, ordering, log contract) is unchanged.

## Impact

- **Code**: `src/sonar/relationships.py` — replace candidate generation; add catch-all-PK precondition. No new dependencies.
- **Tests**: `tests/test_relationships.py` — extend with cases covering direct-match, role-prefix match, multi-rule deduplication, ambiguity suppression, catch-all-PK suppression. Existing tests for declared-FK behaviour and ordering invariants stay green.
- **Specs**: delta on `relationship-mapping` describing the new inference rules; ChEMBL recall numbers retained in `design.md`.
- **Downstream consumers**: MCP `relationships` and `search` tools read the same `Relationship` shape; output volume increases (more `inferred` edges) but no schema change. Bundle JSON format unchanged.
- **Out of scope**: value-overlap-based inference (deferred as `relationship-overlap-tiebreaker` in ROADMAP); semantic/LLM-based inference; cross-schema inference; multi-column PK targets.
