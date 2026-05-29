"""Order Executor : pilote la state machine des OrderRecord.

État cible attendu pour un cycle BUY classique :
    pending_validation (créé par l'Orchestrator)
        → approved (humain via API admin)
            ou auto_approved (timer 2 min)
            ou rejected (humain)
        → submitted (envoyé à Binance)
        → filled (achat exécuté, OCO actif)
            → closed_by_sl ou closed_by_tp (poll_oco_closures)

Pour un SELL manuel (CloseAction) :
    pending_validation → approved/auto → submitted → closed_manually

Pour une erreur Binance : → failed (avec error_message).

Le Ledger virtuel est mis à jour en même temps que l'OrderRecord, dans la
même transaction. L'appelant commit après chaque execute_one (gère
l'atomicité côté DB).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OrderRecord
from app.services.binance_connector import BinanceConnector
from app.services.ledger import Ledger

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class OrderExecutorError(Exception):
    pass


class OrderTransitionError(OrderExecutorError):
    """Tentative de transition de statut non autorisée."""


class OrderNotFoundError(OrderExecutorError):
    pass


# =============================================================================
# Service
# =============================================================================


class OrderExecutor:
    """Service d'exécution et de cycle de vie des ordres."""

    AUTO_APPROVE_TIMEOUT_SECONDS = 120  # 2 minutes

    def __init__(
        self,
        db: Session,
        ledger: Ledger,
        binance: BinanceConnector,
        auto_approve_timeout_seconds: int = AUTO_APPROVE_TIMEOUT_SECONDS,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.binance = binance
        self.auto_approve_timeout_seconds = auto_approve_timeout_seconds

    # ------------------------------------------------------------------ Lookup

    def _get_order(self, order_id: int) -> OrderRecord:
        order = self.db.get(OrderRecord, order_id)
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return order

    # =========================================================================
    # Validation manuelle
    # =========================================================================

    def approve(self, order_id: int) -> OrderRecord:
        order = self._get_order(order_id)
        if order.status != "pending_validation":
            raise OrderTransitionError(
                f"Cannot approve order {order_id}: status is {order.status}"
            )
        order.status = "approved"
        order.validated_at = datetime.now(timezone.utc)
        self.db.flush()
        return order

    def reject(self, order_id: int, reason: str = "") -> OrderRecord:
        order = self._get_order(order_id)
        if order.status != "pending_validation":
            raise OrderTransitionError(
                f"Cannot reject order {order_id}: status is {order.status}"
            )
        order.status = "rejected"
        order.validated_at = datetime.now(timezone.utc)
        order.error_message = reason or None
        self.db.flush()
        return order

    def auto_approve_stale(self, *, now: datetime | None = None) -> list[OrderRecord]:
        """Auto-approuve les ordres en pending_validation depuis > timeout."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.auto_approve_timeout_seconds)
        stmt = select(OrderRecord).where(
            OrderRecord.status == "pending_validation",
            OrderRecord.created_at <= cutoff,
        )
        # SQLite returns naive datetimes ; on compare via la DB pour rester cohérents
        stale = list(self.db.execute(stmt).scalars())
        for order in stale:
            order.status = "auto_approved"
            order.validated_at = now
        if stale:
            self.db.flush()
        return stale

    # =========================================================================
    # Exécution sur Binance
    # =========================================================================

    async def execute_pending(self) -> list[OrderRecord]:
        """Exécute tous les ordres en statut approved / auto_approved."""
        stmt = select(OrderRecord).where(
            OrderRecord.status.in_(["approved", "auto_approved"])
        )
        ready = list(self.db.execute(stmt).scalars())
        executed: list[OrderRecord] = []
        for order in ready:
            try:
                await self._execute_one(order)
                executed.append(order)
            except Exception as e:
                logger.exception("Order %d execution failed", order.id)
                order.status = "failed"
                order.error_message = str(e)
                self.db.flush()
        return executed

    async def _execute_one(self, order: OrderRecord) -> None:
        order.client_order_id = order.client_order_id or f"ang-{uuid.uuid4().hex[:12]}"
        order.submitted_at = datetime.now(timezone.utc)
        order.status = "submitted"
        self.db.flush()

        if order.side == "BUY":
            await self._execute_buy(order)
        elif order.side == "SELL":
            await self._execute_sell(order)
        else:  # pragma: no cover (contrainte DB déjà active)
            raise OrderExecutorError(f"Unknown side: {order.side}")

    async def _execute_buy(self, order: OrderRecord) -> None:
        if order.stop_loss_pct is None or order.take_profit_pct is None:
            raise OrderExecutorError(
                f"Order {order.id} BUY without SL/TP: invalid for OCO"
            )

        fill, sl_id, tp_id = await self.binance.open_oco_buy(
            order.symbol,
            order.quote_amount_usdc,
            order.stop_loss_pct,
            order.take_profit_pct,
            client_order_id=order.client_order_id,
        )
        order.binance_order_id = fill.binance_order_id
        order.fill_price = fill.fill_price
        order.fill_quantity = fill.fill_quantity
        order.fee_usdc = fill.fee_usdc
        order.filled_at = fill.filled_at
        order.status = "filled"

        # Sync ledger
        self.ledger.record_buy(
            candidate_id=order.candidate_id,
            symbol=order.symbol,
            quantity=fill.fill_quantity,
            price=fill.fill_price,
        )
        self.db.flush()

    async def _execute_sell(self, order: OrderRecord) -> None:
        if order.fill_quantity is None or order.fill_quantity <= Decimal("0"):
            # Repli : récupérer la quantité depuis la position courante
            position = self.ledger.get_position(order.candidate_id, order.symbol)
            if position is None:
                raise OrderExecutorError(
                    f"Order {order.id} SELL but no position on {order.symbol}"
                )
            order.fill_quantity = position.quantity

        fill = await self.binance.close_position_market(
            order.symbol,
            order.fill_quantity,
            client_order_id=order.client_order_id,
        )
        order.binance_order_id = fill.binance_order_id
        order.fill_price = fill.fill_price
        order.fee_usdc = fill.fee_usdc
        order.filled_at = fill.filled_at
        order.status = "closed_manually"

        self.ledger.record_sell(
            candidate_id=order.candidate_id,
            symbol=order.symbol,
            quantity=fill.fill_quantity,
            price=fill.fill_price,
        )
        self.db.flush()

    # =========================================================================
    # OCO closures (SL/TP déclenchés côté Binance)
    # =========================================================================

    async def process_oco_closures(self) -> list[OrderRecord]:
        """Drain les OCOs déclenchés depuis le dernier poll, sync OrderRecord + Ledger."""
        closures = await self.binance.poll_oco_closures()
        updated: list[OrderRecord] = []
        for closure in closures:
            order = self.db.execute(
                select(OrderRecord).where(
                    OrderRecord.client_order_id == closure.client_order_id
                )
            ).scalar_one_or_none()
            if order is None:
                logger.warning(
                    "OCO closure for unknown client_order_id=%s", closure.client_order_id
                )
                continue
            if order.status != "filled":
                logger.warning(
                    "OCO closure for order %d in unexpected status %s",
                    order.id,
                    order.status,
                )
                continue

            order.status = (
                "closed_by_sl" if closure.closed_by == "stop_loss" else "closed_by_tp"
            )
            order.closed_at = closure.closed_at

            self.ledger.record_sell(
                candidate_id=order.candidate_id,
                symbol=closure.symbol,
                quantity=closure.fill_quantity,
                price=closure.fill_price,
            )
            updated.append(order)
        if updated:
            self.db.flush()
        return updated

    # =========================================================================
    # Helpers d'inspection
    # =========================================================================

    def pending_orders(self) -> Sequence[OrderRecord]:
        return list(
            self.db.execute(
                select(OrderRecord).where(OrderRecord.status == "pending_validation")
            ).scalars()
        )

    def open_position_orders(self, candidate_id: int) -> Sequence[OrderRecord]:
        """Renvoie les OrderRecord BUY en statut 'filled' (OCO encore actif)."""
        return list(
            self.db.execute(
                select(OrderRecord).where(
                    OrderRecord.candidate_id == candidate_id,
                    OrderRecord.status == "filled",
                    OrderRecord.side == "BUY",
                )
            ).scalars()
        )
