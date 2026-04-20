"""LLM interface — thin abstraction over the LLM provider.

Phase 1: Anthropic (Haiku) direct.
Public release: swap to LiteLLM for multi-provider support.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024


class LLMClient:
    """Thin LLM interface. All LLM calls go through this."""

    def __init__(self, config: LLMConfig | None = None):
        self._config = config or LLMConfig()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError
