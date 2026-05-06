# openai-llm-client Specification

## Purpose

OpenAI-compatible LLM client implementation. Handles OpenAI models natively and any OpenAI-compatible endpoint (Ollama, Groq, Together, vLLM) via configurable base URL.

## Requirements

### Requirement: OpenAI-compatible client implementation

The system SHALL provide `OpenAIClient(LLMClient)` using the official `openai` Python SDK's async client (`openai.AsyncOpenAI`). It SHALL read the API key from the `OPENAI_API_KEY` environment variable via the SDK's default mechanism. When `SONAR_LLM_BASE_URL` is set in the environment, the client SHALL pass it as `base_url` to the SDK constructor.

#### Scenario: Successful generation via OpenAI

- **WHEN** `OpenAIClient(model, max_tokens).generate("summarise this table", system="You are a data analyst.")` is awaited with `model = "gpt-4o"`
- **THEN** the client SHALL call `AsyncOpenAI.chat.completions.create` with `model="gpt-4o"`, `max_tokens=max_tokens`, and messages containing the system prompt and user prompt
- **AND** it SHALL return `response.choices[0].message.content`

#### Scenario: Generation without system prompt

- **WHEN** `OpenAIClient(model, max_tokens).generate("hello")` is awaited without a system argument
- **THEN** the messages list SHALL contain only the user message
- **AND** no system message SHALL be included

#### Scenario: Base URL override via environment variable

- **WHEN** `SONAR_LLM_BASE_URL` is set to `http://localhost:11434/v1`
- **AND** `OpenAIClient(model, max_tokens)` is constructed
- **THEN** the underlying `AsyncOpenAI` client SHALL be initialised with `base_url="http://localhost:11434/v1"`

#### Scenario: No API key required when base URL points to local server

- **WHEN** `SONAR_LLM_BASE_URL` is set to a local endpoint (e.g. Ollama)
- **AND** `OPENAI_API_KEY` is not set in the environment
- **THEN** the client SHALL construct successfully by passing a placeholder API key to the SDK
- **AND** generation SHALL succeed if the endpoint does not require authentication

#### Scenario: Missing API key without base URL fails fast

- **WHEN** `SONAR_LLM_BASE_URL` is not set
- **AND** `OPENAI_API_KEY` is not set in the environment
- **THEN** the constructor SHALL raise `EnvironmentError` with a message indicating which variable to set

#### Scenario: Transient errors retry via SDK

- **WHEN** the OpenAI-compatible endpoint returns a 429 or 5xx on the first attempt
- **THEN** the SDK's configured retry policy (`max_retries=2`) SHALL handle the retry internally
- **AND** Sonar code SHALL NOT implement its own retry loop around the call

### Requirement: Observability at the OpenAI LLM boundary

The system SHALL emit one log record per completed `generate` call via the standard `logging` module on the logger `sonar.engine.llm` at level `INFO`. The record SHALL include the model name, input and output token counts (from `response.usage`), and call latency in milliseconds. The record SHALL NOT include the prompt content, system prompt content, or response content.

#### Scenario: Log record emitted on successful call

- **WHEN** `OpenAIClient.generate` returns successfully
- **THEN** exactly one log record SHALL be emitted on `sonar.engine.llm` at level `INFO`
- **AND** the record's `extra` or structured payload SHALL contain keys `model`, `input_tokens`, `output_tokens`, `latency_ms`
- **AND** the record SHALL NOT contain the strings passed as `prompt` or `system`

#### Scenario: Log record not emitted on provider exception

- **WHEN** `OpenAIClient.generate` raises an `openai.APIError` or subclass
- **THEN** the exception propagates to the caller
- **AND** no `llm_call` log record is emitted
