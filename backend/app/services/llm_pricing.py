"""Pricing des appels LLM par modèle, en USD par million de tokens.

Les prix changent régulièrement — vérifier sur :
- Anthropic : https://www.anthropic.com/pricing
- OpenAI    : https://openai.com/api/pricing/
- Mistral   : https://mistral.ai/pricing
- Google    : https://ai.google.dev/pricing
- xAI       : https://x.ai/api

Pour ajouter ou mettre à jour un prix, modifier le dict PRICING ci-dessous.
Le système calcule le coût par appel à la volée à partir des tokens stockés
dans Decision, donc une mise à jour de tarif rétroactive est sans risque.

DEFAULT_PRICE est utilisé pour tout modèle non listé (sécurité par défaut).
"""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple


class ModelPrice(NamedTuple):
    """Coût USD par million de tokens, séparé input / output."""

    input_per_million_usd: Decimal
    output_per_million_usd: Decimal


# Estimation au 28 mai 2026 — à ajuster avec les vraies grilles tarifaires.
# Quand un nouveau modèle sort, ajouter une ligne ici.
PRICING: dict[str, ModelPrice] = {
    # --- Anthropic ---
    "claude-opus-4-8": ModelPrice(Decimal("15.00"), Decimal("75.00")),
    "claude-opus-4-7": ModelPrice(Decimal("15.00"), Decimal("75.00")),
    "claude-opus-4-6": ModelPrice(Decimal("15.00"), Decimal("75.00")),
    "claude-sonnet-4-6": ModelPrice(Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": ModelPrice(Decimal("1.00"), Decimal("5.00")),
    "claude-haiku-4-5-20251001": ModelPrice(Decimal("1.00"), Decimal("5.00")),
    # --- OpenAI ---
    "gpt-5.5": ModelPrice(Decimal("5.00"), Decimal("20.00")),
    "gpt-5": ModelPrice(Decimal("5.00"), Decimal("20.00")),
    "gpt-5-mini": ModelPrice(Decimal("0.25"), Decimal("2.00")),
    "gpt-4o": ModelPrice(Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": ModelPrice(Decimal("0.15"), Decimal("0.60")),
    # --- Mistral ---
    "mistral-large-latest": ModelPrice(Decimal("3.00"), Decimal("12.00")),
    "mistral-large-2512": ModelPrice(Decimal("3.00"), Decimal("12.00")),
    "mistral-medium-latest": ModelPrice(Decimal("1.00"), Decimal("3.00")),
    # --- Google ---
    "gemini-2.5-pro": ModelPrice(Decimal("1.25"), Decimal("5.00")),
    "gemini-2.5-flash": ModelPrice(Decimal("0.30"), Decimal("1.20")),
    # --- xAI ---
    "grok-4.3": ModelPrice(Decimal("5.00"), Decimal("15.00")),
    "grok-4.20": ModelPrice(Decimal("5.00"), Decimal("15.00")),
    "grok-4": ModelPrice(Decimal("5.00"), Decimal("15.00")),
    # --- Mock / fallback ---
    "mock": ModelPrice(Decimal("0"), Decimal("0")),
}

DEFAULT_PRICE = ModelPrice(Decimal("5.00"), Decimal("15.00"))

_QUANTUM = Decimal("0.0000001")


def get_price(model: str) -> ModelPrice:
    """Récupère le prix d'un modèle, ou le défaut si inconnu."""
    return PRICING.get(model, DEFAULT_PRICE)


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """Calcule le coût total d'un appel LLM en USD.

    >>> cost_usd("claude-haiku-4-5-20251001", 1000, 500)
    Decimal('0.0035000')
    """
    if tokens_in < 0 or tokens_out < 0:
        return Decimal("0")
    price = get_price(model)
    cost_in = Decimal(tokens_in) * price.input_per_million_usd / Decimal("1000000")
    cost_out = Decimal(tokens_out) * price.output_per_million_usd / Decimal("1000000")
    return (cost_in + cost_out).quantize(_QUANTUM)
