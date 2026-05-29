"""Tests du Scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Candidate, Decision, Experiment, OrderRecord
from app.services.episode_manager import EpisodeManager
from app.services.ledger import Ledger
from app.services.scheduler import Scheduler


# =============================================================================
# Helpers
# =============================================================================


def _make_experiment(db, started_at: datetime, duration: int = 14) -> Experiment:
    exp = Experiment(name="Test", started_at=started_at, status="paper", duration_days=duration)
    db.add(exp)
    db.flush()
    return exp


def _add_decision(
    db,
    candidate: Candidate,
    *,
    action: dict | None,
    created_at: datetime,
    episode: int = 1,
) -> Decision:
    decision = Decision(
        candidate_id=candidate.id,
        episode_number=episode,
        system_prompt="x",
        user_prompt="y",
        raw_response="z",
        parsed_action=action,
        provider="mock",
        model="mock",
    )
    decision.created_at = created_at
    db.add(decision)
    db.flush()
    return decision


def _add_order(
    db, candidate: Candidate, *, status: str, side: str = "BUY"
) -> OrderRecord:
    order = OrderRecord(
        candidate_id=candidate.id,
        side=side,
        symbol="BTCUSDC",
        quote_amount_usdc=Decimal("8"),
        stop_loss_pct=Decimal("3") if side == "BUY" else None,
        take_profit_pct=Decimal("5") if side == "BUY" else None,
        status=status,
    )
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def claude(ledger: Ledger) -> Candidate:
    return ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))


# =============================================================================
# Tests
# =============================================================================


class TestFirstRun:
    def test_no_decision_yet_wakes_immediately(self, db, claude: Candidate) -> None:
        experiment = _make_experiment(db, datetime.now(timezone.utc) - timedelta(hours=2))
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude)
        assert wake.should_wake
        assert wake.reason == "first_run"


class TestHourlyDefault:
    def test_within_interval_no_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        # Décision récente (10 minutes), pas de HOLD scheduled
        _add_decision(db, claude, action=None, created_at=now - timedelta(minutes=10))
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "within_interval"

    def test_after_interval_wakes(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=3))
        _add_decision(db, claude, action=None, created_at=now - timedelta(minutes=65))
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert wake.should_wake
        assert wake.reason == "hourly_default"


class TestPreparatoryActions:
    """Actions préparatoires (QUERY_ORACLE, UPDATE_STRATEGY, SEND_MESSAGE)
    déclenchent un wake rapide (60s par défaut) au lieu d'attendre 1h."""

    def test_query_oracle_recent_no_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_decision(
            db, claude,
            action={"action": "QUERY_ORACLE", "query": "BTC trend"},
            created_at=now - timedelta(seconds=30),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "preparing"

    def test_query_oracle_after_60s_wakes(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_decision(
            db, claude,
            action={"action": "QUERY_ORACLE", "query": "BTC trend"},
            created_at=now - timedelta(seconds=90),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert wake.should_wake
        assert wake.reason == "after_query_oracle"

    def test_update_strategy_after_60s_wakes(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_decision(
            db, claude,
            action={"action": "UPDATE_STRATEGY", "strategy": {}},
            created_at=now - timedelta(seconds=90),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert wake.should_wake
        assert wake.reason == "after_update_strategy"


class TestScheduledByModel:
    def test_hold_future_no_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        # Modèle dit "rappelle-moi dans 3 heures"
        future = now + timedelta(hours=3)
        _add_decision(
            db,
            claude,
            action={
                "action": "HOLD",
                "reasoning_summary": "x",
                "next_wake_at_utc": future.isoformat().replace("+00:00", "Z"),
            },
            created_at=now - timedelta(minutes=2),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "waiting_scheduled"

    def test_hold_past_wakes(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        past = now - timedelta(minutes=5)
        _add_decision(
            db,
            claude,
            action={
                "action": "HOLD",
                "reasoning_summary": "x",
                "next_wake_at_utc": past.isoformat().replace("+00:00", "Z"),
            },
            created_at=now - timedelta(minutes=30),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert wake.should_wake
        assert wake.reason == "scheduled_by_model"

    def test_max_sleep_cap_forces_wake_after_24h(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=48), duration=14)
        # Modèle veut dormir 3 jours, mais cap à 24h
        far_future = now + timedelta(days=3)
        # Dernière décision il y a 25h → on a dépassé le cap de 24h
        _add_decision(
            db,
            claude,
            action={
                "action": "HOLD",
                "reasoning_summary": "x",
                "next_wake_at_utc": far_future.isoformat().replace("+00:00", "Z"),
            },
            created_at=now - timedelta(hours=25),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert wake.should_wake
        assert wake.reason == "scheduled_by_model"

    def test_invalid_iso_falls_back_to_hourly(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_decision(
            db,
            claude,
            action={
                "action": "HOLD",
                "reasoning_summary": "x",
                "next_wake_at_utc": "pas une date",
            },
            created_at=now - timedelta(hours=2),
        )
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        # Plus de 1h → wake hourly
        assert wake.should_wake
        assert wake.reason == "hourly_default"


class TestOrderInFlight:
    def test_pending_order_blocks_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_decision(db, claude, action=None, created_at=now - timedelta(hours=3))
        _add_order(db, claude, status="pending_validation")
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "order_in_flight"

    def test_submitted_order_blocks_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_order(db, claude, status="submitted")
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "order_in_flight"


class TestOpenPosition:
    def test_filled_buy_blocks_wake(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        _add_order(db, claude, status="filled", side="BUY")
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "waiting_for_oco_closure"


class TestExperimentFinished:
    def test_after_duration_never_wakes(self, db, claude: Candidate) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(days=20))
        scheduler = Scheduler(db, EpisodeManager(experiment))
        wake = scheduler.should_wake(claude, now)
        assert not wake.should_wake
        assert wake.reason == "experiment_finished"


class TestBatch:
    def test_ready_to_wake_lists_eligibles(
        self, db, ledger: Ledger
    ) -> None:
        now = datetime.now(timezone.utc)
        experiment = _make_experiment(db, now - timedelta(hours=2))
        # 2 candidats actifs : Claude (jamais joué) + Mistral (qui vient de jouer)
        claude = ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))
        mistral = ledger.create_candidate("Mistral", "mistral-large-3", Decimal("10"))
        _add_decision(db, mistral, action=None, created_at=now - timedelta(minutes=5))
        scheduler = Scheduler(db, EpisodeManager(experiment))
        ready = scheduler.candidates_ready_to_wake(now)
        names = {c.name for c, _ in ready}
        assert "Claude" in names
        assert "Mistral" not in names
