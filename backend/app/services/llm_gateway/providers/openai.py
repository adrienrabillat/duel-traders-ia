"""Provider OpenAI : wrapper sur le SDK officiel openai.

Utilise chat.completions pour compatibilité maximale avec gpt-4o, gpt-4.1,
gpt-5 et les modèles compatibles. Pour les modèles "reasoning" (o1, o3) qui
n'acceptent pas temperature, il faudra adapter — c'est documenté dans
docs/llm-providers-notes.md (à venir).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError

if TYPE_CHECKING:  # pragma: no cover
    from openai import AsyncOpenAI


class OpenAIProvider:
    name: str = "openai"

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise ProviderError(
                    "openai SDK not installed. Run: pip install openai"
                ) from e
            self._client = AsyncOpenAI(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout=request.timeout_seconds,
            )
        except Exception as e:
            raise ProviderError(f"OpenAI API error: {e}") from e

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
