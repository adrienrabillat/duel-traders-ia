"""Schéma Pydantic des actions qu'un candidat peut produire à chaque réveil.

Toutes les sorties LLM sont contraintes à respecter ce schéma. Un
discriminated union sur le champ `action` permet à Pydantic de dispatcher
automatiquement vers la bonne variante.

Convention :
- Tous les montants USDC en Decimal (jamais float).
- Les % sont des Decimal (3.5 = 3.5%, pas 0.035).
- Les chaînes texte sont plafonnées pour limiter la taille du contexte.
"""

from __future__ import annotations

import json
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class StrEnum(str, Enum):
    """str-Enum compatible Python 3.10. Sur 3.11+, on pourrait utiliser enum.StrEnum directement."""

    def __str__(self) -> str:  # pragma: no cover
        return self.value


# =============================================================================
# Énum des actions
# =============================================================================


class ActionKind(StrEnum):
    PLACE_ORDER = "PLACE_ORDER"
    CLOSE_POSITION = "CLOSE_POSITION"
    UPDATE_STRATEGY = "UPDATE_STRATEGY"
    QUERY_ORACLE = "QUERY_ORACLE"
    SEND_MESSAGE = "SEND_MESSAGE"
    HOLD = "HOLD"


# =============================================================================
# Bases communes
# =============================================================================


class _ActionBase(BaseModel):
    """Champs communs à toutes les actions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reasoning_summary: str = Field(
        ...,
        max_length=500,
        description=(
            "Résumé condensé du raisonnement ayant mené à cette action. "
            "Max 500 caractères. Le raisonnement complet n'est pas attendu ici."
        ),
    )


# =============================================================================
# Variantes
# =============================================================================


class PlaceOrderAction(_ActionBase):
    """Passe un ordre Spot avec OCO (stop-loss + take-profit obligatoires)."""

    action: Literal[ActionKind.PLACE_ORDER] = ActionKind.PLACE_ORDER
    side: Literal["BUY", "SELL"] = Field(
        ..., description="Sens de l'ordre. BUY ouvre une position long ; SELL clôture (ou short non supporté)."
    )
    symbol: str = Field(
        ...,
        min_length=4,
        max_length=20,
        pattern=r"^[A-Z0-9]+USDC$",
        description="Paire Binance, terminée par USDC. Ex : BTCUSDC, SOLUSDC.",
    )
    quote_amount_usdc: Decimal = Field(
        ...,
        gt=Decimal("0"),
        description="Montant USDC à engager dans l'ordre (notional). Minimum 5 USDC.",
    )
    stop_loss_pct: Decimal = Field(
        ...,
        gt=Decimal("0"),
        le=Decimal("99"),
        description="Distance du stop-loss en pourcentage par rapport au prix d'entrée. Ex : 3.5 = -3.5%.",
    )
    take_profit_pct: Decimal = Field(
        ...,
        gt=Decimal("0"),
        description="Distance du take-profit en pourcentage par rapport au prix d'entrée. Ex : 7 = +7%.",
    )


class ClosePositionAction(_ActionBase):
    """Clôture manuelle d'une position ouverte avant déclenchement OCO."""

    action: Literal[ActionKind.CLOSE_POSITION] = ActionKind.CLOSE_POSITION
    symbol: str = Field(..., min_length=4, max_length=20, pattern=r"^[A-Z0-9]+USDC$")


class StrategyCategory(StrEnum):
    """Les 8 catégories tradables. Chaque candidat doit en choisir UNE.
    Il ne peut ensuite trader que dans cette catégorie. Peut changer via
    UPDATE_STRATEGY (pivot narratif visible côté UI)."""

    MAJORS = "MAJORS"
    L1 = "L1"
    L2 = "L2"
    DEFI = "DEFI"
    MEMECOINS = "MEMECOINS"
    AI = "AI"
    GAMING = "GAMING"
    PRIVACY_RWA = "PRIVACY_RWA"


class UpdateStrategyAction(_ActionBase):
    """Mise à jour de la stratégie écrite + choix de catégorie.

    La category est OBLIGATOIRE : c'est la thèse d'investissement du candidat.
    Tant qu'il n'a pas de catégorie, il ne peut pas trader (PlaceOrderAction
    rejeté). Il peut changer de catégorie via une nouvelle UPDATE_STRATEGY
    (mais c'est un pivot narratif fort, visible dans l'UI)."""

    action: Literal[ActionKind.UPDATE_STRATEGY] = ActionKind.UPDATE_STRATEGY
    category: StrategyCategory = Field(
        ...,
        description=(
            "Catégorie de coins tradables pour ce candidat. Choix parmi : "
            "MAJORS, L1, L2, DEFI, MEMECOINS, AI, GAMING, PRIVACY_RWA. "
            "Tu ne pourras trader que dans cette catégorie."
        ),
    )
    new_strategy: str = Field(..., min_length=20, max_length=2000)


class QueryOracleAction(_ActionBase):
    """Question textuelle posée à l'oracle (Tavily). Sera anonymisée et publiée
    dans le feed partagé visible par tous les candidats."""

    action: Literal[ActionKind.QUERY_ORACLE] = ActionKind.QUERY_ORACLE
    question: str = Field(..., min_length=10, max_length=500)


class SendMessageAction(_ActionBase):
    """Envoi de message inter-modèles (canal public, confessionnal, ou clan).

    - channel='public' : recipients ignoré, lu par tous.
    - channel='private' : recipients = exactement 1 candidat.
    - channel='clan' : recipients = le nom du clan (un seul élément).
    """

    action: Literal[ActionKind.SEND_MESSAGE] = ActionKind.SEND_MESSAGE
    channel: Literal["public", "private", "clan"]
    recipients: list[str] = Field(
        default_factory=list,
        description="Liste vide pour 'public'. Un seul nom pour 'private' ou 'clan'.",
    )
    content: str = Field(..., min_length=1, max_length=1500)


class HoldAction(_ActionBase):
    """Ne rien faire ce tour. Optionnellement, programmer la prochaine décision."""

    action: Literal[ActionKind.HOLD] = ActionKind.HOLD
    next_wake_at_utc: str | None = Field(
        None,
        description=(
            "ISO 8601 UTC de la prochaine décision souhaitée. "
            "Si None ou absent, réveil par défaut dans 1 heure."
        ),
    )


# =============================================================================
# Union discriminée
# =============================================================================


Action = Annotated[
    Union[
        PlaceOrderAction,
        ClosePositionAction,
        UpdateStrategyAction,
        QueryOracleAction,
        SendMessageAction,
        HoldAction,
    ],
    Field(discriminator="action"),
]

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(payload: dict | str) -> Action:
    """Parse un dict (ou un str JSON) en Action validée.

    Lève pydantic.ValidationError si le payload ne respecte pas le schéma.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    return _ACTION_ADAPTER.validate_python(payload)


# =============================================================================
# Génération du schéma pour le system prompt
# =============================================================================


def action_schema_for_prompt() -> str:
    """Renvoie une description lisible du schéma à injecter dans le system prompt.

    Préférée à model_json_schema() brut, car plus courte et compréhensible
    par les LLMs (gain en tokens + meilleure conformité).
    """
    return """Tu DOIS répondre par un unique objet JSON conforme à l'un des schémas
ci-dessous. Aucun texte avant ou après le JSON. Pas de ```json``` (juste l'objet).

PLACE_ORDER : passer un ordre Spot avec OCO.
{
  "action": "PLACE_ORDER",
  "side": "BUY",
  "symbol": "BTCUSDC",
  "quote_amount_usdc": "8.50",
  "stop_loss_pct": "3.5",
  "take_profit_pct": "7.0",
  "reasoning_summary": "..."
}

CLOSE_POSITION : clôturer manuellement une position ouverte.
{
  "action": "CLOSE_POSITION",
  "symbol": "BTCUSDC",
  "reasoning_summary": "..."
}

UPDATE_STRATEGY : modifier la stratégie écrite sans passer d'ordre.
{
  "action": "UPDATE_STRATEGY",
  "new_strategy": "...",
  "reasoning_summary": "..."
}

QUERY_ORACLE : poser une question à l'oracle web (Tavily). Visible des autres.
{
  "action": "QUERY_ORACLE",
  "question": "...",
  "reasoning_summary": "..."
}

SEND_MESSAGE : envoyer un message inter-modèles. Disponible à partir de l'épisode 3.
{
  "action": "SEND_MESSAGE",
  "channel": "public" | "private" | "clan",
  "recipients": [],  // vide pour public ; sinon un nom de candidat ou de clan
  "content": "...",
  "reasoning_summary": "..."
}

HOLD : ne rien faire ce tour. Optionnellement programmer la prochaine décision.
{
  "action": "HOLD",
  "next_wake_at_utc": "2026-05-30T14:00:00Z",
  "reasoning_summary": "..."
}

Contraintes :
- Tous les montants USDC sont des strings décimales (Decimal-compatible).
- Les pourcentages sont des nombres positifs (3.5 = 3.5%, pas 0.035).
- Les symboles de paire doivent finir par USDC.
- reasoning_summary obligatoire pour TOUTES les actions, max 500 caractères.
"""


def get_action_json_schema() -> dict:
    """Renvoie le JSON Schema complet (utile pour l'output structuré côté API)."""
    return _ACTION_ADAPTER.json_schema()
