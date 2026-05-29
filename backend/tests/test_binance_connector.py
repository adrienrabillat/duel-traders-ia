"""Tests du PaperBinance."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.binance_connector import (
    BinanceMinNotionalError,
    BinanceUnknownSymbolError,
    PaperBinance,
)


@pytest.fixture
def binance() -> PaperBinance:
    b = PaperBinance()
    b.set_price("BTCUSDC", Decimal("100"))
    b.set_price("SOLUSDC", Decimal("50"))
    return b


class TestPaperPrice:
    async def test_get_known_symbol(self, binance: PaperBinance) -> None:
        assert (await binance.get_current_price("BTCUSDC")) == Decimal("100")

    async def test_unknown_symbol_raises(self, binance: PaperBinance) -> None:
        with pytest.raises(BinanceUnknownSymbolError):
            await binance.get_current_price("DOGEUSDC")


class TestOpenOcoBuy:
    async def test_basic_buy(self, binance: PaperBinance) -> None:
        fill, sl_id, tp_id = await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("8"),
            stop_loss_pct=Decimal("3"),
            take_profit_pct=Decimal("5"),
        )
        assert fill.symbol == "BTCUSDC"
        assert fill.side == "BUY"
        assert fill.fill_price == Decimal("100")
        # 8 USDC / 100 = 0.08 BTC
        assert fill.fill_quantity == Decimal("0.08")
        # 8 * 0.001 = 0.008 fee
        assert fill.fee_usdc == Decimal("0.008")
        assert sl_id and tp_id and sl_id != tp_id

    async def test_below_min_raises(self, binance: PaperBinance) -> None:
        with pytest.raises(BinanceMinNotionalError):
            await binance.open_oco_buy(
                "BTCUSDC",
                quote_amount_usdc=Decimal("3"),
                stop_loss_pct=Decimal("3"),
                take_profit_pct=Decimal("5"),
            )

    async def test_unknown_symbol_raises(self, binance: PaperBinance) -> None:
        with pytest.raises(BinanceUnknownSymbolError):
            await binance.open_oco_buy(
                "FAKEUSDC",
                quote_amount_usdc=Decimal("10"),
                stop_loss_pct=Decimal("3"),
                take_profit_pct=Decimal("5"),
            )


class TestOcoTriggers:
    async def test_sl_triggers_when_price_drops(self, binance: PaperBinance) -> None:
        await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("10"),
            stop_loss_pct=Decimal("5"),
            take_profit_pct=Decimal("10"),
        )
        # SL à 95, TP à 110
        binance.set_price("BTCUSDC", Decimal("94"))
        closures = await binance.poll_oco_closures()
        assert len(closures) == 1
        assert closures[0].closed_by == "stop_loss"
        # Quantity bought : 10 / 100 = 0.1 BTC
        assert closures[0].fill_quantity == Decimal("0.1")

    async def test_tp_triggers_when_price_rises(self, binance: PaperBinance) -> None:
        await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("10"),
            stop_loss_pct=Decimal("5"),
            take_profit_pct=Decimal("10"),
        )
        binance.set_price("BTCUSDC", Decimal("115"))
        closures = await binance.poll_oco_closures()
        assert len(closures) == 1
        assert closures[0].closed_by == "take_profit"

    async def test_pending_closures_drained(self, binance: PaperBinance) -> None:
        await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("10"),
            stop_loss_pct=Decimal("5"),
            take_profit_pct=Decimal("10"),
        )
        binance.set_price("BTCUSDC", Decimal("94"))
        first = await binance.poll_oco_closures()
        assert len(first) == 1
        second = await binance.poll_oco_closures()
        assert len(second) == 0

    async def test_no_trigger_inside_band(self, binance: PaperBinance) -> None:
        await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("10"),
            stop_loss_pct=Decimal("5"),
            take_profit_pct=Decimal("10"),
        )
        binance.set_price("BTCUSDC", Decimal("103"))
        closures = await binance.poll_oco_closures()
        assert closures == []


class TestCloseManually:
    async def test_close_returns_fill(self, binance: PaperBinance) -> None:
        await binance.open_oco_buy(
            "BTCUSDC",
            quote_amount_usdc=Decimal("10"),
            stop_loss_pct=Decimal("5"),
            take_profit_pct=Decimal("10"),
        )
        fill = await binance.close_position_market("BTCUSDC", Decimal("0.1"))
        assert fill.side == "SELL"
        assert fill.fill_quantity == Decimal("0.1")
        # OCO doit avoir été annulé
        binance.set_price("BTCUSDC", Decimal("80"))
        assert (await binance.poll_oco_closures()) == []
