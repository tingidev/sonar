"""Anthropic LLM client implementation."""

from __future__ import annotations

import logging
import time

import anthropic

from sonar.engine.llm import LLMClient, strip_code_fences

_LOGGER = logging.getLogger("sonar.engine.llm")


class AnthropicClient(LLMClient):
    """Anthropic implementation using `anthropic.AsyncAnthropic`.

    API key is read from `ANTHROPIC_API_KEY` by the SDK; never accepted here.
    """

    def __init__(self, model: str, max_tokens: int) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(max_retries=2)

    async def generate(self, prompt: str, system: str | None = None) -> str:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system

        start = time.perf_counter()
        response = await self._client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        usage = getattr(response, "usage", None)
        _LOGGER.info(
            "llm_call",
            extra={
                "model": self._model,
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "latency_ms": latency_ms,
            },
        )
        return strip_code_fences(response.content[0].text)
