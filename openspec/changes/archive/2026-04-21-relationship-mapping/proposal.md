## Why

The Postgres connector extracts declared foreign keys, but many real-world schemas — especially brownfield ones — carry columns like `user_id` or `customer_id` that act as foreign keys without a declared constraint. Agents consuming Sonar context need the full relationship graph, not just the half that happens to be declared. This change produces one unified graph spanning both declared FKs and naming-heuristic inferences, giving downstream consumers (`context-index`, `mcp-server`) a single structure to reason over when navigating a database.

## What Changes

- Introduce a `relationship-mapping` capability centred on a pure, synchronous function `map_relationships(tables, foreign_keys)` that takes `list[Table]` + `list[ForeignKey]` (both produced by `postgres-connector`) and returns `list[Relationship]`. No class, no state, no I/O.
- Define a new `Relationship` frozen dataclass carrying source and target `(schema, table, column)` triples and a `RelationshipKind` enum (`DECLARED`, `INFERRED`) categorising the provenance of the edge.
- Anchor the graph on declared FKs; emit one `Relationship` per declared FK column pair (composite FKs produce multiple edges, consistent with `ForeignKey` rows).
- Add one naming-heuristic inference rule for columns not covered by a declared FK — matching a foreign-key-like column name to a single unambiguous same-schema target table. Concrete suffix pattern and candidate acceptance criteria live in `design.md` so they can evolve without a spec delta.
- Inference MUST never contradict or override a declared FK. The declared graph is the anchor; heuristics only fill gaps.
- Emit one INFO log record per `map_relationships` call summarising counts of declared edges, inferred edges, and tables scanned. No PII risk here (only schema/table/column names), but we keep the "no row content in logs" contract from the engine for consistency.

## Capabilities

### New Capabilities
- `relationship-mapping`: Produces a unified relationship graph combining declared FKs from Postgres metadata with naming-heuristic inferences. Pure derivation over `Table` and `ForeignKey` inputs — no I/O, no LLM, no side effects.

### Modified Capabilities
(none)

## Impact

- **New code:** `src/sonar/relationships.py` (module) + `tests/test_relationships.py`.
- **No LLM dependency.** Pure in-process computation; safe to exercise in CI with no external services.
- **No changes to `postgres-connector` or `description-engine` specs.** Consumers that want semantic-type corrections based on the graph (e.g. FK columns surfacing as identifiers regardless of what the LLM guessed) compose the two outputs at the `context-index` layer, not here. Keeping this capability pure means future heuristics are a local concern.
- **Unblocks `context-index` (roadmap change #4)**, which needs both descriptions and a relationship graph to persist as the agent-facing context bundle.
