"""Tests du module llm_pricing."""

from __future__ import annotations

from decimal import Decimal

from app.services.llm_pricing import (
    DEFAULT_PRICE,
    PRICING,
    cost_usd,
    get_price,
)


class TestGetPrice:
    def test_known_model(self) -> None:
        p = get_price("claude-haiku-4-5-20251001")
        assert p.input_per_million_usd == Decimal("1.00")
        assert p.output_per_million_usd == Decimal("5.00")

    def test_unknown_model_returns_default(self) -> None:
        p = get_price("unknown-model-42")
        assert p == DEFAULT_PRICE

    def test_mock_is_free(self) -> None:
        p = get_price("mock")
        assert p.input_per_million_usd == Decimal("0")
        assert p.output_per_million_usd == Decimal("0")


class TestCostUsd:
    def test_basic_calc(self) -> None:
        # Haiku 4.5 : $1/M in + $5/M out
        # 1000 in + 500 out = 0.001 + 0.0025 = 0.0035
        assert cost_usd("claude-haiku-4-5-20251001", 1000, 500) == Decimal("0.0035000")

    def test_opus_expensive(self) -> None:
        # Opus 4.7 : $15/M + $75/M
        # 10000 + 5000 = 0.15 + 0.375 = 0.525
        assert cost_usd("claude-opus-4-7", 10000, 5000) == Decimal("0.5250000")

    def test_mock_is_zero(self) -> None:
        assert cost_usd("mock", 99999, 99999) == Decimal("0E-7")

    def test_zero_tokens(self) -> None:
        assert cost_usd("claude-haiku-4-5-20251001", 0, 0) == Decimal("0E-7")

    def test_negative_returns_zero(self) -> None:
        assert cost_usd("claude-haiku-4-5-20251001", -5, 10) == Decimal("0")
        assert cost_usd("claude-haiku-4-5-20251001", 5, -10) == Decimal("0")

    def test_unknown_model_uses_default(self) -> None:
        # Default = $5/M + $15/M
        # 1000 + 1000 = 0.005 + 0.015 = 0.02
        assert cost_usd("totally-fake-model", 1000, 1000) == Decimal("0.0200000")

    def test_all_known_models_have_decimal_prices(self) -> None:
        for model, price in PRICING.items():
            assert isinstance(price.input_per_million_usd, Decimal), model
            assert isinstance(price.output_per_million_usd, Decimal), model
            assert price.input_per_million_usd >= 0, model
            assert price.output_per_million_usd >= 0, model
