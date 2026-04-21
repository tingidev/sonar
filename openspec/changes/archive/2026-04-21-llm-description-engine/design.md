## Context

The `postgres-connector` capability (archived `2026-04-21-postgres-schema-discovery`) produces `Table`, `Column`, and `ForeignKey` dataclasses plus JSON-serialisable `list[dict]` row samples. That is raw structure. This change turns it into agent-usable semantic context: what does this table represent, what does each column mean in business terms, which columns are identifiers vs measures vs timestamps vs PII. Downstream changes (`relationship-mapping`, `context-index`, `mcp-server`) all consume the output of this change.

The vault project file positions the differentiator clearly: "the only tool that auto-generates meaning from structure." Prior art — dbt Semantic Layer, WrenAI MDL, `gubaruch/mcp-semantic-layer` — encodes descriptions as a **structured ontology** (typed fields: identifier, dimension, measure, timestamp), not prose. This design commits to that shape. Prose narrative is one field in a dataclass, not the return type.

Two capabilities are introduced in this change because they have different shapes and different evolution paths:

- `llm-client` is a provider-agnostic async abstraction. In Phase 1 it wraps Anthropic directly; at public release it will be swapped to LiteLLM without changing callers. Keeping it a separate capability means the swap is a localised `MODIFIED Requirements` delta, not a rewrite of `description-engine`.
- `description-engine` is the semantic-inference layer. It owns prompt composition, structured-output parsing, and fan-out. It depends on `llm-client` but does not depend on any specific provider.

The existing stub files (`src/sonar/engine/llm.py`, `describe.py`) hold placeholder signatures; they will be rewritten against the decisions below.

## Goals / Non-Goals

**Goals:**

- Pin a **structured** output schema for table and column descriptions — typed fields an agent can reason over, not prose blobs.
- Commit to a single LLM-call choke-point (`LLMClient.generate`) so the rest of Sonar never imports the Anthropic SDK directly.
- Define a deterministic parse/retry/fail path for structured output — LLMs return malformed JSON often enough that silent hallucination is the biggest risk.
- Bound concurrency in `describe_database` so a 50-table scan cannot accidentally exhaust Anthropic rate limits.
- Keep CI tests independent of the Anthropic API. Every test mocks `LLMClient.generate` or the Anthropic HTTP boundary.

**Non-Goals:**

- **No FK-awareness in prompts.** The `Table` passed in carries columns but no foreign-key graph. Relationship-aware descriptions belong to `relationship-mapping` (the next change) — it will call this engine again with richer context if that proves better than a one-shot combined prompt.
- **No persistence.** Descriptions are returned as dataclasses; `context-index` owns the JSON-on-disk format.
- **No prompt-caching optimisation in Phase 1.** Anthropic's prompt-cache would let us share the system prompt across tables, but it adds a second code path for cache-control markers. Deferred — measure first.
- **No streaming.** Descriptions are small (< 1024 tokens), one-shot generation is simpler and gives us the full JSON to validate before returning.
- **No local / non-Anthropic provider.** Phase 1 is Anthropic direct. LiteLLM and local models are deferred to a later change.
- **No row-count- or cardinality-aware prompting.** `Table.row_count` is `None` today. If downstream quality demands it, a later change populates it first.

## Decisions

### D1. Two capabilities, not one

`llm-client` and `description-engine` live in separate spec files because their concerns and evolution paths diverge. `llm-client` will be swapped for LiteLLM before public release; `description-engine` will not. Keeping them separate means that swap is a spec-delta on one capability, not a cross-cutting rewrite.

Alternative considered: one `description-engine` capability with the Anthropic client internal to it. Rejected because it forces every future LLM-using capability (relationship inference, query planning) to re-implement provider wiring.

### D2. `LLMClient` shape: `async generate(prompt, system) -> str`

Single method, two inputs (`prompt` user message, optional `system` system prompt), one output (the assistant text). No streaming, no tool-use, no multi-turn. Rationale: for semantic description the engine has everything it needs in one shot; we don't need the LLM to call tools. Narrowing the interface this much keeps the LiteLLM swap trivial — any provider exposes a one-shot completion.

`LLMClient` is an abstract base (`abc.ABC`) with `generate` marked `@abstractmethod`. `AnthropicClient(LLMClient)` implements it. `LLMConfig` is a frozen dataclass containing `provider`, `model`, `max_tokens`, and `max_concurrent_calls`.

### D3. Anthropic client uses `AsyncAnthropic` with SDK-default retries

`AnthropicClient.__init__` instantiates `anthropic.AsyncAnthropic(max_retries=2)`. `generate` calls `client.messages.create(model=..., system=..., messages=[{"role": "user", "content": prompt}], max_tokens=...)` and returns `response.content[0].text`. The SDK handles transient HTTP errors and 429s internally via exponential backoff; we don't wrap it with our own retry loop.

`ANTHROPIC_API_KEY` is picked up by the SDK from the environment. We never read it ourselves, never log it, never accept it via constructor.

Alternative considered: implement our own retry-and-backoff on top of `anthropic.Anthropic`. Rejected — the SDK's is correct and well-tested; duplicating it adds surface area.

### D4. Structured output via strict JSON + one parse-retry

The description engine prompts the model to return a single JSON object matching a documented schema. The response text is parsed with `json.loads`. On `JSONDecodeError`:

1. Re-call `LLMClient.generate` once with an appended reminder: the original prompt plus `"Your previous response was not valid JSON. Return only a single JSON object matching the schema. No prose, no markdown, no code fences."`
2. If the second attempt also fails to parse, raise `DescriptionParseError` with the offending text truncated to 500 chars.

We deliberately do **not** use Anthropic's tool-use / JSON-schema-enforcement path in Phase 1. Reasons: it constrains model choice (not every future provider supports it), doubles the test surface (mock tool-call shape vs plain text), and strict-JSON-via-prompt works reliably enough on Claude Haiku in practice. If parse failures exceed ~1% in real use, revisit.

### D5. `TableDescription` / `ColumnDescription` dataclass schema

Frozen dataclasses define the contract. The LLM returns JSON, we parse it, we construct the dataclass — constructing the dataclass is the validation.

```python
@dataclass(frozen=True)
class ColumnDescription:
    name: str
    description: str            # one-sentence meaning in business terms
    semantic_type: SemanticType # enum: IDENTIFIER, DIMENSION, MEASURE, OTHER
    pii_risk: PIIRisk           # enum: NONE, LOW, HIGH
    confidence: float           # 0.0-1.0, how sure is the LLM

@dataclass(frozen=True)
class TableDescription:
    schema: str
    name: str
    description: str            # 1-3 sentence narrative of what the table represents
    grain: str                  # what one row represents ("one order line item per product")
    domain_hints: tuple[str, ...]  # e.g. ("e-commerce", "orders")
    columns: tuple[ColumnDescription, ...]
    confidence: float
```

`SemanticType` is deliberately minimal — four values matching the dbt-canonical trio (entity/dimension/measure, renamed IDENTIFIER for clarity) plus an `OTHER` escape hatch. Dropped from an earlier draft: `FOREIGN_KEY` (deterministic from postgres metadata, the LLM should not guess it), `TIMESTAMP` (recoverable from SQL data type), `STATUS` and `DESCRIPTION` (both collapse cleanly into `DIMENSION`). The enum can be extended additively in a later change if downstream consumers show concrete need — easier to add values than to deprecate them.

`SemanticType` and `PIIRisk` are `enum.StrEnum` — lowercase string values so they round-trip through JSON without custom encoders.

The dataclass is frozen and uses `tuple` (not `list`) for the columns and domain_hints so instances are hashable and immutable, consistent with the `postgres-connector` capability's `Table`.

### D6. Prompt strategy

Two prompts live in `src/sonar/engine/_prompts.py` as module-level constants or small builder functions:

- **System prompt**: Sets the role — "You are a data semantics analyst. Return a single JSON object matching the schema given. Never include prose, markdown, or code fences. Be concise. Mark confidence honestly." This is static across all tables, which makes it a clean candidate for Anthropic prompt caching in a later optimisation pass.
- **User prompt builder** `build_table_prompt(table, samples) -> str`: serialises the table as (a) its schema-qualified name, (b) a compact column list of `name: data_type (nullable=true, pk=true)`, (c) the first N sample rows (default 5) as JSON. Includes a tail section documenting the expected JSON output shape with `SemanticType` and `PIIRisk` enum values enumerated inline.

PII heuristic guidance lives in the system prompt as a short list of classic tell-tales (email, name, phone, address, national ID, credit card). The LLM is instructed to mark `pii_risk` based on column name and sample-value shape.

### D7. `DescriptionEngine.describe_database` concurrency

`describe_database(tables, samples_per_table)` calls `describe_table` for each table, bounded by an `asyncio.Semaphore` sized to `LLMConfig.max_concurrent_calls` (default 5). Tables are processed concurrently under the semaphore; failures on one table do not cancel others — per-table exceptions are caught, logged, and returned as `None` in the result dict.

Return shape: `dict[tuple[str, str], TableDescription | None]` keyed by `(schema, table_name)`. Callers (the future `context-index`) can filter the Nones and decide whether partial failure is acceptable.

Alternative considered: fail-fast (cancel all tasks on first exception). Rejected — a 40-table scan failing on one edge-case table is wasteful. Partial-success is the right default for a batch of independent calls.

### D8. Logging at the LLM boundary

`AnthropicClient.generate` emits one log record per call at `INFO` level via Python's `logging` module: `{"event": "llm_call", "model": ..., "input_tokens": ..., "output_tokens": ..., "latency_ms": ...}`. **No prompt or response content is logged** (PII risk). Logger name `sonar.engine.llm`.

The description engine logs one record per `describe_table` at `INFO` level with schema, table, columns_count, and outcome (`ok` / `parse_retry` / `failed`). Logger name `sonar.engine.describe`.

### D9. Test strategy

Two test files, both unit-level. No `@pytest.mark.integration` tests in this change; the Docker Postgres is not needed.

- `tests/test_llm_client.py`: Mocks `anthropic.AsyncAnthropic` via `unittest.mock.AsyncMock` (the SDK exposes a clean async interface). Asserts request shape (model, system, messages, max_tokens), response extraction from `content[0].text`, log record emission (captured via `caplog`), and that no API key is accepted by constructor.
- `tests/test_description_engine.py`: Mocks `LLMClient` with a fake that returns canned JSON for a given prompt. Uses real `Table` fixtures (constructed in-test, not from the DB) with varied column types including obvious PII (`email`, `ssn`), a composite PK, a status enum. Asserts: (a) `TableDescription` parses correctly from good JSON; (b) parse-retry fires exactly once on bad JSON and succeeds on second attempt; (c) permanent parse failure raises `DescriptionParseError`; (d) `describe_database` respects the concurrency cap (instrument the fake to count concurrent in-flight calls); (e) per-table exceptions do not cancel the batch and surface as `None` values.

Coverage target: 80% on `src/sonar/engine/*.py`. `_prompts.py` is mostly string literals — coverage will come from exercising `build_table_prompt` via the engine tests.

### D10. Error class hierarchy

Two exceptions, both in `src/sonar/engine/describe.py`:

- `DescriptionError(Exception)` — base class.
- `DescriptionParseError(DescriptionError)` — LLM output could not be parsed after one retry. Carries `.raw_text` (truncated).

The Anthropic SDK's own errors (`anthropic.APIError` and subclasses) propagate unchanged from `AnthropicClient.generate`. We don't wrap them — callers that care can catch them specifically.

## Risks / Trade-offs

- **Risk:** LLM returns plausible-but-wrong semantic types (e.g. classifies `order_id` as MEASURE). The engine has no ground-truth to check against. → **Mitigation:** `confidence` field in every description surfaces the uncertainty. `relationship-mapping` will use FK ground-truth to correct obvious errors (e.g. any column that *is* an FK gets `semantic_type=FOREIGN_KEY` regardless of LLM output). Don't try to fix this in the engine; surface it.
- **Risk:** Strict-JSON-via-prompt is not 100% reliable. → **Mitigation:** one parse-retry with a stricter reminder handles transient drift. Real failure rate measured in integration testing; if it exceeds ~1% we move to Anthropic's tool-use JSON-schema enforcement in a follow-up change.
- **Risk:** Anthropic rate limits on a large scan. → **Mitigation:** concurrency semaphore defaults to 5; SDK's retry handles 429s with backoff. If this is still insufficient we add adaptive rate limiting later.
- **Risk:** Logging token counts and latency could be considered telemetry creep. → **Mitigation:** logs are at `INFO` via the standard `logging` module; a user can silence them by raising the logger level. No network egress of telemetry.
- **Trade-off:** `SemanticType` is kept deliberately small (4 values). Downstream consumers may want finer distinctions (e.g. splitting timestamps out from dimensions, or classifying identifiers as natural vs surrogate). → **Accepted:** start minimal and extend additively when a concrete need surfaces. `OTHER` is the escape hatch in the meantime.
- **Trade-off:** `confidence` is self-reported by the LLM, not an independent estimate. It's a soft signal, not a calibrated probability. → **Accepted:** documented as a hint; consumers can treat anything below ~0.5 as low-signal.

## Migration Plan

Not applicable — greenfield capability introduction. The existing stub files in `src/sonar/engine/` are placeholders, not in use. Replacing them is not a breaking change.

## Open Questions

None blocking. Two items to revisit after the change lands:

1. **Prompt caching.** Worth enabling once we have real usage data on prompts-per-scan. Trivial change if it helps.
2. **Provider abstraction rigour.** If the LiteLLM swap reveals that our `generate(prompt, system) -> str` signature is too narrow (e.g. needs metadata like token counts on the return), widen it in the `llm-client` spec at that point, not speculatively now.
