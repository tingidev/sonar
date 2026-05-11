"""LLM interface — ABC, configuration, and factory."""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    model: str = "anthropic/claude-haiku-4-5-20251001"
    max_tokens: int = 4096
    max_concurrent_calls: int = 5
    base_url: str | None = None


class LLMClient(abc.ABC):
    """Abstract async LLM interface. All LLM traffic in Sonar flows through this."""

    @abc.abstractmethod
    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Return the assistant text for a single-turn completion."""


_ANTHROPIC_PREFIX = "anthropic/"


def create_llm_client(config: LLMConfig | None = None) -> LLMClient:
    """Factory — routes to the appropriate client based on model prefix."""
    config = config or LLMConfig()

    if config.model.startswith(_ANTHROPIC_PREFIX):
        from sonar.engine._anthropic import AnthropicClient

        bare_model = config.model[len(_ANTHROPIC_PREFIX) :]
        return AnthropicClient(model=bare_model, max_tokens=config.max_tokens)

    from sonar.engine._openai import OpenAIClient

    return OpenAIClient(model=config.model, max_tokens=config.max_tokens, base_url=config.base_url)


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping the response, if present."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1) if m else text
