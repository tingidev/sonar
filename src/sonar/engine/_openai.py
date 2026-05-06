"""OpenAI-compatible LLM client implementation."""

from __future__ import annotations

import logging
import os
import time

import openai

from sonar.engine.llm import LLMClient, _strip_code_fences

_LOGGER = logging.getLogger("sonar.engine.llm")


class OpenAIClient(LLMClient):
    """OpenAI-compatible implementation using `openai.AsyncOpenAI`.

    Supports any OpenAI-compatible endpoint (OpenAI, Ollama, Groq, Together, vLLM)
    via the `SONAR_LLM_BASE_URL` environment variable.
    """

    def __init__(self, model: str, max_tokens: int) -> None:
        self._model = model
        self._max_tokens = max_tokens

        base_url = os.environ.get("SONAR_LLM_BASE_URL")
        api_key = os.environ.get("OPENAI_API_KEY")

        if not base_url and not api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY must be set when using OpenAI-compatible models "
                "(or set SONAR_LLM_BASE_URL for local endpoints)"
            )

        self._client = openai.AsyncOpenAI(
            api_key=api_key or "placeholder",
            base_url=base_url,
            max_retries=2,
        )

    async def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        start = time.perf_counter()
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=messages,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)

        usage = response.usage
        _LOGGER.info(
            "llm_call",
            extra={
                "model": self._model,
                "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                "latency_ms": latency_ms,
            },
        )
        content = response.choices[0].message.content or ""
        return _strip_code_fences(content)
