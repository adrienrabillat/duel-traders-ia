"""Provider Gemini : wrapper sur le SDK officiel google-genai.

Utilise l'API async via client.aio.models.generate_content. Gemini accepte
le system instruction dans la config, séparée du user content.
"""

from __future__ import annotations

import time
from typing import Any

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError


class GeminiProvider:
    name: str = "gemini"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from google import genai
            except ImportError as e:
                raise ProviderError(
                    "google-genai SDK not installed. Run: pip install google-genai"
                ) from e
            self._client = genai.Client(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model,
                contents=request.user_prompt,
                config={
                    "system_instruction": request.system_prompt,
                    "temperature": request.temperature,
                    "max_output_tokens": request.max_tokens,
                },
            )
        except Exception as e:
            raise ProviderError(f"Gemini API error: {e}") from e

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw_text = (getattr(response, "text", None) or "").strip()
        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            raw_text=raw_text,
            parsed_action=None,
            parse_error=None,
            tokens_input=getattr(usage, "prompt_token_count", 0) if usage else 0,
            tokens_output=getattr(usage, "candidates_token_count", 0) if usage else 0,
            latency_ms=elapsed_ms,
            provider=self.name,
            model=request.model,
            attempt_count=1,
        )
