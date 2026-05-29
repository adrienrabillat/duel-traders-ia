"""Routes admin (auth Bearer requise si ADMIN_API_TOKEN configuré).

Endpoints réservés à l'équipe humaine qui pilote l'expérience :
- gestion de l'expérience (create / stop)
- gestion des candidats
- validation manuelle des ordres
- kill switch et liquidation d'urgence
- injection d'EpisodeEvent (annonces, Twists)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
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
    Decision,
    EpisodeEvent,
    Experiment,
    OrderRecord,
    Position,
    Strategy,
    TeamDeliberation,
    Trade,
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


@router.post("/candidates/{candidate_id}/wake")
async def force_wake_candidate(
    candidate_id: int, request: Request, turns: int = 1
) -> dict:
    """Force un ou plusieurs cycles pour ce candidat, hors planning du scheduler.

    Query params :
    - `turns` (1-5, défaut 1) : enchaîne N cycles d'affilée. Utile pour Team
      qui produit une délibération complète par cycle ; en demander 2-3 d'un
      coup permet de voir le conseil débattre sur des contextes différents.

    Renvoie {ok, runs: [...]} avec un sous-résultat par cycle.
    Le type d'exception exact est renvoyé en cas d'échec — pratique pour
    diagnostiquer en prod.
    """
    if turns < 1 or turns > 5:
        raise HTTPException(400, "turns doit être entre 1 et 5")
    loop = getattr(request.app.state, "runtime_loop", None)
    if loop is None:
        raise HTTPException(503, "Runtime not started yet — try again in a few seconds.")
    runs = []
    for _ in range(turns):
        result = await loop.wake_candidate(candidate_id)
        runs.append(result)
        if not result.get("ok"):
            break  # on arrête au premier échec
    return {"ok": all(r.get("ok") for r in runs), "runs": runs}


def _reset_candidate_data(db: Session, candidate: Candidate) -> dict:
    """Helper : efface tout l'historique d'un candidat et reset son wallet.

    Ordre important : TeamDeliberation -> OrderRecord -> Trade -> Decision
    -> Strategy -> Position -> wallet. On supprime les enfants avant les
    parents pour éviter les violations de FK même si le CASCADE DB devrait
    suffire (sécurité ceinture+bretelles + clarté).
    """
    cid = candidate.id
    decisions_count = db.query(Decision).filter(Decision.candidate_id == cid).count()
    trades_count = db.query(Trade).filter(Trade.candidate_id == cid).count()
    orders_count = db.query(OrderRecord).filter(OrderRecord.candidate_id == cid).count()
    strategies_count = db.query(Strategy).filter(Strategy.candidate_id == cid).count()

    # On bypass l'autoflush pendant les deletes massifs pour éviter qu'un
    # UPDATE pending sur la session (genre wallet.usdc_balance) parte
    # avant les deletes et déclenche un conflit FK.
    with db.no_autoflush:
        # Supprimer d'abord les TeamDeliberations liées aux decisions du candidat
        decision_ids = [
            d.id for d in db.query(Decision.id)
            .filter(Decision.candidate_id == cid).all()
        ]
        if decision_ids:
            db.query(TeamDeliberation).filter(
                TeamDeliberation.decision_id.in_(decision_ids)
            ).delete(synchronize_session=False)

        db.query(OrderRecord).filter(OrderRecord.candidate_id == cid).delete(synchronize_session=False)
        db.query(Trade).filter(Trade.candidate_id == cid).delete(synchronize_session=False)
        db.query(Decision).filter(Decision.candidate_id == cid).delete(synchronize_session=False)
        db.query(Strategy).filter(Strategy.candidate_id == cid).delete(synchronize_session=False)
        if candidate.wallet:
            db.query(Position).filter(Position.wallet_id == candidate.wallet.id).delete(
                synchronize_session=False
            )

    # Maintenant on peut mettre à jour le wallet (UPDATE simple, pas de
    # conflit FK puisque tout l'historique est nettoyé).
    if candidate.wallet:
        candidate.wallet.usdc_balance = candidate.wallet.initial_balance
    return {
        "decisions": decisions_count,
        "trades": trades_count,
        "orders": orders_count,
        "strategies": strategies_count,
    }


@router.post("/reset-all")
def reset_all_candidates(
    db: Session = DbDep, new_initial_balance_usdc: str | None = None
) -> dict:
    """Reset complet de TOUS les candidats. Pour repartir saison à zéro.

    Si new_initial_balance_usdc est passé en query param, on update aussi
    initial_balance (par défaut on garde l'initial_balance existant).
    """
    from decimal import Decimal as _D

    try:
        new_initial = _D(new_initial_balance_usdc) if new_initial_balance_usdc else None
        candidates = db.query(Candidate).all()
        summary = []
        for c in candidates:
            if new_initial is not None and c.wallet:
                c.wallet.initial_balance = new_initial
            cleared = _reset_candidate_data(db, c)
            summary.append({
                "candidate": c.name,
                "cleared": cleared,
                "wallet_usdc": str(c.wallet.usdc_balance) if c.wallet else "n/a",
            })
        db.commit()
    except Exception as e:
        db.rollback()
        import traceback as _tb
        logger.exception("Reset all failed")
        raise HTTPException(
            500,
            f"{type(e).__name__}: {e}\n\n{_tb.format_exc()[:2000]}",
        ) from e
    return {"ok": True, "count": len(summary), "candidates": summary}


@router.post("/pause")
async def pause_experiment(request: Request, db: Session = DbDep) -> dict:
    """Pause l'expérience : liquide toutes les positions ouvertes en USDC,
    cancel les ordres pending, passe l'expérience en status 'paused'.

    Les candidats ne sont plus réveillés tant que l'expérience est en pause.
    """
    exp = db.execute(
        select(Experiment).where(Experiment.status.in_(["paper", "live"]))
    ).scalar_one_or_none()
    if exp is None:
        raise HTTPException(404, "No active experiment to pause")

    loop = getattr(request.app.state, "runtime_loop", None)
    binance = loop.binance if loop is not None else None

    liquidated = []
    cancelled = 0

    # Cancel les ordres pending
    pending = list(
        db.execute(
            select(OrderRecord).where(
                OrderRecord.status.in_(["pending_validation", "approved", "auto_approved"])
            )
        ).scalars()
    )
    now = datetime.now(timezone.utc)
    for o in pending:
        o.status = "rejected"
        o.validated_at = now
        o.error_message = "paused_by_admin"
        cancelled += 1

    # Liquider les positions ouvertes au prix courant de PaperBinance
    if binance is not None:
        from app.services.ledger import Ledger as _Ledger

        ledger = _Ledger(db)
        positions = list(db.query(Position).all())
        for pos in positions:
            try:
                price = await binance.get_current_price(pos.symbol)
            except Exception:
                continue
            try:
                ledger.record_sell(
                    candidate_id=pos.wallet.candidate_id,
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    price=price,
                )
                liquidated.append({
                    "symbol": pos.symbol,
                    "quantity": str(pos.quantity),
                    "price": str(price),
                })
            except Exception as e:
                liquidated.append({"symbol": pos.symbol, "error": str(e)})

    exp.status = "paused"
    db.commit()
    return {
        "ok": True,
        "status": "paused",
        "orders_cancelled": cancelled,
        "positions_liquidated": liquidated,
    }


@router.post("/resume")
def resume_experiment(db: Session = DbDep) -> dict:
    """Reprend une expérience mise en pause."""
    exp = db.execute(
        select(Experiment).where(Experiment.status == "paused")
    ).scalar_one_or_none()
    if exp is None:
        raise HTTPException(404, "No paused experiment to resume")
    exp.status = "paper"
    db.commit()
    return {"ok": True, "status": "paper", "experiment_id": exp.id}


@router.post("/candidates/{candidate_id}/reset")
def reset_candidate(candidate_id: int, db: Session = DbDep) -> dict:
    """Reset complet d'un candidat : efface tout l'historique et remet le wallet
    à son solde initial. Marche pour TOUS les candidats (solo et team).
    En cas d'erreur, on renvoie un 500 avec le type d'exception exact dans
    le JSON pour faciliter le debug en prod.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    try:
        cleared = _reset_candidate_data(db, candidate)
        db.commit()
    except Exception as e:
        db.rollback()
        import traceback as _tb
        logger.exception("Reset candidate %s failed", candidate_id)
        raise HTTPException(
            500,
            f"{type(e).__name__}: {e}\n\n{_tb.format_exc()[:2000]}",
        ) from e
    return {
        "ok": True,
        "candidate": candidate.name,
        "cleared": cleared,
        "wallet": {
            "usdc_balance": str(candidate.wallet.usdc_balance),
        },
    }


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
