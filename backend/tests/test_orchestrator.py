"""Tests d'intégration de l'Orchestrator.

Couvre le cycle complet pour chaque type d'action :
- PLACE_ORDER : crée un OrderRecord en pending_validation.
- UPDATE_STRATEGY : crée une nouvelle Strategy versionnée.
- SEND_MESSAGE : crée un Message (avec respect des canaux du jour).
- QUERY_ORACLE : crée une OracleQuery (sans Tavily réel).
- HOLD : pas d'effet de bord.

Tous les tests utilisent MockProvider pour le LLM et SQLite en mémoire pour la DB.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Experiment, Strategy
from app.models.experiment_models import EpisodeEvent, Message, OracleQuery, OrderRecord
from app.services.episode_manager import EpisodeManager
from app.services.info_clients import CoinSnapshot
from app.services.ledger import Ledger
from app.services.llm_gateway import LLMGateway, MockProvider
from app.services.orchestrator import Orchestrator


# =============================================================================
# Helpers
# =============================================================================


def _make_experiment(db, started_at: datetime, duration: int = 14) -> Experiment:
    exp = Experiment(
        name="Test", started_at=started_at, status="paper", duration_days=duration
    )
    db.add(exp)
    db.flush()
    return exp


def _market_universe() -> list[CoinSnapshot]:
    return [
        CoinSnapshot(
            coin_id="bitcoin",
            symbol="BTC",
            name="Bitcoin",
            current_price_usd=Decimal("100"),
            market_cap_usd=Decimal("1000000"),
            volume_24h_usd=Decimal("50000"),
            price_change_24h_pct=Decimal("2.5"),
            categories=["majors"],
        ),
    ]


# =============================================================================
# PLACE_ORDER
# =============================================================================


class TestPlaceOrderFlow:
    async def test_creates_pending_order(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        started = datetime.now(timezone.utc) - timedelta(hours=2)
        experiment = _make_experiment(db, started)
        episode_mgr = EpisodeManager(experiment)

        provider = MockProvider(json.dumps({
            "action": "PLACE_ORDER",
            "side": "BUY",
            "symbol": "BTCUSDC",
            "quote_amount_usdc": "8.50",
            "stop_loss_pct": "3.5",
            "take_profit_pct": "7.0",
            "reasoning_summary": "Breakout confirmé sur 1h.",
        }))
        gateway = LLMGateway({"anthropic": provider})

        orchestrator = Orchestrator(db, experiment, ledger, gateway, episode_mgr)

        result = await orchestrator.run_candidate_cycle(
            claude,
            provider_name="anthropic",
            market_universe=_market_universe(),
            current_prices={"BTCUSDC": Decimal("100")},
            fear_greed=55,
        )

        assert result.action_kind == "PLACE_ORDER"
        assert result.order_id is not None

        order = db.get(OrderRecord, result.order_id)
        assert order.status == "pending_validation"
        assert order.side == "BUY"
        assert order.symbol == "BTCUSDC"
        assert order.quote_amount_usdc == Decimal("8.50")
        assert order.stop_loss_pct == Decimal("3.5")
        assert order.decision_price == Decimal("100")


# =============================================================================
# UPDATE_STRATEGY
# =============================================================================


class TestUpdateStrategyFlow:
    async def test_creates_strategy_v1(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc))
        provider = MockProvider(json.dumps({
            "action": "UPDATE_STRATEGY",
            "new_strategy": "Stratégie défensive sur les majors uniquement (BTC, ETH). Pas de memecoin.",
            "reasoning_summary": "Premier setup.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))

        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.action_kind == "UPDATE_STRATEGY"
        assert result.strategy_version == 1

    async def test_strategy_versioning(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc))
        # première stratégie
        db.add(Strategy(
            candidate_id=claude.id,
            version=1,
            text="ancienne stratégie défensive sur les majors",
            episode_number=1,
        ))
        db.flush()
        provider = MockProvider(json.dumps({
            "action": "UPDATE_STRATEGY",
            "new_strategy": "Pivot vers une stratégie offensive sur memecoins en bull market.",
            "reasoning_summary": "Changement de régime.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))

        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.strategy_version == 2


# =============================================================================
# SEND_MESSAGE
# =============================================================================


class TestSendMessageFlow:
    async def test_blocked_before_episode_3(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        # Lancement il y a 1 heure → épisode 1
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        experiment = _make_experiment(db, started)

        provider = MockProvider(json.dumps({
            "action": "SEND_MESSAGE",
            "channel": "public",
            "recipients": [],
            "content": "Salut tout le monde.",
            "reasoning_summary": "Test.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))

        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.action_kind == "SEND_MESSAGE"
        # Mais le message n'a pas été persisté (canal fermé)
        assert result.message_id is None

    async def test_allowed_at_episode_3(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        # Lancement il y a 2 jours et demi → épisode 3
        started = datetime.now(timezone.utc) - timedelta(days=2, hours=12)
        experiment = _make_experiment(db, started)

        provider = MockProvider(json.dumps({
            "action": "SEND_MESSAGE",
            "channel": "public",
            "recipients": [],
            "content": "Allez les anges, ça va saigner.",
            "reasoning_summary": "Posture.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))

        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.message_id is not None
        msg = db.get(Message, result.message_id)
        assert msg.channel == "public"
        assert "anges" in msg.content


# =============================================================================
# QUERY_ORACLE
# =============================================================================


class TestQueryOracleFlow:
    async def test_persists_query_without_tavily(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc))
        provider = MockProvider(json.dumps({
            "action": "QUERY_ORACLE",
            "question": "Quelle est l'actualité crypto majeure cette semaine ?",
            "reasoning_summary": "Besoin de contexte macro.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        # tavily=None : on persiste la question sans réponse
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))
        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.oracle_query_id is not None
        q = db.get(OracleQuery, result.oracle_query_id)
        assert "actualité" in q.question
        assert q.error == "No oracle client configured"


# =============================================================================
# HOLD
# =============================================================================


class TestHoldFlow:
    async def test_hold_no_side_effects(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc))
        provider = MockProvider(json.dumps({
            "action": "HOLD",
            "reasoning_summary": "Marché trop incertain.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))
        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.action_kind == "HOLD"
        assert result.order_id is None
        assert result.message_id is None

    async def test_hold_with_next_wake(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc))
        provider = MockProvider(json.dumps({
            "action": "HOLD",
            "next_wake_at_utc": "2026-06-15T18:30:00Z",
            "reasoning_summary": "Attends l'annonce FOMC.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))
        result = await orchestrator.run_candidate_cycle(claude, "anthropic")
        assert result.next_wake_at_utc is not None
        assert result.next_wake_at_utc.year == 2026


# =============================================================================
# Episode events delivery
# =============================================================================


class TestEpisodeEventDelivery:
    async def test_pending_event_marked_delivered(self, db, ledger: Ledger) -> None:
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        experiment = _make_experiment(db, datetime.now(timezone.utc) - timedelta(hours=2))

        # Annonce de la concurrence
        event = EpisodeEvent(
            episode_number=1,
            kind="competition_announce",
            target_candidate_id=None,
            announcement_text="Tu es désormais en compétition avec 4 autres modèles.",
        )
        db.add(event)
        db.flush()
        assert event.delivered_at is None

        provider = MockProvider(json.dumps({
            "action": "HOLD",
            "reasoning_summary": "Réflexion sur la nouvelle.",
        }))
        gateway = LLMGateway({"anthropic": provider})
        orchestrator = Orchestrator(db, experiment, ledger, gateway, EpisodeManager(experiment))
        await orchestrator.run_candidate_cycle(claude, "anthropic")

        db.refresh(event)
        assert event.delivered_at is not None
