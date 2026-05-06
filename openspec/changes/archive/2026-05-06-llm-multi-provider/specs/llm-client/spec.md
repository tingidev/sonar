# llm-client Specification (delta)

## ADDED Requirements

### Requirement: Model prefix routing via factory function

The system SHALL expose a factory function `create_llm_client(config: LLMConfig | None = None) -> LLMClient` as the sole public entry point for obtaining an LLM client. When `config` is `None`, the function SHALL use default `LLMConfig()`. The function SHALL route based on model prefix: model strings starting with `anthropic/` SHALL be routed to `AnthropicClient` with the prefix stripped; all other model strings SHALL be routed to `OpenAIClient` as-is.

#### Scenario: Anthropic model routing

- **WHEN** `create_llm_client(LLMConfig(model="anthropic/claude-haiku-4-5-20251001"))` is called
- **THEN** it SHALL return an `AnthropicClient` instance
- **AND** the client SHALL use bare model ID `claude-haiku-4-5-20251001` for API calls (prefix stripped)

#### Scenario: OpenAI model routing

- **WHEN** `create_llm_client(LLMConfig(model="gpt-4o"))` is called
- **THEN** it SHALL return an `OpenAIClient` instance
- **AND** the client SHALL use model string `gpt-4o` as-is for API calls

#### Scenario: Ollama model routing

- **WHEN** `create_llm_client(LLMConfig(model="llama3"))` is called
- **THEN** it SHALL return an `OpenAIClient` instance
- **AND** the client SHALL use model string `llama3` as-is for API calls

#### Scenario: Default config routes to Anthropic

- **WHEN** `create_llm_client(LLMConfig())` is called (no arguments)
- **THEN** it SHALL return an `AnthropicClient` instance
- **AND** the default model `anthropic/claude-haiku-4-5-20251001` SHALL have its prefix stripped

### Requirement: CLI model flag

The `sonar scan` and `sonar eval descriptions` commands SHALL accept a `--model` option that overrides the default model in `LLMConfig`. The flag value SHALL be passed directly to the factory function without transformation.

#### Scenario: Model flag overrides default

- **WHEN** user runs `sonar scan --dsn <url> --model gpt-4o`
- **THEN** the description engine SHALL use `OpenAIClient` with model `gpt-4o`

#### Scenario: No model flag uses default

- **WHEN** user runs `sonar scan --dsn <url>` without `--model`
- **THEN** the description engine SHALL use `AnthropicClient` with model `claude-haiku-4-5-20251001`

## MODIFIED Requirements

### Requirement: Configuration via `LLMConfig`

The system SHALL expose a frozen dataclass `LLMConfig` with fields `model: str`, `max_tokens: int`, `max_concurrent_calls: int`. Defaults SHALL be `model="anthropic/claude-haiku-4-5-20251001"`, `max_tokens=4096`, `max_concurrent_calls=5`. The `provider` field is removed; provider routing is derived from the model prefix.

#### Scenario: Default config is valid

- **WHEN** `LLMConfig()` is instantiated with no arguments
- **THEN** it SHALL construct successfully with the documented defaults
- **AND** the instance SHALL be immutable (frozen)

#### Scenario: Config fields are type-checked by the dataclass

- **WHEN** `LLMConfig` is constructed with a field value of the wrong type (e.g. `max_tokens="a lot"`)
- **THEN** the call proceeds (dataclasses do not enforce types at runtime)
- **AND** subsequent use by a client MAY raise a provider-level error

### Requirement: No direct provider imports outside `LLMClient` implementations

Only modules implementing `LLMClient` SHALL import from the `anthropic` or `openai` packages. All other Sonar modules SHALL interact with LLMs exclusively through the `create_llm_client` factory and `LLMClient` abstraction.

#### Scenario: Engine modules do not import provider SDKs

- **WHEN** scanning the imports of every module under `src/sonar/` except those implementing `LLMClient`
- **THEN** no module SHALL import from `anthropic` or `openai`

### Requirement: Anthropic implementation

The system SHALL provide `AnthropicClient(LLMClient)` using the official `anthropic` Python SDK's async client (`anthropic.AsyncAnthropic`). It SHALL read the API key from the `ANTHROPIC_API_KEY` environment variable via the SDK's default mechanism and SHALL NOT accept a raw API key via its constructor. The constructor SHALL accept a bare model ID (prefix already stripped by the factory).

#### Scenario: Successful generation via Anthropic

- **WHEN** `AnthropicClient(model, max_tokens).generate("summarise this table", system="You are a data analyst.")` is awaited
- **THEN** the client SHALL call `AsyncAnthropic.messages.create` with the bare `model`, `max_tokens`, `system="You are a data analyst."`, and `messages=[{"role": "user", "content": "summarise this table"}]`
- **AND** it SHALL return `response.content[0].text`

#### Scenario: Transient errors retry via SDK

- **WHEN** the Anthropic API returns a 429 or 5xx on the first attempt
- **THEN** the SDK's configured retry policy (`max_retries=2`) SHALL handle the retry internally
- **AND** Sonar code SHALL NOT implement its own retry loop around the call
