# llm-client Specification

## Purpose
TBD - created by archiving change llm-description-engine. Update Purpose after archive.
## Requirements
### Requirement: Provider-agnostic async LLM interface

The system SHALL expose a single abstract `LLMClient` class with one public async method, `generate(prompt: str, system: str | None = None) -> str`, which returns the assistant message text. All LLM traffic in Sonar SHALL flow through an `LLMClient` subclass; no other module may import an LLM provider SDK directly.

#### Scenario: Calling generate returns assistant text

- **WHEN** a concrete `LLMClient` subclass is instantiated and `generate("What is the capital of France?")` is awaited
- **THEN** the method returns the model's response as a plain `str`
- **AND** no exception is raised for normal successful responses

#### Scenario: System prompt is optional

- **WHEN** `generate` is called without the `system` argument
- **THEN** the underlying provider call SHALL be made without a system prompt
- **AND** the behaviour is otherwise identical to a call with `system=None`

#### Scenario: LLMClient cannot be instantiated directly

- **WHEN** code attempts to instantiate the abstract `LLMClient` class directly
- **THEN** Python SHALL raise `TypeError` because `generate` is an abstract method

### Requirement: Anthropic implementation

The system SHALL provide `AnthropicClient(LLMClient)` as the Phase-1 implementation. It SHALL use the official `anthropic` Python SDK's async client (`anthropic.AsyncAnthropic`). The implementation SHALL read the API key from the `ANTHROPIC_API_KEY` environment variable via the SDK's default mechanism and SHALL NOT accept a raw API key via its constructor.

#### Scenario: Successful generation via Anthropic

- **WHEN** `AnthropicClient(config).generate("summarise this table", system="You are a data analyst.")` is awaited
- **THEN** the client SHALL call `AsyncAnthropic.messages.create` with `model=config.model`, `max_tokens=config.max_tokens`, `system="You are a data analyst."`, and `messages=[{"role": "user", "content": "summarise this table"}]`
- **AND** it SHALL return `response.content[0].text`

#### Scenario: Constructor does not accept an API key

- **WHEN** inspecting the signature of `AnthropicClient.__init__`
- **THEN** it SHALL accept an optional `LLMConfig` and nothing else
- **AND** attempts to pass `api_key=` or similar SHALL raise `TypeError`

#### Scenario: Transient errors retry via SDK

- **WHEN** the Anthropic API returns a 429 or 5xx on the first attempt
- **THEN** the SDK's configured retry policy (`max_retries=2`) SHALL handle the retry internally
- **AND** Sonar code SHALL NOT implement its own retry loop around the call

### Requirement: Configuration via `LLMConfig`

The system SHALL expose a frozen dataclass `LLMConfig` with fields `provider: str`, `model: str`, `max_tokens: int`, `max_concurrent_calls: int`. Defaults SHALL be `provider="anthropic"`, `model="claude-haiku-4-5-20251001"`, `max_tokens=1024`, `max_concurrent_calls=5`.

#### Scenario: Default config is valid

- **WHEN** `LLMConfig()` is instantiated with no arguments
- **THEN** it SHALL construct successfully with the documented defaults
- **AND** the instance SHALL be immutable (frozen)

#### Scenario: Config fields are type-checked by the dataclass

- **WHEN** `LLMConfig` is constructed with a field value of the wrong type (e.g. `max_tokens="a lot"`)
- **THEN** the call proceeds (dataclasses do not enforce types at runtime)
- **AND** subsequent use by `AnthropicClient` MAY raise a provider-level error

### Requirement: Observability at the LLM boundary

The system SHALL emit one log record per completed `generate` call via the standard `logging` module on the logger `sonar.engine.llm` at level `INFO`. The record SHALL include the model name, input and output token counts, and call latency in milliseconds. The record SHALL NOT include the prompt content, system prompt content, or response content.

#### Scenario: Log record emitted on successful call

- **WHEN** `AnthropicClient.generate` returns successfully
- **THEN** exactly one log record SHALL be emitted on `sonar.engine.llm` at level `INFO`
- **AND** the record's `extra` or structured payload SHALL contain keys `model`, `input_tokens`, `output_tokens`, `latency_ms`
- **AND** the record SHALL NOT contain the strings passed as `prompt` or `system`

#### Scenario: Log record not emitted on provider exception

- **WHEN** `AnthropicClient.generate` raises an `anthropic.APIError` or subclass
- **THEN** the exception propagates to the caller
- **AND** no `llm_call` log record is emitted

### Requirement: No direct provider imports outside `LLMClient` implementations

Only modules implementing `LLMClient` SHALL import from the `anthropic` package (or any future provider SDK). All other Sonar modules SHALL interact with LLMs exclusively through the `LLMClient` abstraction.

#### Scenario: Engine modules do not import anthropic

- **WHEN** scanning the imports of every module under `src/sonar/` except those implementing `LLMClient`
- **THEN** no module SHALL import from `anthropic`

