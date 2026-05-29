"""Provider Anthropic : implémentation réelle au-dessus du SDK officiel.

Sert de référence pour les 4 autres providers (OpenAI, Mistral, Google, xAI),
qui auront tous le même squelette : late import du SDK, appel de l'API,
mapping vers LLMRequest/LLMResponse, gestion des erreurs en ProviderError.

Note : le SDK `anthropic` doit être installé pour utiliser cette classe.
L'import est différé (à l'instanciation et à l'appel) pour que le module
soit importable même si la lib n'est pas installée — pratique pour les
tests qui ne touchent que MockProvider.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError

if TYPE_CHECKING:  # pragma: no cover
    from anthropic import AsyncAnthropic


class AnthropicProvider:
    """Provider pour Claude (modèles Anthropic).

    Args:
        api_key: clé API Anthropic.
        client: client async optionnel (utile pour les tests d'intégration).
    """

    name: str = "anthropic"

    def __init__(
        self,
        api_key: str,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise ProviderError(
                    "anthropic SDK not installed. Run: pip install anthropic"
                ) from e
            self._client = AsyncAnthropic(api_key=api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Appelle l'API Anthropic Messages."""
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                system=request.system_prompt,
                messages=[
                    {"role": "user", "content": request.user_prompt},
                ],
                timeout=request.timeout_seconds,
            )
        except Exception as e:
            raise ProviderError(f"Anthropic API error: {e}") from e

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        # Concaténer les content blocks de type text
        raw_text = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        )

        usage: Any = message.usage
        return LLMResponse(
            raw_text=raw_text,
            parsed_action=None,  # parsé par le Gateway
            parse_error=None,
            tokens_input=getattr(usage, "input_tokens", 0),
            tokens_output=getattr(usage, "output_tokens", 0),
            latency_ms=elapsed_ms,
            provider=self.name,
            model=request.model,
            attempt_count=1,
        )
