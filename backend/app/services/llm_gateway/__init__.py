"""LLM Gateway : abstraction unifiée au-dessus des fournisseurs de LLM.

Le Gateway prend en entrée un prompt + un schéma d'action attendu, appelle
le provider concerné, parse la réponse JSON, retente en cas d'erreur de
format, et renvoie une LLMResponse structurée.
"""

from app.services.llm_gateway.actions import (
    Action,
    ActionKind,
    ClosePositionAction,
    HoldAction,
    PlaceOrderAction,
    QueryOracleAction,
    SendMessageAction,
    UpdateStrategyAction,
    action_schema_for_prompt,
    parse_action,
)
from app.services.llm_gateway.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ParseError,
    ProviderError,
)
from app.services.llm_gateway.gateway import LLMGateway
from app.services.llm_gateway.providers.anthropic import AnthropicProvider
from app.services.llm_gateway.providers.gemini import GeminiProvider
from app.services.llm_gateway.providers.mistral import MistralProvider
from app.services.llm_gateway.providers.mock import MockProvider
from app.services.llm_gateway.providers.openai import OpenAIProvider
from app.services.llm_gateway.providers.xai import XAIProvider

__all__ = [
    "Action",
    "ActionKind",
    "AnthropicProvider",
    "ClosePositionAction",
    "GeminiProvider",
    "HoldAction",
    "LLMGateway",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "MistralProvider",
    "MockProvider",
    "OpenAIProvider",
    "ParseError",
    "PlaceOrderAction",
    "ProviderError",
    "QueryOracleAction",
    "SendMessageAction",
    "UpdateStrategyAction",
    "XAIProvider",
    "action_schema_for_prompt",
    "parse_action",
]
