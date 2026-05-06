"""Unit tests for the OpenAI-compatible LLM client."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
import pytest

from sonar.engine._openai import OpenAIClient


def _fake_openai_response(
    text: str, *, prompt_tokens: int = 30, completion_tokens: int = 12
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class TestOpenAIClient:
    @pytest.fixture(autouse=True)
    def _set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    @pytest.mark.asyncio
    async def test_generate_calls_chat_completions_create(self) -> None:
        fake_response = _fake_openai_response("hello world")
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.chat.completions.create = mock_create

            client = OpenAIClient(model="gpt-4o", max_tokens=256)
            result = await client.generate("hi", system="be nice")

            assert result == "hello world"
            mock_create.assert_awaited_once_with(
                model="gpt-4o",
                max_tokens=256,
                messages=[
                    {"role": "system", "content": "be nice"},
                    {"role": "user", "content": "hi"},
                ],
            )

    @pytest.mark.asyncio
    async def test_generate_without_system_omits_system_message(self) -> None:
        fake_response = _fake_openai_response("no system")
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_create = AsyncMock(return_value=fake_response)
            mock_cls.return_value.chat.completions.create = mock_create

            client = OpenAIClient(model="gpt-4o", max_tokens=4096)
            await client.generate("hi")

            kwargs = mock_create.await_args.kwargs
            assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_base_url_override_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONAR_LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = AsyncMock(
                return_value=_fake_openai_response("ok")
            )
            OpenAIClient(model="llama3", max_tokens=4096)
            mock_cls.assert_called_once_with(
                api_key="placeholder",
                base_url="http://localhost:11434/v1",
                max_retries=2,
            )

    def test_missing_key_without_base_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SONAR_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY must be set"):
            OpenAIClient(model="gpt-4o", max_tokens=4096)

    @pytest.mark.asyncio
    async def test_no_key_local_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONAR_LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = AsyncMock(
                return_value=_fake_openai_response("local response")
            )
            client = OpenAIClient(model="llama3", max_tokens=4096)
            result = await client.generate("hello")
            assert result == "local response"

    @pytest.mark.asyncio
    async def test_logs_info_record_on_success(self, caplog: pytest.LogCaptureFixture) -> None:
        prompt = "describe the orders table"
        system = "you are a data analyst"
        response_text = "the orders table stores purchase records"
        fake_response = _fake_openai_response(response_text, prompt_tokens=50, completion_tokens=20)

        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = AsyncMock(return_value=fake_response)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = OpenAIClient(model="gpt-4o", max_tokens=4096)
                await client.generate(prompt, system=system)

        records = [r for r in caplog.records if r.name == "sonar.engine.llm"]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.INFO
        assert record.model == "gpt-4o"
        assert record.input_tokens == 50
        assert record.output_tokens == 20
        assert isinstance(record.latency_ms, int)
        assert record.latency_ms >= 0

        rendered = record.getMessage()
        assert prompt not in rendered
        assert system not in rendered
        assert response_text not in rendered

    @pytest.mark.asyncio
    async def test_no_log_on_provider_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = AsyncMock(
                side_effect=openai.APIError(
                    message="boom",
                    request=None,
                    body=None,
                )
            )
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="sonar.engine.llm"):
                client = OpenAIClient(model="gpt-4o", max_tokens=4096)
                with pytest.raises(openai.APIError):
                    await client.generate("x")

        assert [r for r in caplog.records if r.name == "sonar.engine.llm"] == []

    @pytest.mark.asyncio
    async def test_generate_strips_code_fences(self) -> None:
        fenced = '```json\n{"key": "value"}\n```'
        fake_response = _fake_openai_response(fenced)
        with patch("sonar.engine._openai.openai.AsyncOpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create = AsyncMock(return_value=fake_response)
            client = OpenAIClient(model="gpt-4o", max_tokens=4096)
            result = await client.generate("x")
        assert result == '{"key": "value"}'
