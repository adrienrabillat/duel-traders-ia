"""MockProvider : provider de test scriptable.

Permet de tester l'orchestration du Gateway (retries, parsing, etc.) sans
appeler de vraie API. Deux modes :

1. Réponse fixe : passer un texte au constructeur, il sera renvoyé à chaque appel.
2. Réponses séquentielles : passer une liste ; chaque appel consomme le suivant.
   Utile pour simuler des erreurs au premier essai puis une réussite au retry.
"""

from __future__ import annotations

from collections.abc import Callable

from app.services.llm_gateway.base import LLMRequest, LLMResponse, ProviderError


class MockProvider:
    """Provider de test.

    Args:
        responses: soit un str unique (renvoyé à chaque appel), soit une
                   séquence (consommée dans l'ordre, à la fin lève une erreur).
        fail_on: indices d'appel pour lesquels lever ProviderError (pour
                 simuler des pannes API).
        on_request: callback optionnel invoqué avec chaque LLMRequest (utile
                    pour vérifier ce qui est envoyé).
    """

    name: str = "mock"

    def __init__(
        self,
        responses: str | list[str],
        fail_on: list[int] | None = None,
        on_request: Callable[[LLMRequest], None] | None = None,
    ) -> None:
        self._responses: list[str] = [responses] if isinstance(responses, str) else list(responses)
        self._fixed: bool = isinstance(responses, str)
        self._fail_on: set[int] = set(fail_on or [])
        self._on_request = on_request
        self.calls: list[LLMRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        idx = len(self.calls)
        self.calls.append(request)
        if self._on_request is not None:
            self._on_request(request)
        if idx in self._fail_on:
            raise ProviderError(f"Mock simulated failure on call #{idx}")
        if self._fixed:
            text = self._responses[0]
        else:
            if idx >= len(self._responses):
                raise ProviderError(
                    f"MockProvider out of scripted responses at call #{idx}"
                )
            text = self._responses[idx]
        return LLMResponse(
            raw_text=text,
            parsed_action=None,  # le Gateway parse derrière
            parse_error=None,
            tokens_input=len(request.system_prompt) // 4 + len(request.user_prompt) // 4,
            tokens_output=len(text) // 4,
            latency_ms=1,
            provider=self.name,
            model=request.model,
            attempt_count=1,
        )
