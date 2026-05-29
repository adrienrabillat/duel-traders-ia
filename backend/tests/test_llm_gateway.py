"""Tests du LLMGateway.

Couvre :
- Extraction JSON robuste (texte brut, markdown fence, préambule, JSON imbriqué)
- Parsing en Action validée
- Retries avec injection d'erreur dans le prompt
- Cas où tous les essais échouent (MaxRetriesExceededError)
- Cas de panne provider (ProviderError remonte)
- Provider inconnu
- Logging et compteur d'essais
"""

from __future__ import annotations

import json

import pytest

from app.services.llm_gateway import (
    HoldAction,
    LLMGateway,
    LLMRequest,
    MockProvider,
    PlaceOrderAction,
)
from app.services.llm_gateway.base import (
    LLMResponse,
    MaxRetriesExceededError,
    ProviderError,
)
from app.services.llm_gateway.gateway import extract_json


# =============================================================================
# Fixtures
# =============================================================================


def _make_request(candidate: str = "Claude") -> LLMRequest:
    return LLMRequest(
        candidate_name=candidate,
        model="claude-opus-4-7",
        system_prompt="Tu es un trader.",
        user_prompt="Quelle est ta décision ?",
    )


def _valid_hold_json() -> str:
    return '{"action": "HOLD", "reasoning_summary": "Attente du signal."}'


def _valid_place_order_json() -> str:
    return json.dumps({
        "action": "PLACE_ORDER",
        "side": "BUY",
        "symbol": "BTCUSDC",
        "quote_amount_usdc": "8.50",
        "stop_loss_pct": "3.5",
        "take_profit_pct": "7.0",
        "reasoning_summary": "Breakout confirmé.",
    })


# =============================================================================
# extract_json
# =============================================================================


class TestExtractJson:
    def test_raw_json(self) -> None:
        text = '{"action": "HOLD", "reasoning_summary": "x"}'
        assert json.loads(extract_json(text)) == {"action": "HOLD", "reasoning_summary": "x"}

    def test_markdown_fence_json(self) -> None:
        text = '```json\n{"action": "HOLD", "reasoning_summary": "x"}\n```'
        assert json.loads(extract_json(text))["action"] == "HOLD"

    def test_markdown_fence_no_lang(self) -> None:
        text = '```\n{"action": "HOLD", "reasoning_summary": "x"}\n```'
        assert json.loads(extract_json(text))["action"] == "HOLD"

    def test_preamble_before_json(self) -> None:
        text = 'Voici ma décision :\n{"action": "HOLD", "reasoning_summary": "x"}'
        assert json.loads(extract_json(text))["action"] == "HOLD"

    def test_postamble_after_json(self) -> None:
        text = '{"action": "HOLD", "reasoning_summary": "x"}\n\nVoilà.'
        assert json.loads(extract_json(text))["action"] == "HOLD"

    def test_string_with_braces_in_content(self) -> None:
        # reasoning_summary contient des accolades dans une string : ne doit pas casser le parser
        text = '{"action": "HOLD", "reasoning_summary": "regex {.*}"}'
        assert json.loads(extract_json(text))["reasoning_summary"] == "regex {.*}"

    def test_escaped_quote_in_string(self) -> None:
        text = r'{"action": "HOLD", "reasoning_summary": "He said \"hi\""}'
        assert "hi" in json.loads(extract_json(text))["reasoning_summary"]

    def test_no_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            extract_json("Pas de JSON ici.")


# =============================================================================
# Gateway : succès au premier coup
# =============================================================================


@pytest.mark.asyncio
class TestGatewaySuccess:
    async def test_simple_success(self) -> None:
        provider = MockProvider(_valid_hold_json())
        gateway = LLMGateway({"claude": provider})
        response = await gateway.request_decision("claude", _make_request())
        assert response.success
        assert isinstance(response.parsed_action, HoldAction)
        assert response.attempt_count == 1
        assert provider.call_count == 1

    async def test_success_with_markdown_fence(self) -> None:
        provider = MockProvider(f"```json\n{_valid_hold_json()}\n```")
        gateway = LLMGateway({"claude": provider})
        response = await gateway.request_decision("claude", _make_request())
        assert response.success
        assert isinstance(response.parsed_action, HoldAction)

    async def test_success_with_preamble(self) -> None:
        provider = MockProvider(f"Voici ma décision :\n{_valid_place_order_json()}")
        gateway = LLMGateway({"claude": provider})
        response = await gateway.request_decision("claude", _make_request())
        assert response.success
        assert isinstance(response.parsed_action, PlaceOrderAction)


# =============================================================================
# Gateway : retries
# =============================================================================


@pytest.mark.asyncio
class TestGatewayRetries:
    async def test_succeeds_after_one_retry(self) -> None:
        provider = MockProvider([
            "Pas de JSON dans cette réponse.",
            _valid_hold_json(),
        ])
        gateway = LLMGateway({"claude": provider}, max_retries=2)
        response = await gateway.request_decision("claude", _make_request())
        assert response.success
        assert response.attempt_count == 2
        assert provider.call_count == 2

    async def test_retry_request_includes_error_feedback(self) -> None:
        provider = MockProvider([
            "Pas de JSON.",
            _valid_hold_json(),
        ])
        gateway = LLMGateway({"claude": provider})
        await gateway.request_decision("claude", _make_request())
        second_call = provider.calls[1]
        assert "ERREUR DE FORMAT" in second_call.user_prompt
        assert len(second_call.retry_feedback) == 1

    async def test_all_retries_fail_raises(self) -> None:
        provider = MockProvider([
            "Pas de JSON.",
            "Toujours rien.",
            "Encore raté.",
        ])
        gateway = LLMGateway({"claude": provider}, max_retries=2)
        with pytest.raises(MaxRetriesExceededError):
            await gateway.request_decision("claude", _make_request())
        # max_retries=2 => 1 essai initial + 2 retries = 3 appels
        assert provider.call_count == 3

    async def test_schema_violation_triggers_retry(self) -> None:
        invalid_payload = '{"action": "PLACE_ORDER", "side": "BUY", "symbol": "BTCUSDT", "quote_amount_usdc": "5", "stop_loss_pct": "3", "take_profit_pct": "5", "reasoning_summary": "x"}'
        provider = MockProvider([
            invalid_payload,  # mauvais suffix USDT au lieu de USDC
            _valid_hold_json(),
        ])
        gateway = LLMGateway({"claude": provider}, max_retries=1)
        response = await gateway.request_decision("claude", _make_request())
        assert response.success

    async def test_max_retries_zero_no_retry(self) -> None:
        provider = MockProvider(["Pas de JSON."])
        gateway = LLMGateway({"claude": provider}, max_retries=0)
        with pytest.raises(MaxRetriesExceededError):
            await gateway.request_decision("claude", _make_request())
        assert provider.call_count == 1


# =============================================================================
# Gateway : erreurs amont
# =============================================================================


@pytest.mark.asyncio
class TestGatewayErrors:
    async def test_unknown_provider(self) -> None:
        gateway = LLMGateway({"claude": MockProvider(_valid_hold_json())})
        with pytest.raises(KeyError):
            await gateway.request_decision("inconnu", _make_request())

    async def test_provider_error_propagates(self) -> None:
        provider = MockProvider(_valid_hold_json(), fail_on=[0])
        gateway = LLMGateway({"claude": provider})
        with pytest.raises(ProviderError):
            await gateway.request_decision("claude", _make_request())

    async def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValueError):
            LLMGateway({"x": MockProvider(_valid_hold_json())}, max_retries=-1)


# =============================================================================
# Réponse contient les métadonnées
# =============================================================================


@pytest.mark.asyncio
class TestResponseMetadata:
    async def test_response_carries_provider_and_model(self) -> None:
        provider = MockProvider(_valid_hold_json())
        gateway = LLMGateway({"claude": provider})
        response = await gateway.request_decision("claude", _make_request())
        assert response.provider == "mock"
        assert response.model == "claude-opus-4-7"

    async def test_response_carries_raw_text(self) -> None:
        provider = MockProvider(_valid_hold_json())
        gateway = LLMGateway({"claude": provider})
        response = await gateway.request_decision("claude", _make_request())
        assert "HOLD" in response.raw_text
        assert isinstance(response, LLMResponse)
