# Engine

LLM client abstraction, semantic description generation, and relationship inference.

## Provider abstraction

All LLM traffic flows through the `LLMClient` ABC in `llm.py`. Two implementations exist:

- `_anthropic.py` — native Anthropic SDK, activated by `anthropic/` model prefix
- `_openai.py` — OpenAI SDK, handles OpenAI models and any OpenAI-compatible endpoint (Ollama, vLLM, etc.) via `base_url`

The factory `create_llm_client(config)` dispatches on model prefix. Do not route Anthropic through the OpenAI compatibility endpoint — it silently drops prompt caching and is labeled "not production-ready" by Anthropic.

### Adding a new provider

1. Create `_<provider>.py` implementing `LLMClient.generate(prompt, system) -> str`
2. Add the prefix routing in `create_llm_client`
3. Add the SDK dependency to `pyproject.toml`
4. The provider must handle: authentication (API key from env var), error wrapping (surface rate limits and auth failures clearly), and code fence stripping via `strip_code_fences` from `llm.py`

### Configuration

`LLMConfig` is a frozen dataclass with: `model`, `max_tokens`, `max_concurrent_calls`, `base_url`. The CLI flag `--model` maps to `model`, `--base-url` maps to `base_url`. Env var fallback: `SONAR_LLM_BASE_URL` for base URL, `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` for authentication.

## Description generation

`DescriptionEngine` in `describe.py` orchestrates batch description of tables. It receives an `LLMClient` instance and is provider-agnostic.

Key patterns:
- Bounded concurrency via `asyncio.Semaphore` (from `LLMConfig.max_concurrent_calls`)
- Retry with exponential backoff on provider errors (sleep happens outside the semaphore)
- Returns `None` for tables that fail after retries (the scan continues, the summary reports failures)

### Prompts

Prompt templates live in `_prompts.py`. They must be model-agnostic — no assumptions about specific model capabilities or response formats beyond "returns JSON." The system prompt is a stable block; the user prompt varies per table.

## Relationship inference

`relationships.py` (project root, not engine/) infers FK relationships from naming patterns for databases that don't declare them (common in warehouses like Snowflake). Two-rule heuristic: direct PK name match and role-prefix match, with a catch-all PK filter.

This module is pure logic — no LLM, no database access. It takes `list[Table]` and `list[ForeignKey]` and returns enriched relationships.

## Shared utilities

`strip_code_fences` in `llm.py` is shared across providers. It's a public function (no underscore) because both provider implementations import it. If adding more shared utilities, keep them in `llm.py` or create `_utils.py` if `llm.py` grows too large.

## Testing

- Provider tests use a `FakeLLMClient` that returns canned JSON — test the engine, not the API
- Concurrency tests verify the semaphore bounds are respected
- Each provider has its own test file for SDK-specific behavior (auth, error handling)
- `asyncio_mode = "auto"` in pyproject.toml — do not add `@pytest.mark.asyncio` decorators
