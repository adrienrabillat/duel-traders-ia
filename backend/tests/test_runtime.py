"""Tests de la PaperTradingLoop (un tick complet avec mocks)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Decision, Experiment, OrderRecord
from app.services.binance_connector import PaperBinance
from app.services.info_clients import CoinSnapshot
from app.services.ledger import Ledger
from app.services.llm_gateway import LLMGateway, MockProvider
from app.services.runtime import PaperTradingLoop, static_market_provider


def _make_engine_and_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine, factory


class TestTickCycle:
    async def test_full_tick_runs_pending_candidate(self) -> None:
        _, factory = _make_engine_and_factory()
        with factory() as db:
            exp = Experiment(
                name="t",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                status="paper",
                duration_days=14,
            )
            db.add(exp)
            db.flush()
            ledger = Ledger(db)
            ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
            db.commit()
            exp_id = exp.id
            with factory() as db2:
                experiment_for_loop = db2.get(Experiment, exp_id)

        binance = PaperBinance()
        binance.set_price("BTCUSDC", Decimal("100"))

        mock = MockProvider(
            json.dumps({
                "action": "HOLD",
                "reasoning_summary": "Marché incertain, j'attends.",
            })
        )
        gateway = LLMGateway({"anthropic": mock})
        universe = [
            CoinSnapshot(
                coin_id="bitcoin",
                symbol="BTC",
                name="Bitcoin",
                current_price_usd=Decimal("100"),
                market_cap_usd=Decimal("1000"),
                volume_24h_usd=Decimal("10"),
                price_change_24h_pct=Decimal("0"),
                categories=[],
            )
        ]
        loop = PaperTradingLoop(
            session_factory=factory,
            experiment=experiment_for_loop,
            binance=binance,
            gateway=gateway,
            provider_map={"Claude": "anthropic"},
            market_provider=static_market_provider(
                universe, {"BTCUSDC": Decimal("100")}, fear_greed=55
            ),
            tick_seconds=1,
        )
        result = await loop.tick()
        assert "Claude" in result.cycles_run
        # Décision persistée
        with factory() as db:
            decisions = db.query(Decision).all()
            assert len(decisions) == 1
            assert decisions[0].parsed_action["action"] == "HOLD"

    async def test_tick_auto_approves_and_executes(self) -> None:
        _, factory = _make_engine_and_factory()
        with factory() as db:
            exp = Experiment(
                name="t",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                status="paper",
                duration_days=14,
            )
            db.add(exp)
            db.flush()
            ledger = Ledger(db)
            claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
            # Ordre déjà vieux : doit être auto-approuvé
            order = OrderRecord(
                candidate_id=claude.id,
                side="BUY",
                symbol="BTCUSDC",
                quote_amount_usdc=Decimal("8"),
                stop_loss_pct=Decimal("3"),
                take_profit_pct=Decimal("5"),
                status="pending_validation",
            )
            order.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            db.add(order)
            db.commit()
            order_id = order.id
            exp_id = exp.id
            experiment_for_loop = db.get(Experiment, exp_id)

        binance = PaperBinance()
        binance.set_price("BTCUSDC", Decimal("100"))
        gateway = LLMGateway({
            "anthropic": MockProvider(
                '{"action": "HOLD", "reasoning_summary": "Position en cours."}'
            )
        })

        loop = PaperTradingLoop(
            session_factory=factory,
            experiment=experiment_for_loop,
            binance=binance,
            gateway=gateway,
            provider_map={"Claude": "anthropic"},
            market_provider=static_market_provider(
                [], {"BTCUSDC": Decimal("100")}, 50
            ),
            tick_seconds=1,
        )
        result = await loop.tick()
        assert result.auto_approved_count == 1
        assert result.executed_count == 1
        with factory() as db:
            o = db.get(OrderRecord, order_id)
            assert o.status == "filled"
            assert o.fill_quantity == Decimal("0.08")

    async def test_tick_skips_when_no_provider_configured(self) -> None:
        _, factory = _make_engine_and_factory()
        with factory() as db:
            exp = Experiment(
                name="t",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
                status="paper",
                duration_days=14,
            )
            db.add(exp)
            db.flush()
            ledger = Ledger(db)
            ledger.create_candidate("UnknownGuy", "??", Decimal("10"))
            db.commit()
            exp_id = exp.id
            experiment_for_loop = db.get(Experiment, exp_id)

        binance = PaperBinance()
        gateway = LLMGateway({"anthropic": MockProvider('{"action":"HOLD","reasoning_summary":"x"}')})
        loop = PaperTradingLoop(
            session_factory=factory,
            experiment=experiment_for_loop,
            binance=binance,
            gateway=gateway,
            provider_map={},  # aucun provider configuré
            tick_seconds=1,
        )
        result = await loop.tick()
        assert result.cycles_run == []

    async def test_tick_stops_when_experiment_finished(self) -> None:
        _, factory = _make_engine_and_factory()
        with factory() as db:
            exp = Experiment(
                name="t",
                started_at=datetime.now(timezone.utc) - timedelta(days=15),
                status="paper",
                duration_days=14,
            )
            db.add(exp)
            db.commit()
            exp_id = exp.id
            experiment_for_loop = db.get(Experiment, exp_id)

        gateway = LLMGateway({"anthropic": MockProvider('{"action":"HOLD","reasoning_summary":"x"}')})
        loop = PaperTradingLoop(
            session_factory=factory,
            experiment=experiment_for_loop,
            binance=PaperBinance(),
            gateway=gateway,
            provider_map={},
            tick_seconds=1,
        )
        await loop.tick()
        assert loop._stopped.is_set()
