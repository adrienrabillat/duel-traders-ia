"""Tests du schéma Pydantic des actions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.llm_gateway.actions import (
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


# =============================================================================
# PLACE_ORDER
# =============================================================================


class TestPlaceOrder:
    def _valid(self) -> dict:
        return {
            "action": "PLACE_ORDER",
            "side": "BUY",
            "symbol": "BTCUSDC",
            "quote_amount_usdc": "8.50",
            "stop_loss_pct": "3.5",
            "take_profit_pct": "7",
            "reasoning_summary": "Setup haussier confirmé sur 1h.",
        }

    def test_valid_buy(self) -> None:
        action = parse_action(self._valid())
        assert isinstance(action, PlaceOrderAction)
        assert action.action == ActionKind.PLACE_ORDER
        assert action.side == "BUY"
        assert action.symbol == "BTCUSDC"
        assert action.quote_amount_usdc == Decimal("8.50")
        assert action.stop_loss_pct == Decimal("3.5")
        assert action.take_profit_pct == Decimal("7")

    def test_symbol_must_end_with_usdc(self) -> None:
        bad = self._valid() | {"symbol": "BTCUSDT"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_quote_amount_must_be_positive(self) -> None:
        bad = self._valid() | {"quote_amount_usdc": "0"}
        with pytest.raises(ValidationError):
            parse_action(bad)
        bad = self._valid() | {"quote_amount_usdc": "-5"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_stop_loss_bounds(self) -> None:
        # > 0
        bad = self._valid() | {"stop_loss_pct": "0"}
        with pytest.raises(ValidationError):
            parse_action(bad)
        # <= 99
        bad = self._valid() | {"stop_loss_pct": "100"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_take_profit_positive(self) -> None:
        bad = self._valid() | {"take_profit_pct": "-1"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_invalid_side(self) -> None:
        bad = self._valid() | {"side": "LONG"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_reasoning_required(self) -> None:
        bad = self._valid()
        del bad["reasoning_summary"]
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_reasoning_max_length(self) -> None:
        bad = self._valid() | {"reasoning_summary": "x" * 501}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_unknown_field_rejected(self) -> None:
        bad = self._valid() | {"unknown_field": "x"}
        with pytest.raises(ValidationError):
            parse_action(bad)

    def test_accepts_numeric_decimals(self) -> None:
        # JSON peut renvoyer un float ; Pydantic doit gérer.
        payload = self._valid() | {"quote_amount_usdc": 8.5}
        action = parse_action(payload)
        assert isinstance(action, PlaceOrderAction)


# =============================================================================
# Autres actions
# =============================================================================


class TestClosePosition:
    def test_valid(self) -> None:
        action = parse_action({
            "action": "CLOSE_POSITION",
            "symbol": "SOLUSDC",
            "reasoning_summary": "Sortie anticipée.",
        })
        assert isinstance(action, ClosePositionAction)
        assert action.symbol == "SOLUSDC"


class TestUpdateStrategy:
    def test_valid(self) -> None:
        action = parse_action({
            "action": "UPDATE_STRATEGY",
            "category": "MAJORS",
            "new_strategy": "Switch vers une stratégie défensive sur majors.",
            "reasoning_summary": "Marché trop volatil.",
        })
        assert isinstance(action, UpdateStrategyAction)
        assert action.category.value == "MAJORS"

    def test_strategy_too_short(self) -> None:
        with pytest.raises(ValidationError):
            parse_action({
                "action": "UPDATE_STRATEGY",
                "category": "MAJORS",
                "new_strategy": "court",
                "reasoning_summary": "x",
            })


class TestQueryOracle:
    def test_valid(self) -> None:
        action = parse_action({
            "action": "QUERY_ORACLE",
            "question": "Quel est le sentiment du marché sur SOL cette semaine ?",
            "reasoning_summary": "Besoin d'info externe.",
        })
        assert isinstance(action, QueryOracleAction)

    def test_question_too_short(self) -> None:
        with pytest.raises(ValidationError):
            parse_action({
                "action": "QUERY_ORACLE",
                "question": "court",
                "reasoning_summary": "x",
            })


class TestSendMessage:
    def test_public_message(self) -> None:
        action = parse_action({
            "action": "SEND_MESSAGE",
            "channel": "public",
            "recipients": [],
            "content": "Hello everyone",
            "reasoning_summary": "Test diplomatie.",
        })
        assert isinstance(action, SendMessageAction)
        assert action.channel == "public"
        assert action.recipients == []

    def test_private_message(self) -> None:
        action = parse_action({
            "action": "SEND_MESSAGE",
            "channel": "private",
            "recipients": ["Mistral"],
            "content": "Je te propose une alliance.",
            "reasoning_summary": "Tentative d'alliance.",
        })
        assert isinstance(action, SendMessageAction)
        assert action.recipients == ["Mistral"]

    def test_invalid_channel(self) -> None:
        with pytest.raises(ValidationError):
            parse_action({
                "action": "SEND_MESSAGE",
                "channel": "broadcast",
                "recipients": [],
                "content": "x",
                "reasoning_summary": "x",
            })


class TestHold:
    def test_valid_without_next_wake(self) -> None:
        action = parse_action({
            "action": "HOLD",
            "reasoning_summary": "Marché incertain, j'attends.",
        })
        assert isinstance(action, HoldAction)
        assert action.next_wake_at_utc is None

    def test_with_next_wake(self) -> None:
        action = parse_action({
            "action": "HOLD",
            "next_wake_at_utc": "2026-05-30T14:30:00Z",
            "reasoning_summary": "Attendre l'annonce FOMC.",
        })
        assert action.next_wake_at_utc == "2026-05-30T14:30:00Z"


# =============================================================================
# Dispatch et JSON string
# =============================================================================


class TestDispatch:
    def test_parses_from_json_string(self) -> None:
        action = parse_action('{"action": "HOLD", "reasoning_summary": "test"}')
        assert isinstance(action, HoldAction)

    def test_missing_action_kind(self) -> None:
        with pytest.raises(ValidationError):
            parse_action({"reasoning_summary": "x"})

    def test_unknown_action_kind(self) -> None:
        with pytest.raises(ValidationError):
            parse_action({"action": "DANCE", "reasoning_summary": "x"})


class TestSchemaDescription:
    def test_schema_string_mentions_all_actions(self) -> None:
        desc = action_schema_for_prompt()
        for kind in ActionKind:
            assert kind.value in desc

    def test_schema_string_under_2k_tokens(self) -> None:
        # Rough heuristic : 4 chars/token. Le schéma doit rester compact.
        assert len(action_schema_for_prompt()) < 8000
