# Learnings

Technical explanations of what we're building and why, written as we go. Each section maps to a milestone or decision point.

---

## Project Setup

### Why Poetry + src layout?

Poetry manages Python dependencies and packaging. The `src/` layout (as opposed to putting `sonar/` at the root) prevents a common bug: without `src/`, Python can accidentally import from your local source directory instead of the installed package. With `src/`, you must install the package (`poetry install`) before imports work — this guarantees your tests run against the same code a user would install.

### Why async throughout?

Sonar's operations involve I/O-heavy work: database queries, LLM API calls, MCP message handling. Async (`async`/`await`) lets Python handle multiple I/O operations without blocking. When you `await` a database query, Python can start an LLM call in parallel instead of waiting idle. This matters when scanning 50 tables — you could describe multiple tables concurrently rather than sequentially.

We use `psycopg` 3 (not `psycopg2`) specifically because it has native async support.

### Why frozen dataclasses?

```python
@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
```

`frozen=True` means instances can't be modified after creation. If you want a different value, you create a new object. This eliminates an entire class of bugs where something accidentally modifies shared state. It also makes objects hashable (usable as dict keys or in sets) for free.

The tradeoff: slightly more memory (new objects instead of modifying in place). Irrelevant at our scale.

---

## Postgres Connector

The first real capability. Scans a live Postgres database and returns its structure as Python objects: `Table`s with their `Column`s, `ForeignKey`s between them, and small row samples for each table. Downstream stages (LLM descriptions, relationship graphs, MCP context) all feed off this output. The non-obvious parts:

### `information_schema` vs `pg_catalog`

Postgres exposes schema metadata two ways. `pg_catalog` is Postgres's own internal bookkeeping — fast, complete, and vendor-specific. `information_schema` is the SQL-standard view over `pg_catalog` — portable, slightly slower, and **permission-scoped**: it silently hides tables the connecting role lacks `USAGE` on.

We chose `information_schema`. For the test DB the connecting role owns everything, so visibility is complete. For real deployments this becomes a concern the `mcp-server` change will handle. The tradeoff is deliberate: portability and a standard mental model now, re-inspection when we know the permission shape of real customer DBs.

### psycopg3 row factories (`dict_row`)

psycopg3 returns tuples by default — positional access, column order matters to the caller. The `row_factory` argument lets you pick a different shape. We use `psycopg.rows.dict_row` everywhere introspection runs, so the cursor yields `dict[str, Any]`. Two payoffs:

1. The `discover_tables` grouping loop refers to columns by name (`row["table_name"]`), not index — the query can evolve without rewriting the Python.
2. `sample_table` returns what downstream code wants anyway: dicts keyed by column name, ready to `json.dumps`.

The `dict_row` factory is set per-cursor, not per-connection, so cursors that don't need it stay on the default tuple shape.

### The `udt_name` fallback for ARRAY and USER-DEFINED

`information_schema.columns.data_type` is the SQL-standard name, which for non-standard types is useless: it returns the literal string `"ARRAY"` for any array, and `"USER-DEFINED"` for any enum or domain. An LLM reading `"data_type: ARRAY"` learns nothing — array of what?

The companion column `udt_name` is Postgres's own type name: `_text` for `text[]`, `_int4` for `integer[]`, or the enum/domain's own name (e.g. `order_status`). We swap to `udt_name` exactly when `data_type` is `ARRAY` or `USER-DEFINED`, and leave the standard name alone otherwise. The result: the LLM sees `uuid`, `timestamp with time zone`, `numeric`, `_text`, `order_status` — all informative.

### The `position_in_unique_constraint` join for composite FKs

The query shape people write first is to join `referential_constraints` → `key_column_usage` twice (source side, target side) on `(constraint_name, schema)` and then align source and target columns by name. That works for simple FKs where the referenced column has the same name in both tables. It breaks for composite FKs where the names differ.

The correct join uses `position_in_unique_constraint`: every row in `key_column_usage` for a referencing column carries an integer saying "this is the Nth column of the target's unique constraint". Join the target side on `ordinal_position = position_in_unique_constraint` and the alignment is positional, not nominal. A two-column FK `(a, b) → (x, y)` produces two rows: `a → x`, `b → y`, correct regardless of column names.

### `psycopg.sql.Identifier` even for trusted inputs

`sample_table` takes `schema` and `table` strings and builds `SELECT * FROM {schema}.{table} LIMIT {n}`. In our flow both come from prior discovery, so they are trusted — no untrusted-input SQL injection concern. We still compose the query with `psycopg.sql.Identifier(schema)` and `psycopg.sql.Identifier(table)` rather than f-string interpolation.

Why: `Identifier` handles reserved words (`SELECT * FROM "order"`), mixed case (`"MyTable"`), and embedded quotes for free. It costs nothing and removes an entire class of "works on dev schema, breaks on customer schema" bugs. "Trusted input" is not a license to skip identifier quoting — it's a license to skip **parameter** quoting, which is a different mechanism.

`psycopg.sql.Literal(limit)` is used for the integer limit for the same reason: belt-and-braces composition via a library that knows every corner case, instead of `f"LIMIT {limit}"`.

### Async for a one-shot scan

Sonar scans a database once per invocation. There's no long-running server, no request/response fan-out, no concurrent queries within a single scan. So why is the connector async?

Because the upstream consumer is async. The LLM description stage will call Anthropic's API per table; the MCP server is async by protocol; the context index will hydrate from async I/O. Making the connector sync would force a `asyncio.run()` wrapper at every caller, or split the codebase into a sync island around the connector. Async costs us almost nothing here — a single async context manager, no connection pool, no concurrency within the connector itself — and keeps the whole pipeline in one event loop.

`psycopg3` (unlike `psycopg2`) has native async support, so this choice is free.

### Connection lifecycle as async context manager

`PostgresConnector` is used as `async with PostgresConnector(url) as conn: ...`. `__aenter__` opens one `AsyncConnection`; `__aexit__` closes it. Public methods raise `RuntimeError` if called outside the context.

Alternatives considered and rejected:

- **Per-method connect/disconnect.** Cheap-looking but wrong: every test that exercises three methods pays three handshakes. Also leaks the concept of "connection" into every caller.
- **Connection pool.** Overkill. A scan is a short serial sequence of three queries; there is never a second concurrent query.
- **Single connection opened in `__init__`.** The class now owns a resource with no explicit close point. Exceptions during construction or between method calls leak connections. Async `__init__` is also impossible — you'd need a factory `classmethod` and the syntax becomes uglier than the context manager version it was trying to avoid.

The chosen shape makes the resource lifetime visible at the call site, which is exactly where the reader needs it.

---

## LLM Description Engine

The second real capability. Takes a `Table` + its row samples and returns a `TableDescription` — a structured ontology, not a paragraph. Downstream consumers (relationship inference, MCP context, agents) never see free-form text: they see typed fields they can filter, aggregate, and reason over. The non-obvious parts:

### Two capabilities, not one

`llm-client` and `description-engine` are separate specs in `openspec/specs/`. The temptation was to put the Anthropic SDK usage inside `DescriptionEngine` directly and call it one capability. Rejected. Reasons:

- `llm-client` will be swapped for LiteLLM before public release. If it lived inside `description-engine`, every future LLM-using capability (relationship inference, query planning, MCP tool-use) would need its own provider wiring. With the split, swapping providers is a `MODIFIED Requirements` delta on `llm-client` — no other capability changes.
- The two concerns evolve differently. `llm-client` wants one stable, narrow surface (`generate(prompt, system) -> str`). `description-engine` wants a rich vocabulary (semantic types, PII risk, grain, domain hints). Keeping them in separate specs keeps each spec's requirement list coherent.

Cost of the split: one extra file and a type annotation (`llm_client: LLMClient`) in `DescriptionEngine.__init__`. Worth it.

### Why `StrEnum`, not `Enum`

Python 3.11 introduced `enum.StrEnum`, which is `str`-and-enum simultaneously. That matters for JSON round-tripping:

```python
class SemanticType(StrEnum):
    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    ...
```

With `StrEnum`, `json.dumps({"semantic_type": SemanticType.IDENTIFIER})` produces `{"semantic_type": "identifier"}` with no custom encoder — because `SemanticType.IDENTIFIER` *is* the string `"identifier"` as far as `json` is concerned. Round-trip parse is `SemanticType(loaded["semantic_type"])`.

Plain `Enum` would need a `default=` encoder hook and an explicit lookup on read. `IntEnum` would force numeric enum values on the wire — readable in code, opaque in JSON. `StrEnum` gives a readable wire format and no encoder boilerplate.

### Structured ontology, not prose

The LLM returns JSON matching a documented schema. We parse it. We construct a frozen `TableDescription`. **Constructing the dataclass is the validation.** If the LLM hallucinates a `semantic_type` of `"widget"`, the `SemanticType("widget")` call raises `ValueError` and we fall into the parse-retry path.

Rejected alternative: ask the LLM for prose and post-hoc classify. Prose is irreducibly lossy. Once a model writes "this column stores customer identifiers but also acts as a secondary sort key for historical queries", the downstream consumer has to re-parse English to get a label back out.

Rejected alternative: Anthropic's tool-use JSON-schema enforcement. It would give us stricter JSON but (a) constrains future provider swap — not every provider has an equivalent — and (b) doubles the test surface, because the SDK call shape for tool-use is different from a plain completion. For Phase 1 the prompt-and-parse path works reliably on Haiku and costs one retry in the occasional bad case.

### `SemanticType` is four values, deliberately

The first draft had eight (identifier, foreign_key, dimension, measure, timestamp, status, description, other). Trimmed to four (identifier, dimension, measure, other). Reasoning:

- `FOREIGN_KEY` is **deterministic** from postgres metadata. The `relationship-mapping` capability will know which columns are FKs from the connector's output; letting the LLM guess invites wrong answers we already have a correct answer for.
- `TIMESTAMP` is recoverable from the SQL `data_type` (`timestamp`, `timestamptz`, `date`, `time`). Encoding it as a semantic type duplicates information already on the `Column`.
- `STATUS` and `DESCRIPTION` are subtypes of dimension. A status column (`order_status`) and a description column (`product_notes`) are both categorical/descriptive attributes; splitting them buys nothing a consumer can act on.

**Extending the enum later is easy; deprecating a value is not.** A downstream consumer that branched on `STATUS` breaks silently when we collapse it into `DIMENSION`. Starting with the smaller set and adding additively when concrete need surfaces is the lower-risk path.

`OTHER` is the escape hatch. If a column genuinely doesn't fit identifier/dimension/measure — say a raw JSONB blob — the LLM can still classify it without guessing wrong.

### `generate(prompt, system) -> str` is deliberately narrow

The `LLMClient` interface has one method, two inputs, one output. No streaming, no tool-use, no multi-turn, no token-count return. The narrower the interface, the easier the LiteLLM swap.

Every LLM provider exposes a one-shot chat completion. Streaming and tool-use are where provider APIs diverge sharply. By declaring the interface at the lowest common denominator, the LiteLLM swap becomes: write `LiteLLMClient(LLMClient)`, update one import. No caller code changes.

The cost: if a future capability genuinely needs streaming (e.g. showing partial progress in an agent UI), we widen the interface then. The widening lives in the `llm-client` spec as a `MODIFIED Requirements` delta; it doesn't ripple outward.

### SDK handles retries, we don't

`anthropic.AsyncAnthropic(max_retries=2)` ships with retry-with-exponential-backoff on 429s and 5xx. We accept that and do not wrap it in our own retry loop. The one retry we **do** implement is at a different layer: the parse-retry in `DescriptionEngine`, which re-prompts the model with a "return only JSON" reminder when the response body doesn't parse.

Separating the two is the right shape. HTTP retries are a transport concern — the SDK knows best. Parse-retries are a product concern — we own the prompt shape, so we own the reminder text.

### No API key in Sonar code

`AnthropicClient.__init__` takes an optional `LLMConfig` and nothing else. The API key is read from `ANTHROPIC_API_KEY` by the Anthropic SDK. We never pass `api_key=` to `AsyncAnthropic`, never read the env var ourselves, never log it, never accept it via our constructor. The test `test_constructor_rejects_api_key_kwarg` guards this: passing `api_key="sk-test"` must raise `TypeError`.

Rationale: secret handling is a compliance concern. The fewer code paths that touch the key, the smaller the audit surface. Making the SDK the single reader means rotating the key is an env-var change, not a Sonar config change.

### Bounded concurrency via `asyncio.Semaphore`, not a thread pool or rate-limiter

`describe_database` fans out over N tables. Naïve `asyncio.gather` would fire all N requests at once, hit Anthropic's rate limit, and the SDK would serialise them via 429-retries anyway — wasting wall-clock on bounces. A proper rate-limiter (token bucket) is overkill for Phase 1 and requires knowing the provider's actual limits. A thread pool is wrong shape: every worker would just await an async call.

`asyncio.Semaphore(config.max_concurrent_calls)` — default 5 — is the minimum viable bound. Each `describe_table` coroutine `async with`s the semaphore before making the request. Peak in-flight calls is provably ≤ N. When real usage reveals the actual ceiling, we revisit.

The test (`test_concurrency_bound`) instruments a `FakeLLMClient` with a concurrency counter and asserts the peak never exceeds the cap. It catches the failure mode where someone later reaches for `asyncio.gather` without the semaphore and thinks the tests still pass.

### `return_exceptions=True` is a product decision

`asyncio.gather(..., return_exceptions=True)` means "don't cancel siblings on the first failure; return exceptions as return values instead." For a 40-table scan where one table's LLM response is malformed twice in a row, this is the right default — the other 39 descriptions are useful. The failing table lands in the result dict with value `None`.

The alternative (fail-fast: default `gather` behaviour) would throw away 39 successful calls on one edge-case failure. The caller can always filter Nones if they want stricter semantics; they can't recover work that was cancelled.

The return type `dict[tuple[str, str], TableDescription | None]` signals the partial-success shape directly — a caller pattern-matching on the optional is a type-checker-enforced reminder to handle the None case.

### Logging at the boundary, never payloads

Two loggers: `sonar.engine.llm` (one INFO record per LLM call with model/tokens/latency) and `sonar.engine.describe` (one INFO record per `describe_table` with schema/table/columns_count/outcome). **Neither logs prompt or response content.**

Row samples can contain PII. Prompts contain the samples. Responses describe the samples. Logging any of them creates a PII leak at a place no consumer is looking — the operator's log aggregator. The tests explicitly verify this: the `caplog`-based assertions look for the sample values in every string field of every emitted record and fail if they appear.

Token counts and latency are safe: they're aggregate metadata, not content. They're what an operator actually wants on a dashboard.

### `FakeLLMClient` beats `AsyncMock` for the engine tests

`tests/test_llm_client.py` patches `anthropic.AsyncAnthropic` with `AsyncMock` — appropriate, because those tests are about the SDK call shape. `tests/test_description_engine.py` uses a hand-rolled `FakeLLMClient(LLMClient)` instead. Why:

- **The engine's contract is against `LLMClient`, not against Anthropic.** Mocking Anthropic in engine tests couples the test to a detail the engine shouldn't know about.
- **Concurrency tracking needs real state.** `FakeLLMClient.peak_concurrent` is updated under a real `asyncio.Lock` as coroutines enter and leave `generate`. `AsyncMock` has no equivalent — patching in a side-effect that locks, increments, sleeps, and decrements is more code than the fake.
- **Per-prompt response selection is cleaner.** The partial-failure test needs to return malformed JSON for `public.t2` and valid JSON for the other four tables. A `response_for` callable on the fake expresses that in four lines. An `AsyncMock` `side_effect` callable would do the same but with less readable plumbing.

General principle: mock at the abstraction boundary of the code under test, not one layer below.

---

## Relationship Mapping

The third real capability. Consumes `list[Table] + list[ForeignKey]` from the Postgres connector and returns one unified `list[Relationship]` — declared FKs plus naming-heuristic inferences. No class, no state, no I/O, no LLM. Pure function in a flat module at `src/sonar/relationships.py`. The non-obvious parts:

### Why flat, not under `engine/` or `connectors/`

The initial scaffold (`src/sonar/engine/relationships.py`) grouped this with LLM work. Wrong placement for what it actually does:

- `engine/` is for LLM-backed inference. This module never calls an LLM — it runs a regex and a dict lookup.
- `connectors/` is for database I/O. This module never opens a connection — it operates on already-materialised `Table` and `ForeignKey` instances.

Placement should reflect the module's actual dependencies. A flat `src/sonar/relationships.py` has no implied LLM or I/O coupling; readers find exactly what they expect. If it later grows (transitive closure, cardinality analysis, second inference strategy) it can split into a subpackage then. Premature grouping by association ("it's about databases") hides the purity.

### Two decisions deliberately cut under freeze discipline

The first draft had a second inference rule ("any non-PK column whose name matches a single-PK owner in the same schema") and a `confidence: float` field on `Relationship`. Both were cut before the change was proposed. Applying freeze discipline meant asking for each:

- **Who is the next named consumer that will read this?** For rule 2, no roadmap change mentions joins on non-`_id` columns — the roadmap's only concrete example is `user_id → users.id`, which rule 1 covers. For `confidence: float`, with one rule there's one "inferred" population — the field would be constant, redundant with `kind`.
- **What concrete trigger brings it back?** For rule 2: the first `mcp-server` consumer reporting a measurable gap on non-`_id` join columns (e.g. `status → statuses.status`). For `confidence`: a second rule producing a real gradient, or a cardinality/join-success measurement scoring each edge.

Both decisions are parked in `design.md` Open Questions with revival triggers, not deleted from history. Adding a field later is additive and cheap (JSON consumers read by name; missing fields decode as `None`). Removing a field after consumers depend on it is expensive. Freeze discipline's "minimum interface for the next consumer" rule points at the lower-cost-of-reversal path.

### Declared-blocks-inference as a set, not a dedupe pass

The invariant "inference never overrides a declared edge" has two implementations:

1. **Post-hoc dedupe:** produce both populations, then drop inferred edges whose `(source_schema, source_table, source_column)` appears in declared.
2. **Pre-filter via a set:** build `_declared_source_set` up front, skip any column in that set during inference iteration.

Option 2 is what the module does. The two produce identical output, but option 2 makes the invariant visible at the point where it matters — the inference loop's first guard clause reads "if this column already has a declared edge, skip." Option 1 would split the invariant across two passes. The set lookup is O(1), so the performance difference is irrelevant; the clarity difference is not.

Notably we don't dedupe by `(source → target)` pair. A declared FK `orders.user_id → users.user_id` and a hypothetical inferred `orders.user_id → users.id` both have the same source column; the column-level block naturally silences the inferred one. Target-level dedupe would introduce edge cases (what if the rule eventually points to a different target than the declared edge?) that we don't need yet.

### Same-schema only, naive plural only

Both decisions are false-positive mitigations, not feature limits:

- **Cross-schema inference is off.** Multi-schema databases often share column names coincidentally (`schema_a.users.id` and `schema_b.users.id` may be unrelated). Declared FKs for deliberate multi-schema relationships are the norm. The false-positive cost is high, so we skip the space entirely.
- **Only `stem + "s"` pluralisation.** English plural normalisation (`person↔people`, `category↔categories`, `mouse↔mice`) is a rabbit hole with its own library dependencies. We handle `user↔users` and document the miss for the rest. When a real scan misses a pattern we care about, we add an explicit stem-map (hand-curated, ~10 entries), not a library.

The `Revisit when` triggers on both decisions are concrete — the first real-user-schema false-positive measurement and the first observed pluralisation miss. Until then, missing an edge is recoverable (declare the FK); adding a false edge pollutes the graph in ways that are harder to audit.

### Why `_id`-suffix match but accept `id` or `<stem>_id` as PK

The rule has an asymmetry that's worth explaining. Source column: must end in `_id`. Target column: must be named `id` or `<stem>_id`. Why both options on the target?

Two FK-naming conventions dominate real schemas:

1. **Global `id` on every table.** `users.id`, `orders.id`, FKs reference `id`. Most common in Rails/Django-style ORMs.
2. **Scoped PKs on every table.** `users.user_id`, `orders.order_id`, FKs reference the named PK. Common in hand-rolled schemas and older enterprise designs.

Accepting both matches the two conventions without inventing a third. Rejecting a candidate with any other PK name is deliberate — if the PK is `uuid` or `pk` or `users_pk`, we don't have enough signal to know the column relationship, so we emit nothing rather than guess.

The single-column PK constraint is the other half of the rule. Composite PKs as inference targets are ambiguous (which column is the referent?); declared FKs handle composite correctly because `position_in_unique_constraint` aligns them. Inference doesn't get that alignment signal, so it doesn't try.

### Deterministic ordering as a baseline property

Declared edges come back in input order (which `postgres-connector` already makes deterministic via `ORDER BY` in the SQL). Inferred edges are sorted by `(source_schema, source_table, source_column)`. The combined list is `declared + inferred`.

This is not about the `map_relationships` caller — it's about `context-index` (the next change), which will persist this list to disk. If the order churns between scans, snapshot diffs become noise and the on-disk file looks like it changed when nothing meaningful did. Deterministic output is a free win when the rule is "input order + sort the derived part" — no performance cost, no extra code, reader-friendly tests.

### One INFO log record per call — counts only, no column values

The logging contract mirrors the description engine's: one record per `map_relationships` call on logger `sonar.relationships`, level `INFO`, `extra={"declared": N, "inferred": M, "tables_scanned": T}`. No per-edge logging (would be O(edges) noise), no column values (no PII risk in this module, but the "no row content in logs" contract from the engine is the repo-wide default).

The test (`test_logging_contract`) explicitly scans `record.__dict__` for a string field from the input tables and asserts it doesn't appear. That check is cheap insurance — it catches the failure mode where someone later adds a `"%s"`-style debug message that accidentally formats a `Column` and leaks the name into the record.

### Pure tests, no Docker, no async

`tests/test_relationships.py` is 14 synchronous unit tests built with two small helpers: `_table(schema, name, cols_spec)` and `_fk(...)`. No `pytest-asyncio`, no `conftest` fixtures, no database container. Every scenario in the spec is driven by literal table/FK constructions in the test function itself.

This was worth the explicit "pure unit only" design decision because the Postgres connector's integration tests need the Docker container running and share the session-scoped `connector` fixture. Coupling a pure-function module's tests to a live database would slow the feedback loop for no coverage gain. 100% coverage on `relationships.py` is trivially achievable with constructed inputs — the function is deterministic over its arguments, period.

General principle: the boundary between unit and integration tests should follow the module's actual I/O surface. A pure module gets pure tests, even if the repo mostly uses integration tests elsewhere.

---
