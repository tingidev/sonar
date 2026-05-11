"""Unit tests for the LLM client abstraction and AnthropicClient."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest

from sonar.engine._anthropic import AnthropicClient
from sonar.engine.llm import LLMClient, LLMConfig, create_llm_client, strip_code_fences


class TestLLMConfig:
    def test_defaults(self) -> None:
        config = LLMConfig()
        assert config.model == "anthropic/claude-haiku-4-5-20251001"
        assert config.max_tokens == 4096
        assert config.max_concurrent_calls == 5

    def test_frozen(self) -> None:
        config = LLMConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.model = "something-else"  # type: ignore[misc]


class TestLLMClientAbstract:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            LLMClient()  # type: ignore[abstract]


def _fake_anthropic_response(
    text: str, *, input_tokens: int = 42, output_tokens: int = 17
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class TestAnthropicClient:
    async def test_generate_calls_messages_create_with_expected_args(self) -> None:
        fake_response = _fake_anthropic_response("hello world")
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.messages.create = mock_create

            client = AnthropicClient(model="claude-haiku-4-5-20251001", max_tokens=256)
            result = await client.generate("hi", system="be nice")

            assert result == "hello world"
            mock_cls.assert_called_once_with(max_retries=2)
            mock_create.assert_awaited_once_with(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system="be nice",
                messages=[{"role": "user", "content": "hi"}],
            )

    async def test_generate_without_system_omits_system_arg(self) -> None:
        fake_response = _fake_anthropic_response("no system")
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.messages.create = mock_create

            client = AnthropicClient(model="claude-haiku-4-5-20251001", max_tokens=4096)
            await client.generate("hi")

            kwargs = mock_create.await_args.kwargs
            assert "system" not in kwargs
            assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    async def test_generate_returns_content_0_text(self) -> None:
        fake_response = _fake_anthropic_response("extracted text")
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(return_value=fake_response)
            client = AnthropicClient(model="claude-haiku-4-5-20251001", max_tokens=4096)
            assert await client.generate("x") == "extracted text"

    async def test_logs_info_record_without_payload(self, caplog: pytest.LogCaptureFixture) -> None:
        prompt = "please describe the orders table"
        system = "you are a data analyst"
        response_text = "nothing about the prompt appears here"
        fake_response = _fake_anthropic_response(response_text, input_tokens=111, output_tokens=9)

        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(return_value=fake_response)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = AnthropicClient(model="test-model", max_tokens=4096)
                await client.generate(prompt, system=system)

        records = [r for r in caplog.records if r.name == "sonar.engine.llm"]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.INFO
        assert record.model == "test-model"
        assert record.input_tokens == 111
        assert record.output_tokens == 9
        assert isinstance(record.latency_ms, int)
        assert record.latency_ms >= 0

        rendered = record.getMessage()
        assert prompt not in rendered
        assert system not in rendered
        assert response_text not in rendered
        for value in record.__dict__.values():
            if isinstance(value, str):
                assert prompt not in value
                assert system not in value
                assert response_text not in value

    async def test_no_log_on_provider_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        api_error = anthropic.APIError(message="boom", request=request, body=None)
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(side_effect=api_error)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = AnthropicClient(model="claude-haiku-4-5-20251001", max_tokens=4096)
                with pytest.raises(anthropic.APIError):
                    await client.generate("x")

        assert [r for r in caplog.records if r.name == "sonar.engine.llm"] == []

    async def test_generate_strips_code_fences(self) -> None:
        fenced = '```json\n{"key": "value"}\n```'
        fake_response = _fake_anthropic_response(fenced)
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(return_value=fake_response)
            client = AnthropicClient(model="claude-haiku-4-5-20251001", max_tokens=4096)
            result = await client.generate("x")
        assert result == '{"key": "value"}'


class TestCreateLLMClient:
    @pytest.fixture(autouse=True)
    def _set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def test_anthropic_prefix_routes_to_anthropic_client(self) -> None:
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic"):
            client = create_llm_client(LLMConfig(model="anthropic/claude-haiku-4-5-20251001"))
        assert isinstance(client, AnthropicClient)
        assert client._model == "claude-haiku-4-5-20251001"

    def test_bare_model_routes_to_openai_client(self) -> None:
        from sonar.engine._openai import OpenAIClient

        with patch("sonar.engine._openai.openai.AsyncOpenAI"):
            client = create_llm_client(LLMConfig(model="gpt-4o"))
        assert isinstance(client, OpenAIClient)
        assert client._model == "gpt-4o"

    def test_default_config_routes_to_anthropic(self) -> None:
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic"):
            client = create_llm_client(LLMConfig())
        assert isinstance(client, AnthropicClient)
        assert client._model == "claude-haiku-4-5-20251001"

    def test_ollama_model_routes_to_openai_client(self) -> None:
        from sonar.engine._openai import OpenAIClient

        with patch("sonar.engine._openai.openai.AsyncOpenAI"):
            client = create_llm_client(LLMConfig(model="llama3"))
        assert isinstance(client, OpenAIClient)
        assert client._model == "llama3"

    def test_none_config_uses_defaults(self) -> None:
        with patch("sonar.engine._anthropic.anthropic.AsyncAnthropic"):
            client = create_llm_client(None)
        assert isinstance(client, AnthropicClient)


class TestStripCodeFences:
    def test_strips_json_fence(self) -> None:
        assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_bare_fence(self) -> None:
        assert strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_leaves_plain_json_alone(self) -> None:
        assert strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_strips_with_surrounding_whitespace(self) -> None:
        assert strip_code_fences('  ```json\n{"a": 1}\n```  ') == '{"a": 1}'

    def test_preserves_inner_newlines(self) -> None:
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        assert strip_code_fences(f"```json\n{inner}\n```") == inner
