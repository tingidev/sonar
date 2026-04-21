## Context

The archived `postgres-schema-discovery` capability produces `ForeignKey` entries for every declared constraint in the database. Real brownfield schemas are rarely that clean — it's common to see `orders.user_id` referring to `users.user_id` without a constraint, either because the table was created before the FK was thought through, or because ORMs and migrations skipped declaring it. Agents that rely only on declared FKs see a half-graph.

`description-engine` deliberately dropped `FOREIGN_KEY` from `SemanticType` because FK-ness is deterministic from metadata and shouldn't be LLM-guessed. That left a gap: nothing in the pipeline currently surfaces FK information as first-class structured data. This change fills it.

Concrete downstream consumers already on the roadmap:
- `context-index` (change #4) persists descriptions + relationships as the on-disk context bundle.
- `mcp-server` (change #5) exposes a `relationships` tool directly from that bundle.

Both benefit from one graph that is both declared-anchored and gap-filled.

## Goals / Non-Goals

**Goals:**

- Produce a unified `list[Relationship]` from `list[Table]` + `list[ForeignKey]` — one graph, one shape.
- Keep the module a pure synchronous function over immutable inputs. No I/O, no LLM, no mutation.
- Anchor the graph on declared FKs. Heuristics fill gaps only; they never override.
- Keep inference rules minimal, explicit, and documented — one rule covers the roadmap-named pattern (`user_id → users.id`). Additional rules land when a named consumer demands them.

**Non-Goals:**

- **No LLM-based inference.** Naming heuristics are deterministic. Asking an LLM to infer relationships is a later change if ever warranted — we'd need data on false positives first.
- **No second inference rule in Phase 1.** Only the suffix rule (D3). Broader exact-name matching is deferred (see Open Questions).
- **No continuous `confidence` field.** One inference rule produces exactly one "inferred" population, making a numeric confidence redundant with `kind`. Defer the field until a second rule or a cardinality signal demands a gradient (see Open Questions).
- **No side effects on `TableDescription`.** The relationship graph is a sibling structure; merging with descriptions happens at the `context-index` layer.
- **No transitive-closure computation.** Direct edges only.
- **No cycle detection or graph validation.** Self-references and cycles are legitimate data models.
- **No join-cardinality inference.** Cardinality is derivable later from PK/uniqueness, but it's a feature for `context-index` or beyond.
- **No cross-schema name-similarity heuristics.** Schema mismatch is a strong signal unrelated tables share a column name coincidentally.

## Decisions

### D1. Single-capability change; pure module

One new capability, one new module: `src/sonar/relationships.py`. Flat layout (no subpackage) until it grows. The module exposes:

```python
class RelationshipKind(enum.StrEnum):
    DECLARED = "declared"
    INFERRED = "inferred"

@dataclass(frozen=True)
class Relationship:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    kind: RelationshipKind

def map_relationships(
    tables: list[Table],
    foreign_keys: list[ForeignKey],
) -> list[Relationship]: ...
```

Deliberately not a class. There's no state — `map_relationships` is a pure function. A class would be ceremony. Consumers call the function; if we later want knobs (e.g. turn heuristics off), they become kwargs.

`engine/` houses LLM-backed inference. This is not LLM-backed. `connectors/` houses database I/O. This doesn't do I/O either. Flat module at `src/sonar/relationships.py` matches its actual shape.

No `confidence: float` field. See D3 and Open Questions — a continuous-valued confidence is deferred until a consumer demands it.

Revisit when: the dataclass grows a second categorical kind, a confidence gradient, or a cardinality indicator.
Reversibility: cheap. Adding fields is additive and backward-compatible for serialisers that read by name.

### D2. Declared FKs pass through unchanged

`ForeignKey` already comes pre-expanded — composite FKs produce one `ForeignKey` row per column pair with correct alignment (`postgres-connector` Requirement: Foreign Key Extraction). We pass them through as `Relationship(kind=DECLARED)`. Composite-FK alignment is the connector's concern, not ours.

Declared edges are always emitted, even if the target table isn't present in the `tables` list (e.g. cross-schema FK pointing at a schema the caller filtered out). Filtering based on `tables` would silently drop edges the user might care about.

Revisit when: a caller reports that cross-schema declared edges pollute output (none named today).
Reversibility: cheap. Filtering is a one-line kwarg to add.

### D3. Inference rule — `<stem>_id` suffix → owning table

This is the one heuristic in Phase 1, and it's precisely the pattern named in `ROADMAP.md` ("`user_id` to `users.id`"). For every non-PK column whose `(source_schema, source_table, source_column)` is not already covered by a declared edge:

1. Match the column name against `^(.+)_id$` case-insensitively; extract `<stem>`.
2. Look for a target table in the **same schema** whose name equals `<stem>` or `<stem>s` (naive plural).
3. Accept the candidate only if it has a **single-column** primary key named either `id` or `<stem>_id`.
4. Emit an `INFERRED` edge if and only if exactly one candidate survives. Zero or two-or-more candidates → no edge.

Why same-schema only: cross-schema name collisions are too easy to make by accident, and deliberate multi-schema relationships are usually declared. False-positive cost is high; we skip.

Why naive plural only (`stem + "s"`): English-plural normalisation (`person`↔`people`, `category`↔`categories`) is a rabbit hole. We handle the 95% case.

Why single-column PK only: composite PKs as inference targets are ambiguous — which column is the referent? Declarations handle composite FKs correctly; inference should not try.

Revisit when: measured false-positive rate exceeds ~10% on a real brownfield schema (evidence from first real user scan), or the naive plural proves inadequate on observed schemas.
Reversibility: cheap. Rule is a ~30-line pure function; tightening the match, adding a blocklist, or extending pluralisation is a local edit.

### D4. Declared edges block inference on the source column

Before running heuristics, we build the set of `(source_schema, source_table, source_column)` triples covered by declared edges. Any column already in that set is skipped by inference. This enforces "inference never overrides declared" and avoids emitting a declared edge plus a redundant inferred one for the same column.

We do NOT dedupe by `(source → target)` pair. If the declared FK for `orders.user_id` points at `users.user_id` and a (hypothetical) heuristic would point at `users.id`, the declared one wins and the heuristic is silenced by the column-level block naturally.

Revisit when: a consumer requests visibility into the "what inference would have produced" set for debugging.
Reversibility: cheap. Suppression is one line; reversing it returns both.

### D5. Keep descriptions and relationships separate — merge at `context-index`

The description engine's `TableDescription` and this change's `list[Relationship]` are returned as two independent structures. We do NOT add a `foreign_key: Relationship | None` field to `ColumnDescription`. Reasons:

1. Adding that field is a `MODIFIED Requirements` delta on `description-engine`, coupling two capabilities that evolve at different rates.
2. Agents that want the joined view produce it at the `context-index` layer (the next change), where the on-disk JSON can represent both.
3. Keeping this module pure over `Table` + `ForeignKey` means it's trivially testable without any LLM mocking.

The earlier note in `llm-description-engine`'s design.md ("relationship-mapping will override LLM semantic-type guesses") is satisfied by the graph being available — any consumer can check `Relationship.source == (schema, table, column)` and treat that column as an identifier regardless of the LLM's guess. We don't rewrite `TableDescription` to do that for them.

Revisit when: `context-index` or `mcp-server` lands and client-side composition proves to be repeated boilerplate that deserves a helper.
Reversibility: cheap. A later `sonar.context.merge(descriptions, relationships)` helper is purely additive; no spec change on either capability.

### D6. Logging — one INFO record per call

Logger `sonar.relationships`, level `INFO`, one record per `map_relationships` call with `extra={"declared": N, "inferred": M, "tables_scanned": T}`. No per-edge logging — would be noise on a large scan.

No row content flows through this module, so there's no PII exposure. The "no column values in logs" contract from the engine is inherited by default.

Revisit when: a debugging session requires knowing which columns were skipped and why (rejected candidate counts, etc.).
Reversibility: cheap. Expanding the record is additive.

### D7. Deterministic ordering

Results are returned declared-first (in the order produced by `postgres-connector`, which is already deterministic from `information_schema`), then inferred, sorted by `(source_schema, source_table, source_column)`. This makes tests simple and means snapshots in `context-index` won't churn.

Revisit when: never — determinism is a baseline property.
Reversibility: cheap.

## Test strategy

Pure unit tests. No Docker, no async, no fixtures directory. `Table` and `ForeignKey` are built in-test from a small helper. `tests/test_relationships.py` covers all spec scenarios plus the frozen-dataclass and JSON round-trip invariants. Coverage target: 100% on `src/sonar/relationships.py` (pure function, trivial to exercise every branch).

## Risks / Trade-offs

- **Risk:** Rule 1 emits false positives on generic-looking columns (e.g. `created_by_id` when there's an unrelated `created_by` table). → **Mitigation:** `kind=INFERRED` is itself the surfaceable signal — consumers who want declared-only filter on kind. If false-positive rate is high on real schemas, we tighten the rule (D3 revisit trigger).
- **Risk:** Naive English pluralisation misses `person`/`people`, `categories`, etc. → **Mitigation:** Phase 1 documents this limit. If observed, move to an explicit stem-map (not a library dependency).
- **Trade-off:** We don't infer cross-schema relationships. In multi-schema designs where one schema owns reference data, we miss those edges. → **Accepted:** false-positive risk is too high; users with this pattern should declare the FKs.
- **Trade-off:** Relationships are a separate structure, not a field on `ColumnDescription`. → **Accepted:** keeps both capabilities evolvable independently and this module pure (D5).
- **Trade-off:** No continuous confidence. With one rule, the field would be a constant; adding it now is noise. → **Accepted:** deferred until a second rule or a data-driven signal demands a gradient (Open Questions).

## Migration Plan

Not applicable — greenfield capability.

## Open Questions

Explicitly deferred; each has a concrete trigger that would bring it back.

1. **Second inference rule (shared non-PK column name → single PK owner).**
   Status: considered, cut. No named consumer today; the roadmap-named `user_id → users.id` pattern is covered by D3 alone. The draft rule was: for every non-PK, non-FK, non-rule-1-matched column, search the same schema for tables where that column is the sole PK; emit INFERRED if exactly one such owner exists.
   Bring back when: a consumer (likely `mcp-server` agent usage) reports a measurable gap where relevant joins aren't surfaced because the join column doesn't end in `_id` (e.g. enum-table joins like `status` → `statuses.status`).

2. **`confidence: float` on `Relationship`.**
   Status: considered, cut. One inference rule produces one inferred population; a numeric field is redundant with `kind`. Bring back when: a second inference rule introduces a meaningful gradient, or cardinality / sample-join-success measurement gives a data-driven score.

3. **Blocklist / allowlist for inference.**
   If rule 1 emits too many false positives on a real schema, add an optional `exclude_columns` (or similar) kwarg.
   Trigger: first real-schema measurement of false-positive rate > ~10%.

4. **English pluralisation table.**
   If naive `+s` misses common patterns on real schemas, add a tiny inflection table (hand-maintained, ~10 entries, no library dependency).
   Trigger: observed miss on a user-facing scan.
