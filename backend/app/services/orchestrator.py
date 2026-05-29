"""Orchestrator : le chef d'orchestre d'un cycle de candidat.

Responsabilités :
1. Rassembler l'état courant (portefeuille, stratégie, messages, oracle, marché).
2. Construire le prompt via PromptBuilder.
3. Appeler le LLM Gateway pour obtenir une action validée.
4. Persister la Decision.
5. Dispatcher l'action :
   - PLACE_ORDER  → créer un OrderRecord en `pending_validation`.
   - CLOSE_POSITION → créer un OrderRecord de clôture.
   - UPDATE_STRATEGY → versionner la stratégie.
   - QUERY_ORACLE → appeler Tavily, persister la query+réponse.
   - SEND_MESSAGE → persister le message (avec vérification de visibilité).
   - HOLD → ne rien faire, log uniquement.

Cet orchestrator NE valide PAS les ordres : c'est la responsabilité de
l'admin (via l'API) ou du timer d'auto-approbation. Voir order_executor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Candidate,
    Decision,
    EpisodeEvent,
    Experiment,
    Message,
    OracleQuery,
    OrderRecord,
    Strategy,
    TeamDeliberation,
    Trade,
)
from app.services.episode_manager import ChannelPermissions, EpisodeManager
from app.services.info_clients import CoinSnapshot, TavilyClient
from app.services.ledger import Ledger
from app.services.llm_gateway import LLMGateway, LLMRequest
from app.services.llm_gateway.actions import (
    ClosePositionAction,
    HoldAction,
    PlaceOrderAction,
    QueryOracleAction,
    SendMessageAction,
    UpdateStrategyAction,
)
from app.services.prompt_builder import (
    CycleContext,
    build_system_prompt,
    build_user_prompt,
)
from app.services.team_council import (
    TeamMember,
    run_council_deliberation,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Résultat d'un cycle
# =============================================================================


@dataclass(slots=True)
class CycleResult:
    decision_id: int
    action_kind: str
    order_id: int | None = None
    strategy_version: int | None = None
    message_id: int | None = None
    oracle_query_id: int | None = None
    next_wake_at_utc: datetime | None = None


# =============================================================================
# Orchestrator
# =============================================================================


class Orchestrator:
    """Orchestrateur d'un cycle de candidat."""

    def __init__(
        self,
        db: Session,
        experiment: Experiment,
        ledger: Ledger,
        gateway: LLMGateway,
        episode_manager: EpisodeManager,
        tavily: TavilyClient | None = None,
        team_members: list[TeamMember] | None = None,
    ) -> None:
        self.db = db
        self.experiment = experiment
        self.ledger = ledger
        self.gateway = gateway
        self.episodes = episode_manager
        self.tavily = tavily
        self.team_members = team_members or []

    # =========================================================================
    # Cycle principal
    # =========================================================================

    async def run_candidate_cycle(
        self,
        candidate: Candidate,
        provider_name: str,
        *,
        market_universe: list[CoinSnapshot] | None = None,
        current_prices: dict[str, Decimal] | None = None,
        fear_greed: int | None = None,
        now: datetime | None = None,
    ) -> CycleResult:
        """Lance un cycle complet pour ce candidat."""
        now = now or datetime.now(timezone.utc)
        episode_number = self.episodes.current_episode(now)
        permissions = EpisodeManager.channel_permissions(episode_number)

        ctx = self._gather_context(
            candidate=candidate,
            episode_number=episode_number,
            permissions=permissions,
            market_universe=market_universe or [],
            current_prices=current_prices or {},
            fear_greed=fear_greed,
            now=now,
        )

        system_prompt = build_system_prompt(candidate)
        user_prompt = build_user_prompt(ctx)

        # Branche le flux team si applicable
        if candidate.kind == "team" and self.team_members:
            return await self._run_team_cycle(
                candidate=candidate,
                ctx=ctx,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                now=now,
            )

        request = LLMRequest(
            candidate_name=candidate.name,
            model=candidate.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        response = await self.gateway.request_decision(provider_name, request)

        # On stocke la Decision dans tous les cas, même si parsed_action est None
        # (en pratique le Gateway lève déjà MaxRetriesExceededError avant ça,
        # mais on est défensifs).
        decision = Decision(
            candidate_id=candidate.id,
            episode_number=episode_number,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_response=response.raw_text,
            parsed_action=(
                response.parsed_action.model_dump(mode="json")
                if response.parsed_action is not None
                else None
            ),
            parse_error=response.parse_error,
            provider=response.provider,
            model=response.model,
            tokens_input=response.tokens_input,
            tokens_output=response.tokens_output,
            latency_ms=response.latency_ms,
            attempt_count=response.attempt_count,
        )
        self.db.add(decision)
        self.db.flush()

        # Marquer les EpisodeEvents comme délivrés
        for ev in ctx.pending_events:
            ev.delivered_at = now

        # Dispatch
        result = await self._dispatch_action(decision, response.parsed_action, ctx)
        self.db.flush()
        return result

    # =========================================================================
    # Rassemblement du contexte
    # =========================================================================

    def _gather_context(
        self,
        *,
        candidate: Candidate,
        episode_number: int,
        permissions: ChannelPermissions,
        market_universe: list[CoinSnapshot],
        current_prices: dict[str, Decimal],
        fear_greed: int | None,
        now: datetime,
    ) -> CycleContext:
        wallet = self.ledger.get_wallet(candidate.id)
        current_strategy = self.db.execute(
            select(Strategy)
            .where(Strategy.candidate_id == candidate.id)
            .order_by(desc(Strategy.version))
            .limit(1)
        ).scalar_one_or_none()

        since = now - timedelta(hours=24)
        recent_trades = list(
            self.db.execute(
                select(Trade)
                .where(Trade.candidate_id == candidate.id, Trade.executed_at >= since)
                .order_by(Trade.executed_at)
            ).scalars()
        )

        inbox = self._fetch_inbox(candidate, episode_number)

        oracle_feed = list(
            self.db.execute(
                select(OracleQuery)
                .where(OracleQuery.episode_number >= max(1, episode_number - 1))
                .order_by(OracleQuery.asked_at.desc())
                .limit(15)
            ).scalars()
        )

        pending_events = list(
            self.db.execute(
                select(EpisodeEvent)
                .where(
                    EpisodeEvent.episode_number <= episode_number,
                    EpisodeEvent.delivered_at.is_(None),
                    (EpisodeEvent.target_candidate_id == candidate.id)
                    | (EpisodeEvent.target_candidate_id.is_(None)),
                )
                .order_by(EpisodeEvent.fired_at)
            ).scalars()
        )

        return CycleContext(
            candidate=candidate,
            episode_number=episode_number,
            is_competition_announced=EpisodeManager.competition_announced(episode_number),
            are_strategies_revealed=EpisodeManager.strategies_revealed(episode_number),
            channel_permissions=permissions,
            wallet=wallet,
            current_prices=current_prices,
            recent_trades=recent_trades,
            current_strategy=current_strategy,
            market_universe=market_universe,
            fear_greed_index=fear_greed,
            inbox_messages=inbox,
            oracle_feed=oracle_feed,
            pending_events=pending_events,
            now=now,
        )

    def _fetch_inbox(self, candidate: Candidate, episode_number: int) -> list[Message]:
        """Récupère les messages adressés à ce candidat (publics ou ciblés)."""
        stmt = (
            select(Message)
            .options(joinedload(Message.sender))
            .where(Message.episode_number <= episode_number)
            .order_by(Message.sent_at)
        )
        msgs = list(self.db.execute(stmt).scalars())
        out: list[Message] = []
        for m in msgs:
            if m.sender_candidate_id == candidate.id:
                continue  # on ne renvoie pas à un candidat ses propres messages
            if m.channel == "public":
                out.append(m)
            elif m.channel == "private" and candidate.name in m.recipients:
                out.append(m)
            elif m.channel == "clan":
                # Pour la v1 : on suppose que tous les clans sont identifiés par leur nom
                # et qu'un candidat dans le clan reçoit ; détail à implémenter avec la
                # table clans/clan_members. Pour l'instant on inclut.
                out.append(m)
        return out

    # =========================================================================
    # Dispatch d'action
    # =========================================================================

    async def _dispatch_action(
        self,
        decision: Decision,
        action: object | None,
        ctx: CycleContext,
    ) -> CycleResult:
        if action is None:
            return CycleResult(decision_id=decision.id, action_kind="UNKNOWN")

        if isinstance(action, PlaceOrderAction):
            order = self._handle_place_order(decision, action, ctx)
            return CycleResult(
                decision_id=decision.id,
                action_kind="PLACE_ORDER",
                order_id=order.id,
            )
        if isinstance(action, ClosePositionAction):
            order = self._handle_close_position(decision, action, ctx)
            return CycleResult(
                decision_id=decision.id,
                action_kind="CLOSE_POSITION",
                order_id=order.id,
            )
        if isinstance(action, UpdateStrategyAction):
            strategy = self._handle_update_strategy(action, ctx)
            return CycleResult(
                decision_id=decision.id,
                action_kind="UPDATE_STRATEGY",
                strategy_version=strategy.version,
            )
        if isinstance(action, SendMessageAction):
            msg = self._handle_send_message(action, ctx)
            return CycleResult(
                decision_id=decision.id,
                action_kind="SEND_MESSAGE",
                message_id=msg.id if msg else None,
            )
        if isinstance(action, QueryOracleAction):
            q = await self._handle_query_oracle(action, ctx)
            return CycleResult(
                decision_id=decision.id,
                action_kind="QUERY_ORACLE",
                oracle_query_id=q.id,
            )
        if isinstance(action, HoldAction):
            next_wake = self._parse_next_wake(action.next_wake_at_utc)
            return CycleResult(
                decision_id=decision.id,
                action_kind="HOLD",
                next_wake_at_utc=next_wake,
            )
        return CycleResult(decision_id=decision.id, action_kind="UNKNOWN")

    # =========================================================================
    # Handlers
    # =========================================================================

    def _handle_place_order(
        self, decision: Decision, action: PlaceOrderAction, ctx: CycleContext
    ) -> OrderRecord:
        price_at_decision = ctx.current_prices.get(action.symbol)
        order = OrderRecord(
            decision_id=decision.id,
            candidate_id=ctx.candidate.id,
            side=action.side,
            symbol=action.symbol,
            quote_amount_usdc=action.quote_amount_usdc,
            stop_loss_pct=action.stop_loss_pct,
            take_profit_pct=action.take_profit_pct,
            # Paper trading : on saute la validation manuelle, l'ordre s'exécute
            # au prochain tick. Pour live trading il faudra repasser à
            # "pending_validation" et ré-activer le flow d'approve.
            status="auto_approved",
            validated_at=datetime.now(timezone.utc),
            decision_price=price_at_decision,
        )
        self.db.add(order)
        self.db.flush()
        return order

    def _handle_close_position(
        self, decision: Decision, action: ClosePositionAction, ctx: CycleContext
    ) -> OrderRecord:
        # On crée un OrderRecord "SELL" en pending_validation également.
        position = self.ledger.get_position(ctx.candidate.id, action.symbol)
        quantity = position.quantity if position is not None else Decimal("0")
        price = ctx.current_prices.get(action.symbol)
        order = OrderRecord(
            decision_id=decision.id,
            candidate_id=ctx.candidate.id,
            side="SELL",
            symbol=action.symbol,
            quote_amount_usdc=(quantity * price) if price is not None else Decimal("0"),
            stop_loss_pct=None,
            take_profit_pct=None,
            # idem PLACE_ORDER : exécution auto en paper
            status="auto_approved",
            validated_at=datetime.now(timezone.utc),
            decision_price=price,
            fill_quantity=quantity,
        )
        self.db.add(order)
        self.db.flush()
        return order

    def _handle_update_strategy(
        self, action: UpdateStrategyAction, ctx: CycleContext
    ) -> Strategy:
        next_version = 1 if ctx.current_strategy is None else ctx.current_strategy.version + 1
        strategy = Strategy(
            candidate_id=ctx.candidate.id,
            version=next_version,
            text=action.new_strategy,
            episode_number=ctx.episode_number,
        )
        self.db.add(strategy)
        self.db.flush()
        return strategy

    def _handle_send_message(
        self, action: SendMessageAction, ctx: CycleContext
    ) -> Message | None:
        if not ctx.channel_permissions.is_allowed(action.channel):
            logger.warning(
                "Candidate %s tried to use channel %s but it's not open at episode %d",
                ctx.candidate.name,
                action.channel,
                ctx.episode_number,
            )
            return None
        msg = Message(
            sender_candidate_id=ctx.candidate.id,
            channel=action.channel,
            recipients=list(action.recipients),
            content=action.content,
            episode_number=ctx.episode_number,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    async def _handle_query_oracle(
        self, action: QueryOracleAction, ctx: CycleContext
    ) -> OracleQuery:
        record = OracleQuery(
            candidate_id=ctx.candidate.id,
            episode_number=ctx.episode_number,
            question=action.question,
        )
        self.db.add(record)
        self.db.flush()
        if self.tavily is not None:
            try:
                answer = await self.tavily.search(action.question)
                record.answer = answer.answer
                record.sources = answer.sources
            except Exception as e:  # pragma: no cover (intégration externe)
                record.error = str(e)
                logger.exception("Tavily call failed")
        else:
            record.answer = None
            record.error = "No oracle client configured"
        self.db.flush()
        return record

    # =========================================================================
    # Team deliberation cycle
    # =========================================================================

    async def _run_team_cycle(
        self,
        candidate: Candidate,
        ctx: CycleContext,
        system_prompt: str,
        user_prompt: str,
        now: datetime,
    ) -> CycleResult:
        """Lance une délibération du conseil et exécute l'action gagnante."""
        history, winning_action, outcome = await run_council_deliberation(
            members=self.team_members,
            gateway=self.gateway,
            base_user_prompt=user_prompt,
        )

        # Agrégat des métriques d'usage de tous les membres
        total_in = sum(h.tokens_input for h in history)
        total_out = sum(h.tokens_output for h in history)
        total_latency = sum(h.latency_ms for h in history)

        decision = Decision(
            candidate_id=candidate.id,
            episode_number=ctx.episode_number,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_response=(
                "Team deliberation — "
                f"{len(history)} interventions, "
                f"votes: {outcome.counts}, "
                f"winner: {outcome.winning_kind or 'HOLD (no majority)'}"
            ),
            parsed_action=winning_action.model_dump(mode="json"),
            parse_error=None,
            provider="team-council",
            model=candidate.model,
            tokens_input=total_in,
            tokens_output=total_out,
            latency_ms=total_latency,
            attempt_count=len(history),
        )
        self.db.add(decision)
        self.db.flush()

        # Persiste chaque intervention
        for h in history:
            self.db.add(
                TeamDeliberation(
                    decision_id=decision.id,
                    turn_number=h.turn_number,
                    speaker_name=h.speaker_name,
                    speaker_model=h.speaker_model,
                    speaker_provider=h.speaker_provider,
                    message_to_team=h.message_to_team,
                    proposed_action=(
                        h.proposed_action.model_dump(mode="json")
                        if h.proposed_action is not None
                        else None
                    ),
                    proposed_action_kind=h.proposed_action_kind,
                    parse_error=h.parse_error,
                    tokens_input=h.tokens_input,
                    tokens_output=h.tokens_output,
                    latency_ms=h.latency_ms,
                )
            )

        # Marque les EpisodeEvents comme délivrés
        for ev in ctx.pending_events:
            ev.delivered_at = now

        # Dispatch l'action gagnante
        result = await self._dispatch_action(decision, winning_action, ctx)
        self.db.flush()
        return result

    @staticmethod
    def _parse_next_wake(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            # Pydantic n'a pas validé en datetime, on accepte ici en best-effort
            from datetime import datetime as _dt
            return _dt.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
