"""LLMGateway : orchestre les appels providers + parsing + retries.

Cycle de vie d'une décision :

1. L'orchestrator construit un LLMRequest (system + user prompt) et le
   passe au Gateway avec le provider voulu.
2. Le Gateway appelle provider.complete(req).
3. Le Gateway tente d'extraire un objet JSON du raw_text (robuste aux
   fences markdown ``` et aux préambules type "Here's my decision: ...").
4. Le JSON est validé contre le schéma Action (Pydantic).
5. En cas d'échec de parsing ou de validation, le Gateway retente jusqu'à
   `max_retries` fois en injectant l'erreur précédente dans le prompt.
6. Renvoie une LLMResponse avec parsed_action renseigné en cas de succès.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace

from pydantic import ValidationError

from app.services.llm_gateway.actions import parse_action
from app.services.llm_gateway.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    MaxRetriesExceededError,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Extraction JSON robuste
# =============================================================================


_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(?P<body>\{.*?\})\s*\n?```",
    re.DOTALL | re.IGNORECASE,
)


def extract_json(text: str) -> str:
    """Extrait le plus probable objet JSON d'une réponse LLM.

    Stratégies essayées dans l'ordre :
    1. Bloc ```json {...}``` ou ``` {...} ``` (markdown fence).
    2. Premier objet JSON équilibré (parsing par comptage d'accolades).
    3. Texte brut tel quel.

    Lève json.JSONDecodeError au final si rien ne parse.
    """
    text = text.strip()

    # 1) Markdown fence
    match = _JSON_FENCE_RE.search(text)
    if match:
        candidate = match.group("body")
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 2) Premier objet JSON équilibré (algorithme à comptage)
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    # 3) Tentative brute (si le texte est entièrement un JSON propre)
    json.loads(text)  # lève si invalide
    return text


# =============================================================================
# Gateway
# =============================================================================


class LLMGateway:
    """Orchestrateur de haut niveau pour les appels LLM."""

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        max_retries: int = 2,
    ) -> None:
        """
        Args:
            providers: dict {nom_provider: instance LLMProvider}.
            max_retries: nombre de tentatives supplémentaires en cas de
                         parsing/validation échouée. 0 = un seul appel.
        """
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.providers = providers
        self.max_retries = max_retries

    async def request_decision(
        self, provider_name: str, request: LLMRequest
    ) -> LLMResponse:
        """Demande une décision à un provider, gère les retries de parsing.

        Lève KeyError si provider_name inconnu, MaxRetriesExceededError si
        tous les essais échouent, ProviderError si le provider plante.
        """
        if provider_name not in self.providers:
            raise KeyError(f"Provider unknown: {provider_name!r}")
        provider = self.providers[provider_name]

        last_response: LLMResponse | None = None
        attempts = self.max_retries + 1

        current_request = request
        for attempt in range(1, attempts + 1):
            response = await provider.complete(current_request)
            parsed, error = self._try_parse(response.raw_text)
            response = replace(
                response,
                parsed_action=parsed,
                parse_error=error,
                attempt_count=attempt,
            )
            last_response = response

            if parsed is not None:
                if attempt > 1:
                    logger.info(
                        "Provider %s succeeded after %d attempts (candidate=%s)",
                        provider_name,
                        attempt,
                        request.candidate_name,
                    )
                return response

            logger.warning(
                "Provider %s attempt %d/%d failed parsing for candidate=%s: %s",
                provider_name,
                attempt,
                attempts,
                request.candidate_name,
                error,
            )

            if attempt == attempts:
                break

            current_request = self._build_retry_request(current_request, error or "unknown")

        # Tous les essais ont échoué
        assert last_response is not None
        raise MaxRetriesExceededError(
            f"Provider {provider_name} failed to produce valid action after "
            f"{attempts} attempt(s). Last error: {last_response.parse_error}"
        )

    # ------------------------------------------------------------------ Internal

    @staticmethod
    def _try_parse(raw_text: str) -> tuple[object | None, str | None]:
        """Tente d'extraire puis valider une Action. Renvoie (action, None) ou (None, msg)."""
        try:
            json_text = extract_json(raw_text)
        except json.JSONDecodeError as e:
            return None, f"No valid JSON found in response: {e}"

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as e:  # pragma: no cover (extract_json garantit json.loads OK)
            return None, f"JSON decode error: {e}"

        try:
            action = parse_action(payload)
        except ValidationError as e:
            return None, f"Schema validation failed: {e.errors(include_url=False)}"

        return action, None

    @staticmethod
    def _build_retry_request(previous: LLMRequest, error: str) -> LLMRequest:
        """Construit un retry en injectant l'erreur précédente dans le user prompt."""
        feedback = (
            "\n\n---\n"
            f"[ERREUR DE FORMAT à la tentative précédente : {error}]\n"
            "Réponds UNIQUEMENT par un objet JSON valide conforme au schéma. "
            "Pas de texte autour, pas de balises markdown."
        )
        return LLMRequest(
            candidate_name=previous.candidate_name,
            model=previous.model,
            system_prompt=previous.system_prompt,
            user_prompt=previous.user_prompt + feedback,
            max_tokens=previous.max_tokens,
            temperature=previous.temperature,
            timeout_seconds=previous.timeout_seconds,
            retry_feedback=[*previous.retry_feedback, error],
        )
