"""Provider xAI Grok : utilise l'API OpenAI-compatible de xAI.

xAI expose une API au format chat.completions à https://api.x.ai/v1, ce qui
permet de réutiliser le SDK openai en pointant simplement la base_url.
"""

from __future__ import annotations

import time
from typing import Any

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError


class XAIProvider:
    name: str = "xai"

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise ProviderError(
                    "openai SDK requis pour xAI. Run: pip install openai"
                ) from e
            self._client = AsyncOpenAI(api_key=api_key, base_url=self.BASE_URL)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        # Grok 4.x reasoning models : pas de temperature, max_completion_tokens
        is_reasoning_family = request.model.startswith("grok-4") or "reasoning" in request.model
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "timeout": request.timeout_seconds,
        }
        if is_reasoning_family:
            kwargs["max_completion_tokens"] = request.max_tokens
        else:
            kwargs["max_tokens"] = request.max_tokens
            kwargs["temperature"] = request.temperature
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise ProviderError(f"xAI API error: {e}") from e

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        choice = response.choices[0]
        raw_text = (choice.message.content or "").strip()
        usage = response.usage
        return LLMResponse(
            raw_text=raw_text,
            parsed_action=None,
            parse_error=None,
            tokens_input=getattr(usage, "prompt_tokens", 0) if usage else 0,
            tokens_output=getattr(usage, "completion_tokens", 0) if usage else 0,
            latency_ms=elapsed_ms,
            provider=self.name,
            model=request.model,
            attempt_count=1,
        )
