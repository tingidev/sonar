"""Unit tests for the LLM client abstraction and AnthropicClient."""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest

from sonar.engine.llm import AnthropicClient, LLMClient, LLMConfig


class TestLLMConfig:
    def test_defaults(self) -> None:
        config = LLMConfig()
        assert config.provider == "anthropic"
        assert config.model == "claude-haiku-4-5-20251001"
        assert config.max_tokens == 1024
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
    @pytest.mark.asyncio
    async def test_generate_calls_messages_create_with_expected_args(self) -> None:
        fake_response = _fake_anthropic_response("hello world")
        with patch("sonar.engine.llm.anthropic.AsyncAnthropic") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.messages.create = mock_create

            client = AnthropicClient(LLMConfig(max_tokens=256))
            result = await client.generate("hi", system="be nice")

            assert result == "hello world"
            mock_cls.assert_called_once_with(max_retries=2)
            mock_create.assert_awaited_once_with(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system="be nice",
                messages=[{"role": "user", "content": "hi"}],
            )

    @pytest.mark.asyncio
    async def test_generate_without_system_omits_system_arg(self) -> None:
        fake_response = _fake_anthropic_response("no system")
        with patch("sonar.engine.llm.anthropic.AsyncAnthropic") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.messages.create = mock_create

            client = AnthropicClient()
            await client.generate("hi")

            kwargs = mock_create.await_args.kwargs
            assert "system" not in kwargs
            assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_generate_returns_content_0_text(self) -> None:
        fake_response = _fake_anthropic_response("extracted text")
        with patch("sonar.engine.llm.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(return_value=fake_response)
            client = AnthropicClient()
            assert await client.generate("x") == "extracted text"

    def test_constructor_rejects_api_key_kwarg(self) -> None:
        with pytest.raises(TypeError):
            AnthropicClient(api_key="sk-test")  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_logs_info_record_without_payload(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        prompt = "please describe the orders table"
        system = "you are a data analyst"
        response_text = "nothing about the prompt appears here"
        fake_response = _fake_anthropic_response(
            response_text, input_tokens=111, output_tokens=9
        )

        with patch("sonar.engine.llm.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(return_value=fake_response)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = AnthropicClient(LLMConfig(model="test-model"))
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

    @pytest.mark.asyncio
    async def test_no_log_on_provider_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        api_error = anthropic.APIError(message="boom", request=request, body=None)
        with patch("sonar.engine.llm.anthropic.AsyncAnthropic") as mock_cls:
            mock_cls.return_value.messages.create = AsyncMock(side_effect=api_error)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = AnthropicClient()
                with pytest.raises(anthropic.APIError):
                    await client.generate("x")

        assert [r for r in caplog.records if r.name == "sonar.engine.llm"] == []
