"""Tests de l'OrderExecutor.

Couvre :
- Cycle complet d'un BUY : pending → approved → submitted → filled, ledger sync.
- Auto-approve après timeout.
- Reject manuel.
- OCO closures (SL et TP) → ledger sync + statut.
- Erreur Binance → statut failed.
- Transitions interdites (double approve, approve d'un rejected, etc.).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import Candidate, OrderRecord
from app.services.binance_connector import (
    BinanceMinNotionalError,
    PaperBinance,
)
from app.services.ledger import Ledger
from app.services.order_executor import (
    OrderExecutor,
    OrderNotFoundError,
    OrderTransitionError,
)


# =============================================================================
# Helpers
# =============================================================================


def _create_order(
    db,
    candidate_id: int,
    *,
    side: str = "BUY",
    symbol: str = "BTCUSDC",
    amount: Decimal = Decimal("8"),
    sl: Decimal | None = Decimal("3"),
    tp: Decimal | None = Decimal("5"),
    status: str = "pending_validation",
    fill_quantity: Decimal | None = None,
    created_at: datetime | None = None,
) -> OrderRecord:
    order = OrderRecord(
        candidate_id=candidate_id,
        side=side,
        symbol=symbol,
        quote_amount_usdc=amount,
        stop_loss_pct=sl,
        take_profit_pct=tp,
        status=status,
        fill_quantity=fill_quantity,
    )
    if created_at is not None:
        order.created_at = created_at
    db.add(order)
    db.flush()
    return order


@pytest.fixture
def claude(ledger: Ledger) -> Candidate:
    return ledger.create_candidate("Claude", "claude-opus-4-7", Decimal("10"))


@pytest.fixture
def binance() -> PaperBinance:
    b = PaperBinance()
    b.set_price("BTCUSDC", Decimal("100"))
    return b


@pytest.fixture
def executor(db, ledger: Ledger, binance: PaperBinance) -> OrderExecutor:
    return OrderExecutor(db, ledger, binance)


# =============================================================================
# Validation manuelle
# =============================================================================


class TestApproveReject:
    def test_approve_transitions_status(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        order = _create_order(db, claude.id)
        approved = executor.approve(order.id)
        assert approved.status == "approved"
        assert approved.validated_at is not None

    def test_reject_transitions_status(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        order = _create_order(db, claude.id)
        rejected = executor.reject(order.id, reason="trop risqué")
        assert rejected.status == "rejected"
        assert rejected.error_message == "trop risqué"

    def test_cannot_approve_already_approved(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        order = _create_order(db, claude.id)
        executor.approve(order.id)
        with pytest.raises(OrderTransitionError):
            executor.approve(order.id)

    def test_cannot_approve_rejected(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        order = _create_order(db, claude.id)
        executor.reject(order.id)
        with pytest.raises(OrderTransitionError):
            executor.approve(order.id)

    def test_approve_unknown_raises(self, executor: OrderExecutor) -> None:
        with pytest.raises(OrderNotFoundError):
            executor.approve(99999)


class TestAutoApprove:
    def test_auto_approves_old_pending(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        old = _create_order(
            db, claude.id, created_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        fresh = _create_order(db, claude.id)
        stale = executor.auto_approve_stale()
        assert old in stale and fresh not in stale
        db.refresh(old)
        db.refresh(fresh)
        assert old.status == "auto_approved"
        assert fresh.status == "pending_validation"

    def test_auto_approve_respects_custom_timeout(
        self, db, ledger: Ledger, binance: PaperBinance, claude: Candidate
    ) -> None:
        executor = OrderExecutor(db, ledger, binance, auto_approve_timeout_seconds=10)
        old = _create_order(
            db, claude.id, created_at=datetime.now(timezone.utc) - timedelta(seconds=30)
        )
        stale = executor.auto_approve_stale()
        assert old in stale


# =============================================================================
# Cycle complet BUY
# =============================================================================


class TestBuyExecution:
    async def test_full_cycle_buy(
        self,
        db,
        claude: Candidate,
        executor: OrderExecutor,
        binance: PaperBinance,
        ledger: Ledger,
    ) -> None:
        order = _create_order(db, claude.id, amount=Decimal("8"))
        executor.approve(order.id)
        await executor.execute_pending()
        db.refresh(order)
        assert order.status == "filled"
        assert order.fill_price == Decimal("100")
        # 8 / 100 = 0.08 BTC
        assert order.fill_quantity == Decimal("0.08")
        # Ledger : 10 - 8 - 0.008 fee = 1.992 USDC
        wallet = ledger.get_wallet(claude.id)
        assert wallet.usdc_balance == Decimal("1.992")
        position = ledger.get_position(claude.id, "BTCUSDC")
        assert position is not None
        assert position.quantity == Decimal("0.08")

    async def test_failed_when_below_minimum(
        self, db, claude: Candidate, executor: OrderExecutor, binance: PaperBinance
    ) -> None:
        order = _create_order(db, claude.id, amount=Decimal("3"))  # < 5 USDC min
        executor.approve(order.id)
        executed = await executor.execute_pending()
        db.refresh(order)
        # Ordre marqué failed, NON présent dans la liste des succès
        assert order.status == "failed"
        assert order.error_message
        assert executed == []


# =============================================================================
# OCO closures
# =============================================================================


class TestOcoClosure:
    async def test_sl_closes_position_and_updates_ledger(
        self,
        db,
        claude: Candidate,
        executor: OrderExecutor,
        binance: PaperBinance,
        ledger: Ledger,
    ) -> None:
        order = _create_order(db, claude.id, amount=Decimal("8"), sl=Decimal("5"), tp=Decimal("10"))
        executor.approve(order.id)
        await executor.execute_pending()
        # Le prix décroche, SL = 95 doit déclencher
        binance.set_price("BTCUSDC", Decimal("94"))
        closures = await executor.process_oco_closures()
        assert len(closures) == 1
        db.refresh(order)
        assert order.status == "closed_by_sl"
        # Ledger : position effacée, USDC remontée (légèrement plus bas qu'à l'origine)
        assert ledger.get_position(claude.id, "BTCUSDC") is None
        wallet = ledger.get_wallet(claude.id)
        # Pas de perte gigantesque : on garde > 9 USDC (entrée 8, sortie ~7.52)
        assert wallet.usdc_balance > Decimal("9")
        assert wallet.usdc_balance < Decimal("10")

    async def test_tp_closes_with_profit(
        self,
        db,
        claude: Candidate,
        executor: OrderExecutor,
        binance: PaperBinance,
        ledger: Ledger,
    ) -> None:
        order = _create_order(db, claude.id, amount=Decimal("8"), sl=Decimal("5"), tp=Decimal("10"))
        executor.approve(order.id)
        await executor.execute_pending()
        # TP = 110
        binance.set_price("BTCUSDC", Decimal("115"))
        closures = await executor.process_oco_closures()
        assert len(closures) == 1
        db.refresh(order)
        assert order.status == "closed_by_tp"
        # Profit : ~+0.79 USDC sur le notional de 8
        wallet = ledger.get_wallet(claude.id)
        assert wallet.usdc_balance > Decimal("10")


# =============================================================================
# Pending listing
# =============================================================================


class TestPendingOrders:
    def test_pending_orders_lists_them(self, db, claude: Candidate, executor: OrderExecutor) -> None:
        a = _create_order(db, claude.id)
        b = _create_order(db, claude.id)
        executor.approve(b.id)
        pending = executor.pending_orders()
        assert a in pending and b not in pending
