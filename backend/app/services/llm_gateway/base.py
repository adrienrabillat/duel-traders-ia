"""Types de base et Protocol pour les providers LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.services.llm_gateway.actions import Action


# =============================================================================
# Exceptions
# =============================================================================


class GatewayError(Exception):
    """Erreur de base du LLM Gateway."""


class ProviderError(GatewayError):
    """Erreur côté provider (API down, rate limit, auth, etc.)."""


class ParseError(GatewayError):
    """Réponse du modèle non parsable dans le schéma d'action."""


class MaxRetriesExceededError(GatewayError):
    """Le modèle a échoué à produire un format valide après N tentatives."""


# =============================================================================
# Dataclasses Request / Response
# =============================================================================


@dataclass(slots=True)
class LLMRequest:
    """Requête vers un provider LLM.

    - system_prompt : prompt système (règles du jeu, persona, schéma d'action).
    - user_prompt : prompt utilisateur (contexte courant, pack d'info, historique).
    - model : nom exact du modèle à invoquer côté provider.
    - candidate_name : nom du candidat (pour logging et observabilité).
    """

    candidate_name: str
    model: str
    system_prompt: str
    user_prompt: str
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout_seconds: float = 120.0
    # Champs accumulés au fil des retries
    retry_feedback: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LLMResponse:
    """Réponse normalisée renvoyée par le gateway.

    Si parsed_action est None, parse_error est renseigné. Toutes les
    métadonnées (tokens, latence, provider) sont remplies dans tous les cas.
    """

    raw_text: str
    parsed_action: Action | None
    parse_error: str | None
    tokens_input: int
    tokens_output: int
    latency_ms: int
    provider: str
    model: str
    attempt_count: int = 1

    @property
    def success(self) -> bool:
        return self.parsed_action is not None and self.parse_error is None


# =============================================================================
# Protocol
# =============================================================================


@runtime_checkable
class LLMProvider(Protocol):
    """Interface qu'un provider LLM doit implémenter.

    Toutes les implémentations sont asynchrones pour permettre des appels
    concurrents (5 candidats peuvent se réveiller en quasi-simultané).
    """

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Lance une complétion et renvoie une LLMResponse.

        L'implémentation NE PARSE PAS l'action — elle renvoie raw_text et
        c'est le Gateway qui s'occupe du parsing et des retries.
        L'implémentation lève ProviderError sur problème API.
        """
        ...
