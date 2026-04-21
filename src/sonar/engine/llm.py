"""LLM interface — thin abstraction over the LLM provider.

Phase 1: Anthropic (Haiku) direct.
Public release: swap to LiteLLM for multi-provider support.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass

import anthropic

_LOGGER = logging.getLogger("sonar.engine.llm")


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024
    max_concurrent_calls: int = 5


class LLMClient(abc.ABC):
    """Abstract async LLM interface. All LLM traffic in Sonar flows through this."""

    @abc.abstractmethod
    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Return the assistant text for a single-turn completion."""


class AnthropicClient(LLMClient):
    """Anthropic implementation of `LLMClient` using `anthropic.AsyncAnthropic`.

    API key is read from `ANTHROPIC_API_KEY` by the SDK; never accepted here.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig()
        self._client = anthropic.AsyncAnthropic(max_retries=2)

    async def generate(self, prompt: str, system: str | None = None) -> str:
        kwargs: dict = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
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
                "model": self._config.model,
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "latency_ms": latency_ms,
            },
        )
        return response.content[0].text
