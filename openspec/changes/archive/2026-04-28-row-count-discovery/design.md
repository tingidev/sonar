## Context

`Table.row_count` already exists on the connector's data shape and is already serialised through the bundle format, but the postgres connector never populates it — every table currently surfaces `row_count=None`. The MCP `discover` tool exposes the field unchanged; agents using row counts to triage which tables matter receive no signal.

The interesting decision is *how* to populate the field. Postgres offers three options at very different cost profiles:

1. `SELECT COUNT(*) FROM "schema"."table"` — exact, but a sequential scan on tables with no usable index (minutes on multi-million-row tables, and `discover_tables` runs N of these).
2. `pg_class.reltuples` — the query planner's row-count estimate, maintained by `ANALYZE` / autovacuum. Zero query cost (single catalog lookup, joinable into the existing schema-discovery query), but only as fresh as the last `ANALYZE`.
3. `TABLESAMPLE`-based extrapolation — middle ground, but adds query complexity for a use case (coarse triage) that does not need it.

A cross-cutting constraint: schema introspection runs on every `sonar scan`. The discovery cost budget is "small constant per table" — anything that scales with table size belongs in a separate, opt-in code path.

## Goals / Non-Goals

**Goals:**
- Populate `row_count` for live tables in a single connector query, no per-table fan-out.
- Zero side effects on the user's database (no implicit `ANALYZE`).
- Distinguish "empty table" (`0`) from "unknown" (`None`) cleanly in the contract.
- Keep the existing `Schema Introspection` requirement and its scenarios untouched — row counts are a sibling concern, not a modification of column-level introspection.

**Non-Goals:**
- Exact counts. Agents using `row_count` are deciding which tables are worth describing, sampling, or asking about — order-of-magnitude is sufficient.
- Triggering or scheduling `ANALYZE` from Sonar. The user's database autovacuum policy is theirs.
- Tracking row-count drift over time. Bundle versioning is full-overwrite (per `context-index` spec); historical counts are not in scope.
- Connector abstraction work. This change is postgres-only and uses postgres-specific catalog tables. The Snowflake equivalent (change #9) will discover its own statistics source.

## Decisions

### D1: Use `pg_class.reltuples` for row count, not `COUNT(*)`

Source the estimate from `pg_class.reltuples`, joined into the existing schema-discovery query against `pg_class`/`pg_namespace`. No per-table follow-up query.

**Why:** Zero added query cost — `pg_class` is already touched implicitly by introspection joins. `reltuples` is what Postgres' own planner uses to choose query plans on these tables; "good enough for the planner" is plainly good enough for agent triage. `COUNT(*)` would turn a sub-second discovery into a multi-minute one on a real database; that is not a trade we want to make on every `sonar scan`.

**Alternatives considered:**
- `COUNT(*)` per table — rejected on cost.
- `TABLESAMPLE SYSTEM_ROWS(N)` extrapolation — rejected as over-engineered for the triage use case; also adds an extension dependency (`tsm_system_rows`).
- Optional opt-in `--exact-counts` flag on `sonar scan` — deferred. No concrete consumer is asking for exact counts; YAGNI.

**Revisit when:** an MCP consumer (agent or downstream tool) surfaces a concrete need for sub-1% row-count accuracy — for example, a future evaluation-toolkit metric (roadmap change #10) that depends on absolute table size.

**Reversibility:** cheap. Swap the catalog lookup for a different source; the field shape (`int | None`) and bundle JSON do not change. No persisted-format implications.

### D2: Map "no usable statistics" to `None`, not `0`

`pg_class.reltuples` returns `-1` when the planner has no statistics for a relation (newly created, never analysed). Treat any negative value as `None`. Tables with `reltuples = 0` after a real `ANALYZE` are emitted as `0` (genuinely empty).

**Why:** `0` and "unknown" are different signals. An agent looking at `row_count=0` SHOULD conclude "this table is empty, deprioritise"; on `None` it should conclude "no info, fall back to other heuristics." Conflating them silently degrades agent decision quality. The Postgres convention of using `-1` as the no-stats sentinel is well-defined and stable across versions.

**Alternatives considered:**
- Always emit `0` when stats absent — rejected, false signal.
- Trigger `ANALYZE` when stats absent — rejected on the no-side-effects principle. The user's `ANALYZE` schedule is theirs to own; Sonar must not silently mutate database catalogs.
- Read `pg_stat_user_tables.n_live_tup` instead — partially redundant with `reltuples` (both fed by autovacuum), and unavailable on standby replicas. `reltuples` is the broader-coverage source.

**Revisit when:** first user report that they consistently see `None` on a populated table — likely indicates an autovacuum-disabled environment, in which case we'd add a single-line warning rather than change behaviour.

**Reversibility:** cheap. The mapping lives in one expression in `discover_tables`.

### D3: Implement as one extra column in the existing introspection query, not a separate query path

Extend the existing `discover_tables` SQL to include `(SELECT reltuples::bigint FROM pg_class c WHERE c.oid = format('"%s"."%s"', n.nspname, t.table_name)::regclass)` (or equivalent join) as a per-row column. Cast `reltuples::bigint` so Python receives an `int`, not a `float`. Apply the negative-to-`None` mapping in the Python row factory.

**Why:** Keeps the connector's introspection footprint at one round-trip. Adding a second query would double the catalog round-trips for no gain — `pg_class` is the same relation we already query for table enumeration.

**Alternatives considered:**
- Separate `_fetch_row_counts` method called after `discover_tables` — rejected; doubles round-trips, splits the data shape across two methods, and creates a window where part of the bundle is stale relative to the rest.

**Revisit when:** the introspection query gets large enough (more catalog joins for the inferred-relationships change #7) that splitting it improves readability.

**Reversibility:** cheap. The query is one call site.

## Risks / Trade-offs

- **Stale reltuples on long-untouched tables** → mitigated by D2 sentinel handling. Tables with very out-of-date stats but `reltuples >= 0` may show a count an order of magnitude off the truth. Spec's accuracy clause says "within an order of magnitude" for analysed tables; we accept that bound. The mitigation if a user complains is documentation ("counts are planner estimates"), not implementation change.
- **Standby replicas / read-only roles** → `pg_class.reltuples` is readable on replicas; no privilege requirement beyond what schema introspection already needs. The dotted-identifier rejection check (existing requirement) still applies — the new column doesn't bypass it.
- **The query uses `format(...)::regclass`** which is Postgres's safe identifier resolution. This avoids the SQL-injection surface that naive concatenation would create; the `::regclass` cast either resolves the identifier through the catalog or raises, never executes injected text.

## Migration Plan

No migration. Bundle JSON shape is unchanged — `row_count` was always serialised as `int | None`. After this change, values stop being `null` for live tables and start carrying integer estimates. Existing `.sonar/` bundles remain readable; running `sonar scan` regenerates them with populated counts. No version bump needed.

## Open Questions

- **Partitioned tables surface `None` despite holding data (deferred 2026-04-28).** `pg_class.reltuples` on a partitioned-table parent (`relkind = 'p'`) is often `-1` or `0` because rows live in the child partitions. Our negative-to-`None` mapping then surfaces `None` for a populated partitioned table — technically a violation of the spec's "Live tables carry a non-negative row count" scenario for that table class. No roadmap consumer is on partitioned schemas yet, and the `inferred-relationships` / `snowflake-connector` work doesn't intersect this. Cheapest fix when it bites: change the query to `SUM(reltuples)` over the partition tree using `pg_inherits`. Surfaced by `/opsx:audit` 2026-04-28 (medium severity, high confidence). **Revisit when:** first user report of a known-partitioned table consistently surfacing `None`. **Reversibility:** cheap (catalog-side query change, no API impact).
