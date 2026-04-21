## Why

Schema introspection gives Sonar structure; on its own, structure is not context. A table named `orders` with columns `id`, `uid`, `status`, `placed_at`, `total` is legible to a human but ambiguous to an agent: what does `status` mean, which column is the grain, is `uid` PII, what domain is this? Sonar's core differentiator — "the only tool that auto-generates meaning from structure" — lives here. Without a description engine, every downstream consumer (relationship inference, MCP context tools, agent queries) operates on raw identifiers and SQL types.

This is the second foundational change of Phase 1. Every subsequent change (`relationship-mapping`, `context-index`, `mcp-server`) consumes its output. The scope is deliberately **structured ontology, not prose**: tables and columns are described as typed fields an agent can reason over, not as free-form paragraphs.

## What Changes

- Introduce a `llm-client` capability: a thin, provider-agnostic async abstraction (`LLMClient.generate(prompt, system) -> str`) that Sonar uses for every LLM call. The Phase-1 implementation wraps the Anthropic SDK on `claude-haiku-4-5`. Swapping providers later (LiteLLM, local models) changes only the implementation.
- Introduce a `description-engine` capability: takes a discovered `Table` and its row samples, returns a structured `TableDescription` with narrative, grain, domain hints, and a `ColumnDescription` per column including semantic type, PII classification, and confidence. A `describe_database` method fans out over all tables with a bounded concurrency cap.
- Structured output: the engine emits frozen dataclasses, not free-form text. LLM prompts instruct the model to return strict JSON; responses are parsed and validated before being handed back. Malformed output retries once, then fails.
- Observability at the boundary: every LLM call logs the model, token counts, and latency. No payload logging by default (PII risk).
- Test harness that does not require a live LLM: tests mock `LLMClient.generate` and assert prompt composition plus output parsing. No CI dependency on `ANTHROPIC_API_KEY`.

## Capabilities

### New Capabilities

- `llm-client`: Thin async LLM abstraction. Single public method `generate(prompt, system) -> str`. Anthropic implementation in Phase 1. API key from `ANTHROPIC_API_KEY`. Handles retries and logging at the provider boundary; callers above this layer see a plain string or a raised exception.
- `description-engine`: Semantic description generation. Consumes `Table` + `list[dict]` row samples (from the `postgres-connector` capability) and produces a frozen `TableDescription` dataclass containing a table-level narrative, grain, domain hints, and a tuple of `ColumnDescription` entries. Exposes `describe_table` and `describe_database` (concurrent fan-out, bounded).

### Modified Capabilities

None. This change establishes two new capabilities; no existing spec's requirements change.

## Impact

- **Code:** `src/sonar/engine/llm.py` (replace stub with `AnthropicClient` implementation and `LLMClient` abstract shape). `src/sonar/engine/describe.py` (replace stub with `DescriptionEngine`, `TableDescription`, `ColumnDescription`, and a module-level prompt builder). New `src/sonar/engine/_prompts.py` holds prompt templates so they can be inspected and tested independently.
- **Dependencies:** `anthropic = "^0.49"` is already declared — no new runtime dependencies. No new dev dependencies.
- **Configuration:** `ANTHROPIC_API_KEY` read from the environment by the Anthropic SDK. No secret handling in Sonar code. The `LLMConfig` dataclass (model, max_tokens, concurrency) becomes the only config surface.
- **Tests:** New `tests/test_llm_client.py` (mock Anthropic SDK at the HTTP layer; verify request shape, response parsing, retry-once-on-parse-failure, logging side-effects). New `tests/test_description_engine.py` (mock `LLMClient.generate` with canned JSON; verify prompt composition from real `Table` fixtures, `TableDescription` parsing, concurrency bound honored, graceful failure on permanent parse error). No `@pytest.mark.integration` tests in this change — the test Postgres is not required. Coverage target: 80% on `src/sonar/engine/*.py`.
- **CLI:** No CLI surface exposed yet. `sonar scan` remains a placeholder until the `context-index` change wires schema discovery + descriptions + persistence end-to-end.
- **Public API surface:** `LLMClient` (abstract), `AnthropicClient`, `LLMConfig`, `DescriptionEngine`, frozen dataclasses `TableDescription`, `ColumnDescription`, `SemanticType`, `PIIRisk`.
