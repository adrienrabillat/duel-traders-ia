"""Routes publiques (lecture seule) consommées par le dashboard spectateur."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.api.schemas import (
    CandidateOut,
    CostSummary,
    CostSummaryEntry,
    DecisionOut,
    DeliberationOut,
    DeliberationTurnOut,
    ExperimentOut,
    LeaderboardEntry,
    MessageOut,
    OracleQueryOut,
    OrderOut,
    PositionOut,
    StrategyOut,
    TradeOut,
    WalletOut,
)
from app.deps import DbDep
from app.models import (
    Candidate,
    Decision,
    Experiment,
    Message,
    OracleQuery,
    OrderRecord,
    Strategy,
    TeamDeliberation,
    Trade,
)
from app.services.episode_manager import EpisodeManager
from app.services.ledger import Ledger
from app.services.llm_pricing import cost_usd

router = APIRouter(prefix="/api", tags=["public"])


# =============================================================================
# Helpers
# =============================================================================


def _current_experiment(db: Session) -> Experiment | None:
    return db.execute(
        select(Experiment)
        .where(Experiment.status.in_(["paper", "live"]))
        .order_by(desc(Experiment.started_at))
        .limit(1)
    ).scalar_one_or_none()


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
# Routes
# =============================================================================


@router.get("/experiment", response_model=ExperimentOut)
def get_current_experiment(db: Session = DbDep) -> ExperimentOut:
    experiment = _current_experiment(db)
    if experiment is None:
        raise HTTPException(404, "No active experiment")
    em = EpisodeManager(experiment)
    return ExperimentOut(
        id=experiment.id,
        name=experiment.name,
        started_at=experiment.started_at,
        ended_at=experiment.ended_at,
        status=experiment.status,
        duration_days=experiment.duration_days,
        current_episode=em.current_episode(datetime.now(timezone.utc)),
    )


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(db: Session = DbDep) -> list[CandidateOut]:
    candidates = list(
        db.execute(
            select(Candidate)
            .options(joinedload(Candidate.wallet))
            .where(Candidate.is_active.is_(True))
            .order_by(Candidate.id)
        )
        .unique()
        .scalars()
    )
    return [
        CandidateOut(
            id=c.id,
            name=c.name,
            model=c.model,
            kind=c.kind,
            is_active=c.is_active,
            wallet=_wallet_out(c),
        )
        for c in candidates
    ]


@router.get("/leaderboard", response_model=list[LeaderboardEntry])
def get_leaderboard(db: Session = DbDep) -> list[LeaderboardEntry]:
    """Classement par valeur de portefeuille décroissante.

    Utilise le avg_cost comme proxy de prix courant pour les positions ouvertes,
    car le service n'a pas accès en temps réel aux prix Binance ici. Pour une
    valorisation marked-to-market, passer par /candidates/{id}/portfolio avec
    les prix injectés par le runtime.
    """
    candidates = list(
        db.execute(
            select(Candidate)
            .options(joinedload(Candidate.wallet))
            .where(Candidate.is_active.is_(True))
        )
        .unique()
        .scalars()
    )
    ledger = Ledger(db)
    entries: list[LeaderboardEntry] = []
    for c in candidates:
        # Valorisation à coût moyen (best-effort sans prix temps-réel)
        prices = {p.symbol: p.avg_cost_usdc for p in c.wallet.positions}
        value = ledger.portfolio_value(c.id, prices)
        initial = c.wallet.initial_balance
        roi = (
            (value - initial) / initial * Decimal("100")
            if initial > 0
            else Decimal("0")
        )
        entries.append(
            LeaderboardEntry(
                candidate_id=c.id,
                candidate_name=c.name,
                model=c.model,
                kind=c.kind,
                portfolio_value_usdc=value,
                roi_pct=roi,
                realized_pnl_usdc=ledger.realized_pnl(c.id),
            )
        )
    entries.sort(key=lambda e: e.portfolio_value_usdc, reverse=True)
    return entries


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(candidate_id: int, db: Session = DbDep) -> CandidateOut:
    candidate = db.execute(
        select(Candidate)
        .options(joinedload(Candidate.wallet))
        .where(Candidate.id == candidate_id)
    ).unique().scalar_one_or_none()
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    return CandidateOut(
        id=candidate.id,
        name=candidate.name,
        model=candidate.model,
        kind=candidate.kind,
        is_active=candidate.is_active,
        wallet=_wallet_out(candidate),
    )


@router.get("/candidates/{candidate_id}/trades", response_model=list[TradeOut])
def get_candidate_trades(
    candidate_id: int,
    limit: int = Query(50, ge=1, le=500),
    db: Session = DbDep,
) -> list[TradeOut]:
    trades = list(
        db.execute(
            select(Trade)
            .where(Trade.candidate_id == candidate_id)
            .order_by(desc(Trade.executed_at))
            .limit(limit)
        ).scalars()
    )
    return [TradeOut.model_validate(t) for t in trades]


@router.get("/candidates/{candidate_id}/strategy", response_model=StrategyOut)
def get_candidate_strategy(candidate_id: int, db: Session = DbDep) -> StrategyOut:
    strat = db.execute(
        select(Strategy)
        .where(Strategy.candidate_id == candidate_id)
        .order_by(desc(Strategy.version))
        .limit(1)
    ).scalar_one_or_none()
    if strat is None:
        raise HTTPException(404, "No strategy yet for this candidate")
    return StrategyOut.model_validate(strat)


@router.get("/candidates/{candidate_id}/decisions", response_model=list[DecisionOut])
def get_candidate_decisions(
    candidate_id: int,
    limit: int = Query(20, ge=1, le=200),
    db: Session = DbDep,
) -> list[DecisionOut]:
    decisions = list(
        db.execute(
            select(Decision)
            .where(Decision.candidate_id == candidate_id)
            .order_by(desc(Decision.created_at))
            .limit(limit)
        ).scalars()
    )
    return [DecisionOut.model_validate(d) for d in decisions]


@router.get("/messages", response_model=list[MessageOut])
def get_messages(
    channel: str | None = Query(None, pattern="^(public|clan)$"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = DbDep,
) -> list[MessageOut]:
    """Messages PUBLICS uniquement (ou clan). Les confessionnaux ne sont pas
    exposés via l'API publique pendant l'expérience — uniquement en post-mortem.
    """
    stmt = select(Message).options(joinedload(Message.sender)).order_by(desc(Message.sent_at)).limit(limit)
    if channel:
        stmt = stmt.where(Message.channel == channel)
    else:
        stmt = stmt.where(Message.channel.in_(["public", "clan"]))
    msgs = list(db.execute(stmt).scalars())
    return [
        MessageOut(
            id=m.id,
            sender_name=m.sender.name,
            channel=m.channel,
            recipients=m.recipients,
            content=m.content,
            episode_number=m.episode_number,
            sent_at=m.sent_at,
        )
        for m in msgs
    ]


@router.get("/oracle", response_model=list[OracleQueryOut])
def get_oracle_feed(
    limit: int = Query(50, ge=1, le=500), db: Session = DbDep
) -> list[OracleQueryOut]:
    queries = list(
        db.execute(
            select(OracleQuery).order_by(desc(OracleQuery.asked_at)).limit(limit)
        ).scalars()
    )
    return [
        OracleQueryOut(
            id=q.id,
            episode_number=q.episode_number,
            question=q.question,
            answer=q.answer,
            asked_at=q.asked_at,
        )
        for q in queries
    ]


@router.get("/orders/recent", response_model=list[OrderOut])
def get_recent_orders(
    limit: int = Query(50, ge=1, le=200), db: Session = DbDep
) -> list[OrderOut]:
    orders = list(
        db.execute(
            select(OrderRecord).order_by(desc(OrderRecord.created_at)).limit(limit)
        ).scalars()
    )
    return [OrderOut.model_validate(o) for o in orders]


@router.get("/decisions/{decision_id}/deliberations", response_model=DeliberationOut)
def get_deliberation(decision_id: int, db: Session = DbDep) -> DeliberationOut:
    """Retourne le fil complet d'une délibération du Team pour une décision donnée.

    Renvoie 404 si la décision n'existe pas ou n'est pas une décision du conseil
    (auquel cas il n'y a pas de fil).
    """
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    turns = list(
        db.execute(
            select(TeamDeliberation)
            .where(TeamDeliberation.decision_id == decision_id)
            .order_by(TeamDeliberation.turn_number, TeamDeliberation.created_at)
        ).scalars()
    )
    if not turns:
        raise HTTPException(404, "No deliberation for this decision")
    counts: dict[str, int] = {}
    # Dernier vote par membre (on peut avoir plusieurs tours)
    last_vote: dict[str, str] = {}
    for t in turns:
        if t.proposed_action_kind:
            last_vote[t.speaker_name] = t.proposed_action_kind
    for kind in last_vote.values():
        counts[kind] = counts.get(kind, 0) + 1
    return DeliberationOut(
        decision_id=decision_id,
        episode_number=decision.episode_number,
        created_at=decision.created_at,
        final_action=decision.parsed_action,
        turns=[DeliberationTurnOut.model_validate(t) for t in turns],
        vote_counts=counts,
    )


@router.get("/costs", response_model=CostSummary)
def get_costs(db: Session = DbDep) -> CostSummary:
    """Coûts cumulés des appels LLM par candidat depuis le début de l'expérience.

    Calculés à la volée à partir des tokens stockés dans Decision × prix par modèle
    (cf. app.services.llm_pricing). Pas de cache : c'est négligeable côté CPU.
    """
    candidates = list(
        db.execute(
            select(Candidate)
            .where(Candidate.is_active.is_(True))
            .order_by(Candidate.id)
        ).scalars()
    )
    decisions_by_cand: dict[int, list[Decision]] = {c.id: [] for c in candidates}
    for d in db.execute(select(Decision)).scalars():
        decisions_by_cand.setdefault(d.candidate_id, []).append(d)

    entries: list[CostSummaryEntry] = []
    total_cost = Decimal("0")
    total_calls = 0
    total_in = 0
    total_out = 0
    for c in candidates:
        decisions = decisions_by_cand.get(c.id, [])
        cand_cost = Decimal("0")
        cand_in = 0
        cand_out = 0
        last_at = None
        provider = "mock"
        for d in decisions:
            cand_cost += cost_usd(d.model, d.tokens_input, d.tokens_output)
            cand_in += d.tokens_input
            cand_out += d.tokens_output
            if last_at is None or (d.created_at and d.created_at > last_at):
                last_at = d.created_at
            provider = d.provider
        entries.append(
            CostSummaryEntry(
                candidate_id=c.id,
                candidate_name=c.name,
                model=c.model,
                provider=provider,
                call_count=len(decisions),
                total_tokens_input=cand_in,
                total_tokens_output=cand_out,
                total_cost_usd=cand_cost,
                last_call_at=last_at,
            )
        )
        total_cost += cand_cost
        total_calls += len(decisions)
        total_in += cand_in
        total_out += cand_out

    entries.sort(key=lambda e: e.total_cost_usd, reverse=True)
    return CostSummary(
        entries=entries,
        total_cost_usd=total_cost,
        total_calls=total_calls,
        total_tokens_input=total_in,
        total_tokens_output=total_out,
    )
