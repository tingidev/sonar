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
