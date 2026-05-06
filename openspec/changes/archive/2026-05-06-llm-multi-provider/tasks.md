## 1. Dependencies and module structure

- [x] 1.1 Add `openai = "^1.0"` to `[tool.poetry.dependencies]` in `pyproject.toml` and run `poetry lock`
- [x] 1.2 Split `src/sonar/engine/llm.py` into three modules: `llm.py` (ABC, config, factory, utilities), `_anthropic.py` (AnthropicClient), `_openai.py` (OpenAIClient placeholder)
- [x] 1.3 Update `src/sonar/engine/__init__.py` exports if needed

## 2. LLMConfig update

- [x] 2.1 Remove `provider` field from `LLMConfig`, update default model to `"anthropic/claude-haiku-4-5-20251001"`
- [x] 2.2 Update all existing tests that construct `LLMConfig` with the `provider` field

## 3. Dispatcher factory

- [x] 3.1 Implement `create_llm_client(config: LLMConfig) -> LLMClient` in `llm.py` — route `anthropic/` prefix to `AnthropicClient` (strip prefix), everything else to `OpenAIClient`
- [x] 3.2 Write unit tests for dispatcher routing: anthropic prefix, bare model names, default config

## 4. AnthropicClient migration

- [x] 4.1 Move `AnthropicClient` to `src/sonar/engine/_anthropic.py`, update constructor to accept bare model ID and max_tokens directly from factory
- [x] 4.2 Verify existing AnthropicClient tests pass with the new module path

## 5. OpenAIClient implementation

- [x] 5.1 Implement `OpenAIClient(LLMClient)` in `src/sonar/engine/_openai.py` — `AsyncOpenAI` with `SONAR_LLM_BASE_URL` env var support, placeholder API key for keyless local endpoints
- [x] 5.2 Write unit tests for OpenAIClient: successful generation, base URL override, no-key local endpoint, observability logging

## 6. CLI integration

- [x] 6.1 Add `--model` option to `sonar scan` command, pass through to `LLMConfig` and `create_llm_client`
- [x] 6.2 Add `--model` option to `sonar eval descriptions` command
- [x] 6.3 Remove direct `AnthropicClient` import from `cli.py` — use factory only
- [x] 6.4 Update CLI tests to cover `--model` flag

## 7. Verification

- [x] 7.1 Run full test suite (`poetry run pytest`), confirm all pass
- [x] 7.2 Verify no direct provider SDK imports outside `_anthropic.py` and `_openai.py`
- [x] 7.3 Run `ruff check` and `ruff format --check` — clean
- [x] 7.4 Manual smoke test: `sonar scan --dsn <chembl> --model anthropic/claude-haiku-4-5-20251001`
