# Learnings

Technical explanations of what we're building and why, written as we go. Each capability section follows a fixed template (see `CLAUDE.md`): *What we're building* → *Architecture* → *Key decisions* → *Implementation details* → *What goes wrong* → *Decisions made*. Opening subsections establish the mental model; implementation details are dense and skippable on first read.

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

### What we're building

The first real capability. Scans a live Postgres database and returns its structure as immutable Python objects: `Table`s with their `Column`s, `ForeignKey`s between them, and small row samples for each table. Downstream stages (LLM descriptions, relationship graphs, MCP context) all feed off this output — it's the pipeline's single source of ground truth about shape.

### Architecture

- **Inputs:** a Postgres connection string, optional schema filter.
- **Outputs:** `list[Table]` + `list[ForeignKey]` from `discover_tables` / `discover_relationships`; `list[dict]` row samples per table from `sample_table`.
- **Shape decisions:**
  1. **Async, single connection, context-managed.** `async with PostgresConnector(url) as conn: ...` opens one `AsyncConnection` on `__aenter__`, closes it deterministically on `__aexit__`, raises `RuntimeError` if public methods are called outside the context. No pool — a scan is a short serial sequence of queries with no concurrency to amortise.
  2. **Async from the first module**, even though a scan is one-shot, because every downstream consumer is async. Mixing sync and async here would force an `asyncio.run` wrapper at every call site or split the codebase into a sync island.
  3. **`information_schema` for metadata, not `pg_catalog`.** SQL-standard, portable, permission-scoped in exchange for a shared mental model.

### Key decisions

- **`information_schema` vs `pg_catalog`.** Postgres exposes schema metadata two ways. `pg_catalog` is Postgres's internal bookkeeping — fast, complete, vendor-specific. `information_schema` is the SQL-standard view over `pg_catalog` — portable, slightly slower, and permission-scoped (silently hides tables the role lacks `USAGE` on). We chose `information_schema` for portability and a standard mental model now. The permission-scoping concern is parked for the `mcp-server` change, which will know the permission shape of real customer DBs.

- **Async context manager over alternatives.** Per-method connect/disconnect pays a handshake for every query. A connection pool is overkill for a serial three-query scan. A single connection opened in `__init__` leaks on any exception between construction and explicit close, and async `__init__` is impossible — you'd need a factory `classmethod` and the syntax becomes uglier than the context manager it was trying to avoid. The context manager makes the resource lifetime visible at the call site, which is exactly where the reader needs it.

- **Async API for one-shot scans.** Within the connector there is never a second concurrent query, so async buys nothing internally. It buys something externally: the whole pipeline stays in one event loop. Every caller — description engine, MCP server, context index — is async, and `psycopg3` has native async support, so the cost is one `async with` and zero runtime.

### Implementation details

- **`dict_row` row factory.** `psycopg3` returns tuples by default — positional access, column order matters to the caller. `psycopg.rows.dict_row` makes cursors yield `dict[str, Any]` instead. Two payoffs: the `discover_tables` grouping loop refers to columns by name (`row["table_name"]`) so the query can evolve without rewriting Python, and `sample_table` returns what downstream code wants anyway — dicts ready to `json.dumps`. Set per-cursor, not per-connection, so cursors that don't need it stay on the default tuple shape.

- **`udt_name` fallback for ARRAY and USER-DEFINED.** `information_schema.columns.data_type` is the SQL-standard name, which for non-standard types is useless: it returns the literal string `"ARRAY"` for any array and `"USER-DEFINED"` for any enum or domain. An LLM reading `"data_type: ARRAY"` learns nothing — array of what? The companion column `udt_name` is Postgres's own type name (`_text`, `_int4`, or the enum's own name like `order_status`). We swap to `udt_name` exactly when `data_type` is `ARRAY` or `USER-DEFINED`, leave the standard name alone otherwise. Result: the LLM sees `uuid`, `timestamp with time zone`, `numeric`, `_text`, `order_status` — all informative.

- **`position_in_unique_constraint` join for composite FKs.** The query shape most people write first joins `referential_constraints` → `key_column_usage` twice (source side, target side) on `(constraint_name, schema)` and aligns source and target columns by name. That works for simple FKs where the referenced column has the same name in both tables; it breaks for composite FKs where the names differ. The correct join uses `position_in_unique_constraint` — every row in `key_column_usage` for a referencing column carries an integer saying "this is the Nth column of the target's unique constraint." Join the target side on `ordinal_position = position_in_unique_constraint` and the alignment is positional, not nominal. A two-column FK `(a, b) → (x, y)` produces two rows `a → x` and `b → y`, correct regardless of column names.

- **`psycopg.sql.Identifier` even for trusted inputs.** `sample_table` takes `schema` and `table` strings from prior discovery — trusted, no SQL-injection concern. We still compose the query with `Identifier(schema)` and `Identifier(table)` rather than f-string interpolation, because `Identifier` handles reserved words (`SELECT * FROM "order"`), mixed case (`"MyTable"`), and embedded quotes for free. "Trusted input" is a license to skip *parameter* quoting, which is a different mechanism — it's not a license to skip identifier quoting. `psycopg.sql.Literal(limit)` covers the integer limit for the same reason.

### What goes wrong

- **Silent table omission under `information_schema`.** The role running the scan sees only tables it has `USAGE` on. No error is raised for excluded tables; an operator expecting N tables and getting N-1 has no signal pointing at permissions. Production deployments will need the MCP server to surface "scope visible to this role" explicitly.

- **Unknown `data_type` values.** If Postgres adds a new type class we haven't handled, the `udt_name` swap won't trigger (we only swap on `ARRAY` / `USER-DEFINED`) and the LLM gets the raw standard name. Silent degradation rather than a loud error.

- **Composite-FK join drift.** If someone later refactors the FK query and drops the `position_in_unique_constraint` join (maybe thinking it's redundant), composite FKs with differently-named columns start aligning by accidental ordering. The integration test fixture (`tests/fixtures/init.sql`) seeds a composite FK specifically to catch this regression.

### Decisions made

- `information_schema` (portable, permission-scoped) over `pg_catalog`.
- One `AsyncConnection` per scan, managed by async context manager.
- `psycopg3` with `dict_row` row factory on introspection queries.
- `udt_name` fallback for ARRAY / USER-DEFINED types.
- `position_in_unique_constraint` join for composite FK alignment.
- `psycopg.sql.Identifier` / `Literal` for all identifier / literal composition, even on trusted input.
- Async API even for one-shot scans, to keep the pipeline in one event loop.

---

## LLM Description Engine

### What we're building

The second real capability. Takes a `Table` + its row samples and returns a `TableDescription` — a structured ontology, not a paragraph. Downstream consumers (relationship inference, MCP context, agents) never see free-form text: they see typed fields they can filter, aggregate, and reason over. Split into two capabilities: `llm-client` (a thin async provider abstraction) and `description-engine` (the semantic layer that uses it).

### Architecture

- **Inputs:** a `Table` (from the connector) + a `list[dict]` of row samples.
- **Outputs:** a `TableDescription` (frozen dataclass) carrying a table-level description, grain, domain hints, confidence, and a `tuple[ColumnDescription, ...]` — each column with its semantic type (`IDENTIFIER` / `DIMENSION` / `MEASURE` / `OTHER`), PII risk (`NONE` / `LOW` / `HIGH`), and confidence.
- **Shape decisions:**
  1. **Two capabilities, not one.** `llm-client` is a minimal `generate(prompt, system) -> str` abstraction with `AnthropicClient` as the concrete implementation; `description-engine` never imports the Anthropic SDK. Future LLM-using features reuse the same client; a LiteLLM swap becomes a `MODIFIED Requirements` delta on `llm-client` with zero ripple.
  2. **Structured ontology, not prose.** The LLM returns JSON matching a documented schema. Parsing it and constructing the frozen dataclass *is* the validation — a hallucinated `semantic_type = "widget"` fails the `SemanticType("widget")` constructor and drops into the parse-retry path.
  3. **Bounded concurrency, fail-soft per table.** `describe_database` runs N tables under a semaphore-bounded `asyncio.gather(..., return_exceptions=True)`. One failed table lands in the result dict as `None`; the other N-1 descriptions survive.

### Key decisions

- **Two capabilities, not one.** The temptation was to put the Anthropic SDK usage inside `DescriptionEngine` directly. Rejected. (1) `llm-client` will be swapped for LiteLLM before public release — if it lived inside `description-engine`, every future LLM-using capability would need its own provider wiring; with the split the swap is a local spec delta on `llm-client`. (2) The two concerns evolve differently — `llm-client` wants a stable narrow surface; `description-engine` wants a rich vocabulary (semantic types, PII risk, grain, domain hints). Separate specs keep each requirement list coherent. Cost: one extra file and a type annotation. Worth it.

- **Structured ontology over prose.** The LLM returns JSON matching a documented schema; we parse and construct a frozen `TableDescription`; dataclass construction *is* the validation. Rejected alternatives: (a) ask for prose and post-hoc classify — prose is irreducibly lossy, once the model writes "this column stores identifiers but also acts as a secondary sort key" downstream consumers have to re-parse English to get a label back; (b) Anthropic tool-use JSON-schema enforcement — gives stricter JSON but constrains provider swap (not every provider has the equivalent) and doubles the test surface (tool-use call shape differs from plain completion). Prompt-and-parse works reliably on Haiku and costs one retry in the occasional bad case.

- **`SemanticType` is four values, deliberately.** First draft had eight (`IDENTIFIER`, `FOREIGN_KEY`, `DIMENSION`, `MEASURE`, `TIMESTAMP`, `STATUS`, `DESCRIPTION`, `OTHER`). Trimmed to four. `FOREIGN_KEY` is deterministic from Postgres metadata — letting the LLM guess invites wrong answers we already have a correct answer for. `TIMESTAMP` is recoverable from the SQL `data_type`. `STATUS` and `DESCRIPTION` collapse into `DIMENSION` — splitting them buys nothing a consumer can act on. **Extending the enum later is additive and cheap; deprecating a value after downstream consumers branch on it is expensive.** `OTHER` is the escape hatch.

- **SDK handles HTTP retries, engine handles parse retry.** `anthropic.AsyncAnthropic(max_retries=2)` ships retry-with-backoff on 429s / 5xx — we accept that and don't wrap it. The one retry we *do* implement is at a different layer: `DescriptionEngine.describe_table` re-prompts with a "return only JSON" reminder when the response doesn't parse. Transport retries are the SDK's concern; parse retries are a product concern because we own the prompt shape.

- **No API key in Sonar code.** `AnthropicClient.__init__` takes an optional `LLMConfig` and nothing else. The API key is read from `ANTHROPIC_API_KEY` by the Anthropic SDK. We never pass `api_key=`, never read the env var ourselves, never log it, never accept it via our constructor. A test asserts that passing `api_key=` raises `TypeError`. Rationale: the fewer code paths that touch the key, the smaller the audit surface — rotating it becomes an env-var change, not a Sonar change.

- **Bounded concurrency via `asyncio.Semaphore`.** Naive `asyncio.gather` would fire all N table requests at once and hit Anthropic's rate limit; the SDK would serialise them via 429-retries anyway, wasting wall-clock. A proper token-bucket rate-limiter is overkill for Phase 1 (requires knowing the provider's actual limits). `asyncio.Semaphore(config.max_concurrent_calls)` is the minimum viable bound: each `describe_table` `async with`s it before the call. A test instruments a `FakeLLMClient` with a concurrency counter and asserts the peak never exceeds the cap — catches the regression where someone later drops the semaphore.

- **`return_exceptions=True` is a product decision.** For a 40-table scan where one table's LLM response is malformed twice, fail-fast would throw away 39 successful calls on one edge-case failure. We want the 39 useful descriptions; the caller can filter `None`s if they want stricter semantics. The return type `dict[tuple[str, str], TableDescription | None]` surfaces the partial-success shape directly — a caller pattern-matching on the optional is a type-checker-enforced reminder to handle the `None` case.

- **Logging at the boundary, never payloads.** Two loggers: `sonar.engine.llm` (one INFO per LLM call with model / tokens / latency) and `sonar.engine.describe` (one INFO per `describe_table` with schema / table / columns_count / outcome). Neither logs prompt or response content. Row samples can contain PII; prompts contain samples; responses describe samples — logging any creates a PII leak at a place no consumer is looking. Tests explicitly scan every string field of every emitted record for sample values and fail if they appear.

### Implementation details

- **`StrEnum` for zero-boilerplate JSON round-trip.** Python 3.11's `enum.StrEnum` is `str` and enum simultaneously. `json.dumps({"semantic_type": SemanticType.IDENTIFIER})` produces `{"semantic_type": "identifier"}` with no custom encoder — the enum *is* the string. Parse is `SemanticType(loaded["semantic_type"])`. Plain `Enum` would need a `default=` encoder hook and an explicit lookup on read; `IntEnum` would force opaque numeric wire values.

- **Name-alignment check on LLM column payloads.** `_parse_table_description` zips input `columns` with `cols_payload` from the LLM. Count mismatch is caught; the system prompt instructs the model to preserve order — but a *reordered* response used to produce structurally valid, semantically wrong `ColumnDescription`s (semantic type attached to the wrong column). The parser now raises when `cols_payload[i]["name"] != source_col.name`; the error flows through the existing one-retry path. Added as a hardening fix after the pre-change-4 cross-cutting audit.

- **Narrow `generate(prompt, system) -> str` interface.** The `LLMClient` ABC has one method, two inputs, one output. No streaming, no tool-use, no multi-turn, no token-count return. Every LLM provider exposes a one-shot chat completion; streaming and tool-use are where provider APIs diverge sharply. Widening later is a `MODIFIED` spec delta; widening now pre-pays for features no named consumer has asked for.

- **`FakeLLMClient` beats `AsyncMock` for engine tests.** `tests/test_llm_client.py` patches `anthropic.AsyncAnthropic` with `AsyncMock` — appropriate, those tests are about SDK call shape. `tests/test_description_engine.py` uses a hand-rolled `FakeLLMClient(LLMClient)` because (a) the engine's contract is against `LLMClient`, not Anthropic — mocking Anthropic couples the test to a detail the engine shouldn't know about; (b) concurrency tracking needs real state (`peak_concurrent` updated under an `asyncio.Lock`), which `AsyncMock` can't express cleanly; (c) per-prompt response selection (malformed for `public.t2`, valid for the other four tables) is four lines on the fake vs tangled `side_effect` plumbing. General principle: **mock at the abstraction boundary of the code under test, not one layer below.**

### What goes wrong

- **LLM reorders columns without changing count.** Spec says columns are returned in input order; system prompt instructs preservation; count check catches omissions. A reordered response used to silently produce wrong descriptions. Fixed by the name-alignment assertion. The class of bug (silent structurally-valid-but-semantically-wrong corruption) is the failure mode to watch — any new parse step needs a similar consistency check.

- **Anthropic rate-limit ceiling hit on large scans.** With `max_concurrent_calls=5` and ~40 tables, we've never hit the ceiling in Phase 1. Customer scans of 500+ tables may. The semaphore is the minimum viable bound, not a tuned limit; a token-bucket rate-limiter becomes warranted when the first real-scan telemetry shows sustained 429 activity.

- **Partial-failure result dict needs disciplined consumer handling.** `dict[tuple[str, str], TableDescription | None]` is honest about the shape, but a careless `for desc in results.values(): desc.name` crashes on the first failed table. The type annotation is a reminder, not a guarantee — downstream `context-index` will need to pattern-match the optional explicitly.

- **PII in logs from a future code path.** Current tests verify sample values don't leak into log records. The invariant is "no prompt or response content in any log emitted by any module in this capability" — a new log added in a refactor needs the same scrutiny. The tests catch current shape; a reviewer audit catches new shapes.

### Decisions made

- Split into `llm-client` and `description-engine` — two capabilities, two specs.
- Structured JSON output with dataclass construction as validation; never prose.
- Four `SemanticType` values (`IDENTIFIER`, `DIMENSION`, `MEASURE`, `OTHER`) — extend additively when concrete need surfaces.
- Haiku 4.5 (`claude-haiku-4-5-20251001`) for Phase 1.
- SDK owns HTTP retries; engine owns parse retry.
- API key lives only in `ANTHROPIC_API_KEY` env var; never in Sonar code paths.
- `asyncio.Semaphore`-bounded fan-out with `return_exceptions=True`.
- INFO logs carry counts and metadata only — never prompts, responses, or sample values.
- `FakeLLMClient` over `AsyncMock` for engine-level tests.
- Name-alignment assertion on LLM column payloads (post-audit hardening).

---

## Relationship Mapping

### What we're building

The third real capability. Consumes `list[Table]` + `list[ForeignKey]` from the Postgres connector and returns one unified `list[Relationship]` — declared FKs plus naming-heuristic inferences. No class, no state, no I/O, no LLM. Pure synchronous function in a flat module at `src/sonar/relationships.py`. Downstream consumer is `context-index` (change #4), which persists the combined graph as agent-facing context.

### Architecture

- **Inputs:** `list[Table]` + `list[ForeignKey]` from the connector.
- **Outputs:** `list[Relationship]` — each a frozen dataclass carrying `(source_schema, source_table, source_column)`, `(target_schema, target_table, target_column)`, and `kind: RelationshipKind` (`DECLARED` or `INFERRED`).
- **Shape decisions:**
  1. **Pure sync function, flat module.** `map_relationships(tables, foreign_keys) -> list[Relationship]` — no class, no state, no I/O. The module sits at `src/sonar/relationships.py`, not under `engine/` or `connectors/`, because it has no LLM dependency and no database dependency.
  2. **Declared edges anchor the graph; inference fills gaps.** A set of declared source columns pre-filters the inference loop, so an inferred edge can never override a declared one. The invariant lives at the point where it matters — the inference guard clause — not as a post-hoc dedupe pass.
  3. **One inference rule, deliberately minimal.** `<stem>_id` suffix on a non-declared column → same-schema table named `<stem>` or `<stem>s` + single-column PK named `id` or `<stem>_id`. Second rule and `confidence: float` were cut under freeze discipline; both parked in `design.md` Open Questions with concrete revival triggers.

### Key decisions

- **Flat `src/sonar/relationships.py`, not under `engine/` or `connectors/`.** The initial scaffold grouped this with LLM work. Wrong placement for what it actually does: `engine/` is for LLM-backed inference (this module never calls an LLM); `connectors/` is for database I/O (this module never opens a connection — it operates on already-materialised `Table` and `ForeignKey` instances). Placement should reflect the module's actual dependencies; a flat module has no implied LLM or I/O coupling so readers find exactly what they expect. If the capability later grows (transitive closure, cardinality analysis) it can split into a subpackage then. Premature grouping by association hides the purity.

- **Cuts under freeze discipline: rule 2 and `confidence: float`.** The first draft had a second inference rule ("any non-PK column whose name matches a single-PK owner in the same schema") and a `confidence: float` field on `Relationship`. Both cut before the change was proposed. Applying freeze discipline meant asking for each: *who is the next named consumer that will read this?* For rule 2, no roadmap change mentions joins on non-`_id` columns — the roadmap's only concrete example is `user_id → users.id`, which rule 1 covers. For `confidence`, with one rule there's one "inferred" population — the field would be constant, redundant with `kind`. Both parked in `design.md` Open Questions with revival triggers, not deleted. **Adding a field later is additive and cheap; removing one after consumers depend on it is expensive.**

- **Declared-blocks-inference via set, not post-hoc dedupe.** Two implementations would produce identical output: dedupe (run both populations, drop inferred edges whose source column is in declared) vs pre-filter (build `_declared_source_set` up front, skip declared columns during inference iteration). Option 2 is what the module does, because it makes the invariant visible at the point where it matters — the inference loop's first guard reads "if this column already has a declared edge, skip." Dedupe would split the invariant across two passes. Set lookup is O(1) so performance is irrelevant; clarity is not.

- **Same-schema only, naive plural only.** Both are false-positive mitigations, not feature limits. Cross-schema inference is off because multi-schema databases often share column names coincidentally (`schema_a.users.id` and `schema_b.users.id` may be unrelated); deliberate cross-schema relationships are typically declared. Pluralisation is just `stem + "s"` because English plural normalisation (`person↔people`, `mouse↔mice`, `category↔categories`) is a rabbit hole with library dependencies; when a real scan misses a pattern we care about, we add an explicit stem-map (hand-curated, ~10 entries) rather than import a library.

- **PK-name acceptance: `id` or `<stem>_id`, nothing else.** Two FK-naming conventions dominate real schemas: (1) global `id` on every table — `users.id`, `orders.id`, FKs reference `id` — common in Rails/Django ORMs; (2) scoped PKs — `users.user_id`, `orders.order_id`, FKs reference the named PK — common in hand-rolled schemas. Accepting both matches the two conventions without inventing a third. Rejecting any other PK name (`uuid`, `pk`, `users_pk`) is deliberate — without a naming signal we don't have enough information to guess.

- **Single-column PK constraint on inference targets.** Composite PKs as inference targets are ambiguous (which column is the referent?). Declared FKs handle composite correctly because `position_in_unique_constraint` aligns them; inference doesn't get that alignment signal, so it doesn't try. Missing a composite-PK inference is recoverable by declaring the FK; adding a wrong composite-PK edge pollutes the graph.

- **Deterministic ordering.** Declared edges in input order (connector already sorts its SQL by `ORDER BY`); inferred edges sorted by `(source_schema, source_table, source_column)`. Combined list is `declared + inferred`. Not for the `map_relationships` caller — for `context-index`, which persists this list to disk. If the order churns between scans, snapshot diffs become noise and the on-disk file looks changed when nothing meaningful did.

### Implementation details

- **Pure tests, no Docker, no async.** `tests/test_relationships.py` is 14 synchronous unit tests built with two small helpers: `_table(schema, name, cols_spec)` and `_fk(...)`. No `pytest-asyncio`, no `conftest` fixtures, no database container. Every scenario in the spec is driven by literal table/FK constructions in the test function itself. Worth the explicit "pure unit only" decision because the Postgres connector's integration tests need Docker and share a session-scoped fixture; coupling a pure-function module's tests to a live database would slow the feedback loop for no coverage gain. 100% coverage on `relationships.py` is trivially achievable with constructed inputs because the function is deterministic over its arguments, period. General principle: **the unit/integration boundary should follow the module's actual I/O surface.**

- **One INFO log record per call — counts only, no column values.** Logger is `sonar.relationships`, level `INFO`, `extra={"declared": N, "inferred": M, "tables_scanned": T}`. No per-edge logging (would be O(edges) noise), no column values. The "no row content in logs" contract carries over from the engine, even though this module has no PII risk. A test explicitly scans `record.__dict__` for a string field from the input tables and asserts it doesn't appear — cheap insurance against a future `"%s"`-style debug message that accidentally formats a `Column` into the log line.

- **Dedupe by source column, not `(source → target)` pair.** A declared FK `orders.user_id → users.user_id` and a hypothetical inferred `orders.user_id → users.id` both have the same source column; the column-level block naturally silences the inferred one. Target-level dedupe would introduce edge cases (what if the rule eventually points to a different target than the declared edge?) that we don't need yet.

### What goes wrong

- **False positives on coincidental column names.** The rule matches any `<stem>_id` column where same-schema `<stem>` or `<stem>s` has a compatible PK. A column named `user_id` in a schema with an unrelated `users` table produces a wrong edge. Declared FKs never get this wrong; inference can. The `Revisit when` trigger is "first real-user-schema false-positive measurement."

- **Pluralisation misses.** `people`, `mice`, `categories`, `children` — none match `stem + "s"`. An `author_id` column pointing at a table called `people` produces no inferred edge. Recoverable (declare the FK), but means the heuristic quietly under-covers in irregular-English schemas.

- **Inference order drift.** If someone later refactors and the inferred list's sort key changes, `context-index` snapshot diffs start churning. Tests pin the ordering via a concrete multi-table example; a regression would fail explicitly.

- **Non-`_id` FK-like columns don't get inferred.** Rule 2 was cut. A column named `status` pointing at `statuses.status`, or `country` pointing at `countries.code`, emits no edge. Recoverable by declaring. Revival trigger: first `mcp-server` consumer reporting a measurable gap on these cases.

### Decisions made

- Flat `src/sonar/relationships.py`, not under `engine/` or `connectors/`.
- Pure synchronous `map_relationships(tables, foreign_keys) -> list[Relationship]`.
- Declared FKs anchor the graph; heuristics fill gaps and never override declared.
- One inference rule (`<stem>_id` suffix, same-schema, `id` or `<stem>_id` single-column PK); second rule and `confidence: float` parked.
- Same-schema only; `stem + "s"` pluralisation only; PK acceptance list `id` or `<stem>_id`.
- Declared-blocks-inference via pre-filter set, not post-hoc dedupe.
- Deterministic ordering: declared in input order, inferred sorted by source triple.
- One INFO log record per call with counts; no per-edge logs; no column values.
- Pure unit tests — no Docker, no async, no fixtures.

---

## Context Index

### What we're building

The fourth real capability — the pipeline terminus. Composes the three prior capability outputs (`Table`s, `TableDescription`s, `Relationship`s) into a single frozen `ContextBundle` and writes it as four per-capability JSON files under `.sonar/`. Also hosts `sonar scan <dsn>` — the first end-to-end CLI command, wiring connector → sampling → description engine → relationship mapper → bundle writer in a single linear orchestration. Downstream consumer is `mcp-server` (#5), which will parse `.sonar/` files directly at server startup and serve tools against the in-memory bundle without reconnecting to the database.

### Architecture

- **Inputs:** a DSN (CLI) and, at the library layer, a pre-built `ContextBundle`.
- **Outputs:** four files — `meta.json`, `tables.json`, `descriptions.json`, `relationships.json` — under a bundle directory (default `.sonar/`).
- **Shape decisions:**
  1. **Thin composition in memory, per-capability files on disk.** `ContextBundle` holds three parallel collections plus a `BundleMeta` header — no pre-joined "fat" rows. The on-disk layout mirrors the capability boundaries, not the in-memory shape. Each file can evolve independently under one bundle-wide `schema_version`.
  2. **Bundle-wide version, governed by `meta.json`.** One integer governs all four files together; `ContextStore.read()` raises `BundleVersionError` on mismatch. No migration logic in v1 — the field exists so the first breaking change doesn't have to retrofit one.
  3. **`sonar scan` owns orchestration directly in `cli.py`.** No `Pipeline` / `Orchestrator` class. One caller, linear data flow, no reuse surface — an abstraction here would be pure ceremony.

### Key decisions

- **Thin `ContextBundle`, not a pre-joined "fat" row type.** Rejected: an `EnrichedTable` merging `Column` + `ColumnDescription` fields. Fat forces inventing merge rules when either side is missing (a table with no description, a description with columns the table doesn't have — both legitimate Phase 1 states), and it duplicates the upstream dataclasses' fields. Thin keeps the three capability shapes visible in the composed type, and `mcp-server`'s `describe` tool joins on `(schema, name)` at call time — trivial at a few hundred tables. When the join profile ever shows up, the fat shape can be added without touching the thin one. **Widening is additive; narrowing a shipped "fat" type is a migration.**

- **Per-capability files, not one blob.** Three reasons for the split: (1) re-runs on a stable schema churn `descriptions.json` (LLM drift) but leave `tables.json` byte-identical — a signal lost in a single-file layout; (2) the future `sonar scan --only descriptions` lands with a natural seam already in place; (3) on-disk grain matches the code's capability boundaries, so a reader of the repo and a reader of `.sonar/` build the same mental model. Relationships are inherently cross-table so a per-*table* layout would be a hybrid anyway, which weakens that alternative. `meta.json` carries the single version integer that governs all four together.

- **`schema_version: 1` from day one.** Adding the field retroactively means the first breaking shape change also introduces a version field *and* a migration tool in the same commit. One integer now defers exactly that pain. Read-side behaviour is loud and dumb — unsupported versions raise `BundleVersionError`. Migration logic lands the day a non-additive change does, not speculatively.

- **No row samples on disk.** Samples flow connector → engine in memory and are discarded. Rejected: caching 5 rows per table in the bundle for an "offline describe" affordance. The price is writing raw row data — routinely PII — to a file the operator then stores, backs up, and potentially syncs off-host. Keeping samples off disk is the same posture `description-engine` already takes with its log discipline — **PII-off-disk is a pipeline-wide first principle, not a per-module policy.** `mcp-server`'s `sample` tool will open a live DB connection per call instead.

- **Failed descriptions persist as JSON `null`, not omitted.** `descriptions.json` keys *every* table in `tables.json`; the LLM engine's partial-success dict (`TableDescription | None`) round-trips through the file. This preserves the distinction between "scanned but failed" (key present, value null) and "never scanned" (key absent) — the latter being an integrity violation in v1, raised on read. Omitting failures would collapse two legitimate states into one absence and hide real partial success from downstream consumers. Enforced both at write time (encoder emits `null`) and at read time (`_check_integrity` compares the `tables` key set against the `descriptions` key set and raises on asymmetric difference).

- **`"<schema>.<name>"` dict-key encoding, anchored by an upstream guard.** JSON has no tuple-key support; the in-memory `dict[tuple[str, str], ...]` has to be serialised as a string-keyed object. The naïve `"."` separator would be ambiguous if either identifier contained a dot — so we closed that ambiguity upstream: `postgres-connector.discover_tables` and `discover_relationships` now raise `ValueError` if any returned schema or table name contains a literal `"."`. That spec-delta is the cost of the cheap encoding. Alternative considered: a JSON array of `{"schema": ..., "name": ..., "description": ...}` objects — more verbose, less grep-friendly, same robustness. Rejected because operator databases effectively never use dotted identifiers (`pg_catalog` and `information_schema` are dot-free throughout), and surfacing a clear connector-level error is preferable to a silently-corrupted bundle on read. **Reversibility is cheap because the guard makes the invariant loud — a future operator hitting the restriction gets an explicit error, not a mangled file.**

- **Explicit decoders on read, not generic `from_dict`.** Every dataclass has its own hand-rolled `_decode_table`, `_decode_column`, `_decode_table_description`, `_decode_column_description`, `_decode_relationship`. Rejected alternatives: `dataclasses.asdict` inverse via introspection, or a generic `from_dict` helper. Both collapse the I/O boundary into a single magic function that hides where each field actually lives. Explicit decoders are more verbose, but the cost is paid once per dataclass and buys direct control: `StrEnum` fields land in the enum constructor (`SemanticType(...)`, `PIIRisk(...)`, `RelationshipKind(...)`), nested tuples are reconstructed explicitly, and every field's source is grep-visible. When a dataclass grows a field, the decoder change is local and obvious.

- **`ContextStore.read()` returns `None` on missing bundle, raises on corruption.** Parked during design (D-Open-Question) and settled during implementation: a missing bundle directory or missing `meta.json` returns `None` (clean "nothing to read" signal for `mcp-server`); a `meta.json` with the wrong shape or wrong version raises; an orphan or missing description key raises. The rule is "silence for absence, loud for damage." A single caller (`mcp-server`) with a clear contract means the optional return is cheap to pattern-match; collapsing absence and damage into a single exception would force `try/except` sprinkled everywhere the caller cares about "bundle present?"

- **Sync file I/O around an async pipeline.** `ContextStore.write` / `.read` are plain sync functions. `sonar scan` runs the async pipeline inside `asyncio.run(_scan_pipeline(dsn))` and then calls `store.write(bundle)` synchronously once, outside the loop. Rejected: `aiofiles` for consistency. Phase 1 bundle size is O(100 KB); write latency is irrelevant; sync file I/O is one less moving part and keeps the async surface scoped to things that actually benefit from it (DB queries, LLM calls).

- **DSN sanitisation via `format_database_label`, with safe fallback.** `BundleMeta.database` must never carry a password. The helper parses the DSN with `urlparse`, keeps `[user@]host[:port][/dbname]`, and explicitly omits the password component. Pathological DSNs — passwords containing `@` or `/` that confuse `urlparse` — legitimately fall back to the literal `"unknown"`. The contract is "password never leaks to disk," not "host always survives parsing"; relaxing the second guarantee keeps the first absolute. This same helper is reused at the CLI error boundary (see next decision).

- **Error messages at the CLI boundary scrub the raw DSN.** `psycopg.OperationalError`'s `str()` embeds the full connection string including the password. Printing the exception directly to stderr on connect failure would leak credentials into terminals, CI logs, and shell history. `cli._run_scan` catches the exception, computes the sanitised label once, and `str(exc).replace(dsn, label)` before printing. Exception type name is preserved for diagnostic value. An integration test uses a distinctive password (`hunter2`) and asserts neither the password nor the full DSN appears in captured stderr — regression-proofs the scrub. This fix landed as post-audit hardening, not initial implementation.

- **`sonar.cli.AnthropicClient` is the documented monkeypatch seam (D11).** `AnthropicClient` is imported at module scope in `cli.py` — not from inside the `scan` function body — precisely because the integration test patches `sonar.cli.AnthropicClient` with a `FakeLLMClient` factory. Rejected alternatives: env-flag switch (`SONAR_LLM=fake`) leaks a test code path into production; constructor-injection factory adds a module-level seam whose only consumer is the test. The monkeypatch approach keeps the production import graph unchanged and documents the seam as a convention rather than an interface. Speculative? No — the alternatives would have been.

### Implementation details

- **`_json_default` hook for `StrEnum`.** `json.dump` uses `default=_json_default`, which returns `obj.value` for `StrEnum` instances. Three enums ship through the bundle — `SemanticType`, `PIIRisk`, `RelationshipKind` — and none need per-type encoder logic because `StrEnum` *is* `str` at wire level. Decode is symmetric: each enum's constructor (`SemanticType("identifier")`) reconstructs the typed value from its string wire form.

- **Integrity check computes symmetric difference.** `_check_integrity(tables, descriptions)` builds two sets and raises on either side of the difference: orphan keys (description for a table that isn't in `tables.json`) are one exception; missing keys (table without a description entry at all) are another. Both exceptions enumerate the offending keys in sorted order so the error message is actionable. Sort order in the message aids operator debugging — the test suite round-trips a deliberately-corrupted bundle and pins the format.

- **`_bundle_log_extra` emits four integer counts and nothing else.** Logger is `sonar.index`; records carry `tables`, `descriptions_present`, `descriptions_null`, `relationships` — all `int`. No DSN, no description text, no column names, no file paths. The test explicitly scans every log record's `__dict__` for a specific description string from the test fixture and asserts it doesn't appear. Same posture as `description-engine` (no prompts / responses in logs) and `relationship-mapping` (no column values in logs). **Logging discipline is capability-agnostic: if it's not a count, it doesn't go in the record.**

- **Fake LLM client parses the prompt itself.** `tests/test_scan.py`'s `_FakeLLMClient` extracts `Table: <schema>.<name>` and the columns block from the prompt body using two regexes, then synthesises a valid JSON payload whose column names match the prompt. No advance knowledge of the Docker fixture's schema. The fake is completely self-contained — swapping the fixture doesn't require updating the test. Failure injection is a `fail={("public", "orders")}` set; when the prompt's table matches, the fake returns a deliberately malformed string that flows through the engine's existing partial-failure path.

- **Integration tests are `def`, not `async def`.** pytest-asyncio's auto mode wraps every `async def` test in its own event loop; `main()` itself calls `asyncio.run(_scan_pipeline(...))`; nesting `asyncio.run` inside a running loop raises `RuntimeError: asyncio.run() cannot be called from a running event loop`. Keeping the test functions synchronous lets `main()` own its loop. The fake LLM's `generate` stays `async def` because the engine calls it via `await` — only the outer test function is sync.

- **`Path(bundle_dir).mkdir(parents=True, exist_ok=True)` on every write.** The directory is created lazily on first write. Operators point `--bundle-dir` at a path that doesn't exist yet (including nested parents); the store handles it rather than forcing an explicit `mkdir` at the CLI layer. `exist_ok=True` makes the operation idempotent — re-scans overwrite cleanly.

- **Write does not delete stray files; overwrite is scoped to the four expected filenames.** If someone manually drops `junk.json` into `.sonar/`, it survives a `write(bundle)`. Phase 1 deliberately avoids filesystem-level transactions — single-writer, operator-run, `sonar scan` re-runs on crash. The test `test_second_write_overwrites_first` pins the four-file contract but not directory-level cleanup, matching the intent.

### What goes wrong

- **Non-atomic four-file write leaves a half-written bundle on crash.** `ContextStore.write` writes `meta.json`, then `tables.json`, then `descriptions.json`, then `relationships.json`. If the process dies mid-write, the bundle directory contains a new `meta.json` claiming `schema_version: 1` but stale (or missing) companion files. `ContextStore.read()` will either surface a `BundleIntegrityError` (orphan or missing keys) or, worse, silently succeed with a stale file whose contents predate the current `meta.json`. Mitigation in Phase 1: operator re-runs `sonar scan`. Revisit trigger: an always-on writer (daemon, scheduled scanner) — at that point move to write-to-temp-then-rename or a SQLite-backed store.

- **LLM drift churns `descriptions.json` across re-runs, even on a frozen schema.** Intentional — the file is the single-source-of-truth for what the model said this time. But it means `git diff` on a tracked bundle is always noisy; operators committing bundles will see rolling churn. The `.gitignore` entry for `.sonar/` is the opinionated default; operators who need a shared bundle opt in explicitly and accept the diff noise.

- **`schema_version` bump is a flag day for `mcp-server`.** The bundle-wide version means a bump affects all four files simultaneously — readers either understand the whole new format or they don't. When the first non-additive change lands, `mcp-server` has to ship a compatible reader in the same release. Mitigation per D3: keep additive changes strictly additive (new optional fields on existing dataclasses don't bump the version); only bump when a required field changes shape or meaning.

- **Pathological DSN loses its host label.** `format_database_label` falls back to `"unknown"` when `urlparse` can't cleanly extract `host` — which happens for DSNs with `@` or `/` embedded in the password. The `BundleMeta.database` field then carries `"unknown"` instead of `"user@host:5432/db"`. Operator-facing cost: a slightly less useful provenance label. Security benefit: the password cannot leak through a clever parsing edge case. The trade-off is deliberate and pinned by the `test_password_never_appears_even_for_odd_input` test.

- **Dotted-identifier databases fail loudly at the connector boundary.** A schema or table named `foo.bar` causes `discover_tables` / `discover_relationships` to raise `ValueError`. `sonar scan` catches at the CLI layer and exits non-zero with a single stderr line. An operator hits this the first time they run sonar against a schema that legitimately uses dots. Revisit trigger per D7: the first such report — at which point either relax the guard and switch to the array-of-objects encoding (format migration, `schema_version` bump) or document the restriction as permanent.

- **`ContextStore.read()` returning `None` for "directory missing" conflates two states.** Missing `.sonar/` directory and empty-but-existing `.sonar/` directory without a `meta.json` both return `None`. Callers can't distinguish "never scanned" from "bundle partially deleted." In Phase 1 `mcp-server` doesn't need to — "no bundle, run `sonar scan` first" is the same error message either way. When a second caller arrives that does care, splitting into two return values (or an exception for the empty-dir case) is an additive change.

### Decisions made

- Thin `ContextBundle` with parallel collections; no pre-joined "fat" shape.
- Four per-capability JSON files on disk under one bundle-wide `schema_version`.
- `schema_version: 1` from day one; read-side raises `BundleVersionError` on mismatch; no migration logic in v1.
- No row samples persisted — PII-off-disk posture across the whole pipeline.
- Failed descriptions persist as JSON `null`; orphan / missing keys raise `BundleIntegrityError` on read.
- `"<schema>.<name>"` key encoding, made unambiguous by a new `ValueError` guard in `postgres-connector`.
- Explicit per-dataclass decoders on read — no generic `from_dict`.
- `read()` returns `None` for missing bundle, raises for damaged bundle.
- Sync `ContextStore` I/O around the async scan pipeline.
- `format_database_label` strips passwords; falls back to `"unknown"` on pathological input.
- CLI error path scrubs raw DSN out of exception messages before printing to stderr.
- `sonar.cli.AnthropicClient` imported at module scope as the documented monkeypatch seam (D11).
- Integration tests run `def`, not `async def`, to let `main()` own its own event loop.
- Count-only INFO logging on `sonar.index`; no prompts, descriptions, DSNs, or sample values.
- `sonar scan` orchestration lives directly in `cli.py`; no `Pipeline` class.

---
