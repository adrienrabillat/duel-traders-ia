"""PaperTradingLoop : la boucle d'orchestration runtime.

Une itération (tick) du runtime fait, dans cet ordre :

1. Auto-approuve les ordres pending depuis > 2 min.
2. Exécute les ordres approved/auto_approved sur Binance (paper).
3. Drain les OCO closures (SL/TP déclenchés depuis le dernier tick).
4. Détermine les candidats prêts à être réveillés via le Scheduler.
5. Pour chaque candidat éligible, lance un cycle Orchestrator complet.
6. Commit tout le batch.

La boucle tourne à intervalle régulier (runtime_tick_seconds, par défaut 30s)
et s'arrête proprement sur signal de stop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Experiment
from app.services.binance_connector import BinanceConnector
from app.services.episode_manager import EpisodeManager
from app.services.info_clients import CoinSnapshot
from app.services.ledger import Ledger
from app.services.llm_gateway import LLMGateway
from app.services.orchestrator import Orchestrator
from app.services.order_executor import OrderExecutor
from app.services.scheduler import Scheduler
from app.services.team_council import TeamMember

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration d'un run
# =============================================================================


@dataclass(slots=True)
class TickResult:
    """Bilan d'un tick : utile pour les tests et le monitoring."""

    auto_approved_count: int = 0
    executed_count: int = 0
    oco_closed_count: int = 0
    cycles_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# =============================================================================
# Boucle principale
# =============================================================================


class PaperTradingLoop:
    """Runtime asynchrone du paper trading.

    Args:
        session_factory : callable qui retourne une Session SQLAlchemy fraîche
                          à chaque appel (typiquement sessionmaker()).
        experiment      : l'expérience courante (Experiment SQLAlchemy).
        binance         : connecteur Binance (Paper ou Live).
        gateway         : LLMGateway configuré avec les providers.
        provider_map    : {candidate_name: provider_name}.
        market_provider : callable async qui renvoie (universe, prices, fear_greed)
                          au moment du tick. Permet d'injecter CoinGecko en prod
                          ou des données fixes en tests.
        tick_seconds    : intervalle entre les ticks.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        experiment: Experiment,
        binance: BinanceConnector,
        gateway: LLMGateway,
        provider_map: dict[str, str],
        market_provider: Callable[
            [], "asyncio.Future[tuple[list[CoinSnapshot], dict[str, Decimal], int | None]]"
        ]
        | None = None,
        tick_seconds: int = 30,
        team_members: list[TeamMember] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.experiment_id = experiment.id
        self.binance = binance
        self.gateway = gateway
        self.provider_map = provider_map
        self.market_provider = market_provider
        self.tick_seconds = tick_seconds
        self.team_members = team_members or []
        self._stopped = asyncio.Event()

    # =========================================================================
    # Cycle de vie
    # =========================================================================

    async def run_forever(self) -> None:
        """Boucle infinie jusqu'à appel de `stop()`."""
        logger.info("PaperTradingLoop starting (tick=%ds)", self.tick_seconds)
        while not self._stopped.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Tick raised uncaught exception")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), timeout=self.tick_seconds)
        logger.info("PaperTradingLoop stopped")

    def stop(self) -> None:
        self._stopped.set()

    # =========================================================================
    # Un tick
    # =========================================================================

    async def tick(self) -> TickResult:
        """Exécute une itération complète. Renvoie un TickResult."""
        result = TickResult()
        with self.session_factory() as db:
            try:
                experiment = db.get(Experiment, self.experiment_id)
                if experiment is None:
                    logger.warning("Experiment %d not found, skipping tick", self.experiment_id)
                    return result
                episode_mgr = EpisodeManager(experiment)
                if episode_mgr.is_finished():
                    self.stop()
                    return result

                ledger = Ledger(db)
                executor = OrderExecutor(db, ledger, self.binance)
                scheduler = Scheduler(db, episode_mgr)

                # 1. Auto-approve
                auto = executor.auto_approve_stale()
                result.auto_approved_count = len(auto)

                # 2. Execute approved
                executed = await executor.execute_pending()
                result.executed_count = len(executed)

                # 3. OCO closures
                closed = await executor.process_oco_closures()
                result.oco_closed_count = len(closed)

                # 4. Market data snapshot
                universe, prices, fear_greed = await self._fetch_market()

                # 5. Run cycles for candidates ready
                orchestrator = Orchestrator(
                    db,
                    experiment,
                    ledger,
                    self.gateway,
                    episode_mgr,
                    team_members=self.team_members,
                )
                ready = scheduler.candidates_ready_to_wake()
                for candidate, wake in ready:
                    provider_name = self.provider_map.get(candidate.name)
                    if provider_name is None:
                        logger.warning(
                            "Skipping candidate %s : no provider configured",
                            candidate.name,
                        )
                        continue
                    try:
                        await orchestrator.run_candidate_cycle(
                            candidate,
                            provider_name=provider_name,
                            market_universe=universe,
                            current_prices=prices,
                            fear_greed=fear_greed,
                        )
                        result.cycles_run.append(candidate.name)
                    except Exception as e:
                        logger.exception(
                            "Cycle failed for candidate %s", candidate.name
                        )
                        result.errors.append(f"{candidate.name}: {e}")

                db.commit()
            except Exception:
                db.rollback()
                raise
        return result

    # =========================================================================
    # Helpers
    # =========================================================================

    async def _fetch_market(
        self,
    ) -> tuple[list[CoinSnapshot], dict[str, Decimal], int | None]:
        if self.market_provider is None:
            return [], {}, None
        return await self.market_provider()


# =============================================================================
# Helper : provider de marché statique
# =============================================================================


def static_market_provider(
    universe: list[CoinSnapshot],
    prices: dict[str, Decimal],
    fear_greed: int | None = None,
):
    """Renvoie toujours les mêmes données. Pratique pour les tests."""

    async def _provider():
        return universe, prices, fear_greed

    return _provider
