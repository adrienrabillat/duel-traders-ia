"""Routes admin (auth Bearer requise si ADMIN_API_TOKEN configuré).

Endpoints réservés à l'équipe humaine qui pilote l'expérience :
- gestion de l'expérience (create / stop)
- gestion des candidats
- validation manuelle des ordres
- kill switch et liquidation d'urgence
- injection d'EpisodeEvent (annonces, Twists)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.api.schemas import (
    CandidateOut,
    CreateCandidateIn,
    CreateEpisodeEventIn,
    CreateExperimentIn,
    ExperimentOut,
    KillSwitchResult,
    OperationOk,
    OrderOut,
    PositionOut,
    RejectOrderIn,
    WalletOut,
)
from app.deps import AdminAuthDep, DbDep
from app.models import (
    Candidate,
    EpisodeEvent,
    Experiment,
    OrderRecord,
)
from app.services.episode_manager import EpisodeManager
from app.services.ledger import Ledger
from app.services.order_executor import (
    OrderExecutor,
    OrderNotFoundError,
    OrderTransitionError,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminAuthDep])


# =============================================================================
# Helpers
# =============================================================================


def _wallet_out(candidate: Candidate) -> WalletOut:
    return WalletOut(
        usdc_balance=candidate.wallet.usdc_balance,
        initial_balance=candidate.wallet.initial_balance,
        positions=[
            PositionOut(
                symbol=p.symbol, quantity=p.quantity, avg_cost_usdc=p.avg_cost_usdc
            )
            for p in candidate.wallet.positions
        ],
    )


# =============================================================================
# Experiment management
# =============================================================================


@router.post("/experiment", response_model=ExperimentOut)
def create_experiment(payload: CreateExperimentIn, db: Session = DbDep) -> ExperimentOut:
    existing = db.execute(
        select(Experiment).where(Experiment.status.in_(["paper", "live"]))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"Active experiment already exists: id={existing.id}")
    exp = Experiment(
        name=payload.name,
        started_at=datetime.now(timezone.utc),
        status="paper",
        duration_days=payload.duration_days,
    )
    db.add(exp)
    db.commit()
    em = EpisodeManager(exp)
    return ExperimentOut(
        id=exp.id,
        name=exp.name,
        started_at=exp.started_at,
        ended_at=exp.ended_at,
        status=exp.status,
        duration_days=exp.duration_days,
        current_episode=em.current_episode(datetime.now(timezone.utc)),
    )


@router.post("/experiment/{experiment_id}/stop", response_model=OperationOk)
def stop_experiment(experiment_id: int, db: Session = DbDep) -> OperationOk:
    exp = db.get(Experiment, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    exp.status = "aborted"
    exp.ended_at = datetime.now(timezone.utc)
    db.commit()
    return OperationOk(message=f"Experiment {experiment_id} aborted")


# =============================================================================
# Candidates management
# =============================================================================


@router.post("/candidates", response_model=CandidateOut)
def create_candidate(payload: CreateCandidateIn, db: Session = DbDep) -> CandidateOut:
    ledger = Ledger(db)
    candidate = ledger.create_candidate(
        name=payload.name,
        model=payload.model,
        initial_balance_usdc=payload.initial_balance_usdc,
        kind=payload.kind,
    )
    db.commit()
    db.refresh(candidate)
    return CandidateOut(
        id=candidate.id,
        name=candidate.name,
        model=candidate.model,
        kind=candidate.kind,
        is_active=candidate.is_active,
        wallet=_wallet_out(candidate),
    )


# =============================================================================
# Orders : validation manuelle
# =============================================================================


@router.get("/orders/pending", response_model=list[OrderOut])
def list_pending_orders(db: Session = DbDep) -> list[OrderOut]:
    executor = OrderExecutor(db, Ledger(db), binance=_NullBinance())  # noqa: ignore (lookup only)
    pending = executor.pending_orders()
    return [OrderOut.model_validate(o) for o in pending]


@router.post("/orders/{order_id}/approve", response_model=OrderOut)
def approve_order(order_id: int, db: Session = DbDep) -> OrderOut:
    executor = OrderExecutor(db, Ledger(db), binance=_NullBinance())
    try:
        order = executor.approve(order_id)
    except OrderNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except OrderTransitionError as e:
        raise HTTPException(409, str(e)) from e
    db.commit()
    return OrderOut.model_validate(order)


@router.post("/orders/{order_id}/reject", response_model=OrderOut)
def reject_order(
    order_id: int, payload: RejectOrderIn, db: Session = DbDep
) -> OrderOut:
    executor = OrderExecutor(db, Ledger(db), binance=_NullBinance())
    try:
        order = executor.reject(order_id, reason=payload.reason)
    except OrderNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except OrderTransitionError as e:
        raise HTTPException(409, str(e)) from e
    db.commit()
    return OrderOut.model_validate(order)


# =============================================================================
# Kill switch
# =============================================================================


@router.post("/kill-switch", response_model=KillSwitchResult)
def kill_switch(db: Session = DbDep) -> KillSwitchResult:
    """Annule TOUS les ordres en attente de validation et stoppe l'expérience.

    Note : cet endpoint marque les ordres en `rejected` avec la raison
    'kill_switch'. Pour vendre les positions ouvertes en USDC,
    voir POST /admin/liquidate-all.
    """
    pending = list(
        db.execute(
            select(OrderRecord).where(
                OrderRecord.status.in_(["pending_validation", "approved", "auto_approved"])
            )
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    for order in pending:
        order.status = "rejected"
        order.validated_at = now
        order.error_message = "kill_switch"
    # Stoppe l'expérience active
    exp = db.execute(
        select(Experiment).where(Experiment.status.in_(["paper", "live"]))
    ).scalar_one_or_none()
    stopped = False
    if exp is not None:
        exp.status = "aborted"
        exp.ended_at = now
        stopped = True
    db.commit()
    return KillSwitchResult(stopped=stopped, pending_cancelled=len(pending))


# =============================================================================
# Episode events (annonces, Twists)
# =============================================================================


@router.post("/episode-events", response_model=OperationOk)
def inject_episode_event(
    payload: CreateEpisodeEventIn, db: Session = DbDep
) -> OperationOk:
    event = EpisodeEvent(
        episode_number=payload.episode_number,
        kind=payload.kind,
        target_candidate_id=payload.target_candidate_id,
        announcement_text=payload.announcement_text,
    )
    db.add(event)
    db.commit()
    return OperationOk(message=f"Event injected for episode {payload.episode_number}")


# =============================================================================
# Stub Binance pour les endpoints qui ne font pas d'exécution
# =============================================================================


class _NullBinance:
    """Binance fictif pour les endpoints admin qui ne placent jamais d'ordre.

    Utilisé uniquement par approve/reject/list qui ne touchent pas à l'API.
    """

    name = "null"

    async def get_current_price(self, symbol):
        raise NotImplementedError

    async def open_oco_buy(self, *args, **kwargs):
        raise NotImplementedError

    async def close_position_market(self, *args, **kwargs):
        raise NotImplementedError

    async def poll_oco_closures(self):
        return []
