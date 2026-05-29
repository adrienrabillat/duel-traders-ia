"""Provider Mistral : wrapper sur le SDK officiel mistralai (v1.x)."""

from __future__ import annotations

import time
from typing import Any

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError


class MistralProvider:
    name: str = "mistral"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from mistralai import Mistral
            except ImportError as e:
                raise ProviderError(
                    "mistralai SDK not installed. Run: pip install mistralai"
                ) from e
            self._client = Mistral(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.chat.complete_async(
                model=request.model,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except Exception as e:
            raise ProviderError(f"Mistral API error: {e}") from e

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
