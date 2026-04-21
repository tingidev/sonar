## 1. Foundations

- [x] 1.1 Delete placeholder bodies in `src/sonar/engine/llm.py` and `src/sonar/engine/describe.py`; keep only a module docstring until rewritten.
- [x] 1.2 Add `SemanticType(StrEnum)` and `PIIRisk(StrEnum)` in `src/sonar/engine/describe.py` with lowercase-string values per the spec.
- [x] 1.3 Add frozen dataclasses `ColumnDescription` and `TableDescription` in `src/sonar/engine/describe.py` with `tuple` fields for `columns` and `domain_hints`.
- [x] 1.4 Add exception hierarchy `DescriptionError` and `DescriptionParseError(DescriptionError)` in `src/sonar/engine/describe.py`. `DescriptionParseError` carries a `raw_text: str` attribute truncated to 500 chars at construction.

## 2. LLM client abstraction

- [x] 2.1 Replace `src/sonar/engine/llm.py` contents with an `abc.ABC` base class `LLMClient` declaring `@abstractmethod async def generate(self, prompt: str, system: str | None = None) -> str`.
- [x] 2.2 Add frozen `LLMConfig` dataclass with fields `provider`, `model`, `max_tokens`, `max_concurrent_calls` and defaults per the spec.
- [x] 2.3 Implement `AnthropicClient(LLMClient)`. Constructor accepts an optional `LLMConfig` only; internally instantiate `anthropic.AsyncAnthropic(max_retries=2)`. No `api_key` parameter.
- [x] 2.4 Implement `AnthropicClient.generate`: call `messages.create` with model/max_tokens/system/messages per D3, return `response.content[0].text`.
- [x] 2.5 Add `INFO`-level logging in `AnthropicClient.generate` on the `sonar.engine.llm` logger with `model`, `input_tokens`, `output_tokens`, `latency_ms`. Exclude prompt and response content.

## 3. Prompt module

- [x] 3.1 Create `src/sonar/engine/_prompts.py` with a module-level `SYSTEM_PROMPT` constant per D6 (role, JSON-only output, PII heuristic hints, confidence honesty).
- [x] 3.2 Implement `build_table_prompt(table: Table, samples: list[dict]) -> str` that serialises the schema-qualified name, the column list, the samples as JSON, and the expected output JSON shape with enum values enumerated.
- [x] 3.3 Export `SYSTEM_PROMPT` and `build_table_prompt` via `src/sonar/engine/__init__.py` only if needed by tests; otherwise keep module-private.

## 4. Description engine

- [x] 4.1 Implement `DescriptionEngine.__init__(self, llm_client: LLMClient, config: LLMConfig | None = None)`. Store both.
- [x] 4.2 Implement a private helper `_parse_table_description(raw: str, schema: str, name: str, columns: tuple[Column, ...]) -> TableDescription` that runs `json.loads`, constructs enum members, and returns the dataclass. Raises `DescriptionParseError` on any failure (`JSONDecodeError`, `KeyError`, `ValueError`).
- [x] 4.3 Implement `async def describe_table(self, table: Table, samples: list[dict]) -> TableDescription`. First attempt: build prompt via `build_table_prompt`, call `LLMClient.generate` with `SYSTEM_PROMPT`, parse with `_parse_table_description`.
- [x] 4.4 On parse failure, re-invoke `LLMClient.generate` exactly once with the original prompt plus the reminder string per D4. If second parse also fails, log outcome `"failed"` and raise `DescriptionParseError`.
- [x] 4.5 Emit `INFO` log record on `sonar.engine.describe` with `schema`, `table`, `columns_count`, `outcome` per D8. Outcome is `"ok"`, `"parse_retry"`, or `"failed"`.
- [x] 4.6 Implement `async def describe_database(self, tables, samples_per_table) -> dict[tuple[str, str], TableDescription | None]`. Create an `asyncio.Semaphore(config.max_concurrent_calls)`. Wrap each `describe_table` call in a semaphore-acquired coroutine. Use `asyncio.gather(..., return_exceptions=True)` to keep siblings alive on failure. Map failed entries to `None` in the returned dict.
- [x] 4.7 Short-circuit `describe_database([], {})` to return `{}` without instantiating a semaphore or calling `LLMClient.generate`.

## 5. LLM client tests

- [x] 5.1 Create `tests/test_llm_client.py`. Add `TestLLMConfig`: asserts default values, frozen behaviour, and that assignment raises `FrozenInstanceError`.
- [x] 5.2 Add `TestLLMClientAbstract`: asserts `LLMClient` cannot be instantiated (missing abstract method raises `TypeError`).
- [x] 5.3 Add `TestAnthropicClient` with `AsyncMock` patching of `anthropic.AsyncAnthropic`. Verify `messages.create` is called with exactly the expected model, max_tokens, system, and messages arguments.
- [x] 5.4 Verify `AnthropicClient.generate` returns `response.content[0].text` by constructing a fake response object with that shape.
- [x] 5.5 Verify constructor does not accept `api_key=` (TypeError on extra kwarg).
- [x] 5.6 Use pytest's `caplog` to verify exactly one `INFO` record is emitted on `sonar.engine.llm` per successful call, containing `model`, `input_tokens`, `output_tokens`, `latency_ms`, and NOT containing the prompt string or response text.
- [x] 5.7 Verify that when the mocked Anthropic call raises `anthropic.APIError`, no `llm_call` log record is emitted and the exception propagates unchanged.

## 6. Description engine tests

- [x] 6.1 Create `tests/test_description_engine.py`. Add a `FakeLLMClient(LLMClient)` test helper that returns pre-scripted responses and records call count and concurrent-in-flight peak.
- [x] 6.2 Add `TestDataclasses`: `TableDescription` is frozen (assignment raises `FrozenInstanceError`), `columns` field is a `tuple`, enum values round-trip through `json.dumps`/`json.loads`.
- [x] 6.3 Add `TestDescribeTable::test_successful_parse`: `FakeLLMClient` returns valid JSON matching a fixture `Table` with three columns; assert returned `TableDescription` fields match input schema/name and column order is preserved.
- [x] 6.4 Add `TestDescribeTable::test_prompt_composition`: capture the prompt arg on `LLMClient.generate` and assert it contains the schema-qualified name, each column's `name: data_type` fragment, the serialised samples, and the documented `SemanticType`/`PIIRisk` enum values.
- [x] 6.5 Add `TestDescribeTable::test_pii_classification_respected`: fixture returns `pii_risk="high"` for `email`; assert the returned `ColumnDescription.pii_risk == PIIRisk.HIGH` and engine does not override.
- [x] 6.6 Add `TestParseRetry::test_retry_recovers`: first call returns `"not json {"`, second returns valid JSON; assert `describe_table` succeeds, `generate` called exactly twice, second prompt contains the reminder substring.
- [x] 6.7 Add `TestParseRetry::test_permanent_failure_raises`: both calls return malformed JSON; assert `DescriptionParseError` raised, `raw_text` attribute present and truncated to at most 500 chars, `generate` called exactly twice.
- [x] 6.8 Add `TestParseRetry::test_valid_first_call_no_retry`: valid JSON first call; assert `generate` called exactly once.
- [x] 6.9 Add `TestDescribeDatabase::test_concurrency_bound`: 10 tables, `max_concurrent_calls=3`, `FakeLLMClient` tracks peak concurrency; assert peak never exceeded 3, all 10 entries present in result.
- [x] 6.10 Add `TestDescribeDatabase::test_partial_failure_does_not_cancel`: 5 tables, one designed to raise `DescriptionParseError`; assert result dict has 5 entries, exactly one is `None`, the other four are `TableDescription` instances, no exception propagates.
- [x] 6.11 Add `TestDescribeDatabase::test_empty_input_short_circuits`: call with `([], {})`; assert result is `{}` and `generate` was never called.
- [x] 6.12 Add `TestLogging`: `caplog` captures `sonar.engine.describe` records. Assert outcomes `"ok"`, `"parse_retry"`, and `"failed"` are each emitted in the matching scenarios with `schema`, `table`, `columns_count` keys and no prompt/response content.

## 7. Verification

- [x] 7.1 Run `poetry run pytest` and confirm the full suite passes (this change's new tests plus the existing postgres-connector suite).
- [x] 7.2 Confirm coverage on `src/sonar/engine/*.py` is >= 80% (from the terminal `--cov-report=term-missing` output).
- [x] 7.3 Confirm no module under `src/sonar/` outside of `engine/llm.py` imports from `anthropic` (quick `grep -r "import anthropic" src/sonar` check).
- [x] 7.4 Run `openspec validate llm-description-engine` and confirm it passes.
