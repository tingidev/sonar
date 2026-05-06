## Why

Sonar currently requires an Anthropic API key — users without one cannot use the tool at all. For an open-source project targeting broad adoption pre-launch, this is an unnecessary gate. The `openai` SDK covers OpenAI natively and any OpenAI-compatible endpoint (Ollama, Groq, Together, vLLM) via `base_url`, giving users provider choice with minimal added complexity.

## What Changes

- Add `openai` SDK as a required dependency alongside existing `anthropic` SDK
- Implement `OpenAIClient(LLMClient)` that handles all non-Anthropic models
- Add thin dispatcher function that routes by model prefix: `anthropic/` prefix routes to native Anthropic SDK (prefix stripped), everything else routes to OpenAI-compat client
- Add `--model` CLI flag to `sonar scan` and `sonar eval descriptions`
- Add `SONAR_LLM_BASE_URL` env var support for the OpenAI-compat path (Ollama, self-hosted)
- Update default model string from `claude-haiku-4-5-20251001` to `anthropic/claude-haiku-4-5-20251001`
- **BREAKING**: `LLMConfig.provider` field removed (routing derived from model prefix)

## Capabilities

### New Capabilities

- `openai-llm-client`: OpenAI-compatible LLM client implementation covering OpenAI and any base_url-configurable endpoint (Ollama, Groq, vLLM, etc.)

### Modified Capabilities

- `llm-client`: Requirements change — dispatcher factory function, model prefix routing convention, `SONAR_LLM_BASE_URL` env var, updated config shape (provider field removed)

## Impact

- `src/sonar/engine/llm.py` — split into multiple modules, dispatcher added
- `src/sonar/cli.py` — `--model` flag, no longer imports `AnthropicClient` directly
- `src/sonar/eval/descriptions.py` — uses dispatcher instead of direct `AnthropicClient`
- `pyproject.toml` — `openai` added as required dependency
- Tests — new unit tests for OpenAIClient and dispatcher, existing AnthropicClient tests updated for prefix convention
