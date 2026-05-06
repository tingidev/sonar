## Context

Sonar's LLM layer (`src/sonar/engine/llm.py`) currently has one implementation: `AnthropicClient`. The `LLMClient` ABC exists, so adding providers is structurally simple. However, the CLI hardcodes `AnthropicClient`, `LLMConfig` carries an unused `provider` field, and there's no user-facing way to select a model or provider.

The explore phase (2026-05-06) confirmed the two-SDK approach: `openai` SDK as the universal OpenAI-compatible client (covers OpenAI, Ollama, Groq, Together, vLLM, etc. via `base_url`), `anthropic` SDK retained natively (avoids compat endpoint limitations for the default path). LiteLLM was rejected for supply chain risk and 65MB footprint.

## Goals / Non-Goals

**Goals:**
- Any user with an OpenAI key, Ollama install, or Anthropic key can run `sonar scan`
- Single `--model` flag controls provider routing (no separate `--provider` flag)
- Zero-config backward compatibility: no flags + `ANTHROPIC_API_KEY` in env = Haiku runs
- Clean module split: one file per provider, dispatcher as the public entry point

**Non-Goals:**
- Streaming responses (Sonar uses single-turn completions only)
- Prompt caching (no multi-turn sessions, no repeated system prompts across calls today)
- Provider-specific features beyond basic text generation (tool use, vision, etc.)
- Fallback chains (try provider A, fall to provider B on failure)
- Model registry or auto-detection of available models

## Decisions

### D1: Slash-prefix routing convention

Model strings use `provider/model-id` format. The dispatcher checks if the model starts with `anthropic/` — if yes, strips the prefix and routes to `AnthropicClient` with the bare model ID. Everything else passes as-is to `OpenAIClient`.

Alternatives considered:
- Colon separator (`anthropic:model`) — less readable, conflicts with potential future `base_url:model` patterns
- Well-known name detection (`claude-*` → anthropic) — requires maintaining a pattern list that breaks on new model names
- Separate `--provider` flag — redundant information, easy to get out of sync with model name

Revisit when: a third native SDK is added (e.g. Bedrock) that needs its own prefix routing.
Reversibility: cheap (prefix convention is internal, not persisted or exposed to downstream consumers)

### D2: Module split

Split `src/sonar/engine/llm.py` into:
- `src/sonar/engine/llm.py` — `LLMClient` ABC, `LLMConfig`, `create_llm_client()` factory, `_strip_code_fences()`
- `src/sonar/engine/_anthropic.py` — `AnthropicClient` implementation
- `src/sonar/engine/_openai.py` — `OpenAIClient` implementation

Private modules (underscore prefix) for implementations. Public interface remains `llm.py` — consumers import `create_llm_client` and `LLMConfig` from there.

Alternatives considered:
- Single file with both clients — works at two clients, won't at three. Splitting now costs nothing.
- `providers/` subdirectory — over-structured for two files.

Revisit when: a third provider implementation is added and the flat layout feels crowded.
Reversibility: cheap (internal module boundaries, no public API change)

### D3: `SONAR_LLM_BASE_URL` env var for OpenAI-compat path only

The `OpenAIClient` reads `SONAR_LLM_BASE_URL` from the environment at construction time. If set, it overrides the SDK's default base URL (`api.openai.com/v1`). This env var is ignored when the dispatcher routes to `AnthropicClient`.

Alternatives considered:
- CLI flag (`--base-url`) — mixes connection config with invocation config; env var is more natural for endpoint addresses
- Baked into model string (`ollama/llama3` implying localhost) — magic, breaks for remote Ollama or non-standard ports
- `OPENAI_BASE_URL` (SDK's native env var) — conflicts if the user has it set for other tools; Sonar-namespaced is safer

Revisit when: users report needing per-invocation base URL switching (unlikely for a CLI tool).
Reversibility: cheap (additive env var, no persisted state)

### D4: `LLMConfig` shape change

Remove the `provider` field from `LLMConfig`. The model string now carries the routing information. Updated fields: `model: str` (default `"anthropic/claude-haiku-4-5-20251001"`), `max_tokens: int` (default `4096`), `max_concurrent_calls: int` (default `5`).

Alternatives considered:
- Keep `provider` alongside model prefix — redundant, two sources of truth for the same information
- Add `base_url` to config — env var is the right home for endpoint addresses (see D3)

Revisit when: config needs to carry per-provider options that can't be derived from model string or env vars.
Reversibility: cheap (breaking change, but no active users — pre-launch)

### D5: `openai` SDK as required dependency

`openai` is added to `[tool.poetry.dependencies]` as a required dep (not optional). The 11MB footprint shares most transitive deps with `anthropic` (httpx, pydantic, anyio). The LLM client is core infrastructure, not a specialized connector.

Alternatives considered:
- Optional dep with dispatch-time guard (like Snowflake connector) — adds friction for the majority use case; LLM client is not optional infrastructure

Revisit when: install size becomes a user complaint (unlikely given shared transitive deps).
Reversibility: cheap (can demote to optional later without breaking existing installs)

### D6: Factory function as public API

`create_llm_client(config: LLMConfig) -> LLMClient` is the single entry point. CLI and engine modules call this instead of importing concrete client classes. The factory reads the model prefix from config and returns the appropriate client.

Alternatives considered:
- Registry pattern (register providers by name, look up at runtime) — over-engineered for two providers
- Direct concrete imports in CLI — violates the existing spec requirement ("no direct provider imports outside LLMClient implementations")

Revisit when: plugin-based provider discovery is needed (unlikely before launch).
Reversibility: cheap (function signature is internal)

## Risks / Trade-offs

- [OpenAI SDK version churn] The `openai` SDK has frequent breaking changes (v0 → v1 was disruptive). Pin to `^1.0` and test against it in CI. Mitigation: Sonar's usage surface is minimal (one `chat.completions.create` call), so breakage is unlikely.
- [Anthropic compat endpoint temptation] Future contributors may suggest routing Anthropic through the OpenAI compat client to "simplify." The compat endpoint silently drops prompt caching and is labeled "not production-ready." Guard against this with a comment in the dispatcher and the spec requirement for native Anthropic routing.
- [Ollama model naming] Ollama uses bare model names (`llama3`, `codellama`) that could collide with future OpenAI model names. Not a real risk today — OpenAI model names are distinctive (`gpt-4o`, `o1-mini`). If collision appears, the user can qualify with a prefix.

## Open Questions

None — all decisions resolved in explore phase.
