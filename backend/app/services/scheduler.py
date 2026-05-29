"""Scheduler : logique adaptative de réveil des candidats.

Règles de réveil (par candidat) :

1. Expérience terminée (épisode >= duration_days) : plus jamais.
2. Ordre en vol (pending_validation / approved / auto_approved / submitted) :
   on attend la fin du cycle d'exécution, pas de re-déclenchement par temps.
3. Position ouverte (BUY filled, OCO actif) : on attend la clôture
   automatique (poll OCO), pas de réveil par temps.
4. Dernière action HOLD avec next_wake_at_utc : on respecte cette heure,
   plafonnée à `max_sleep_seconds` (24h par défaut) depuis la dernière décision.
5. Sinon : réveil par défaut toutes les `default_interval_seconds` (1h).
6. Pas de décision encore : réveil immédiat (cas du premier cycle).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Candidate, Decision, OrderRecord
from app.services.episode_manager import EpisodeManager

logger = logging.getLogger(__name__)


# =============================================================================
# Constantes
# =============================================================================


# Statuts d'ordre qui empêchent un réveil "par temps" (= cycle en cours)
IN_FLIGHT_STATUSES = (
    "pending_validation",
    "approved",
    "auto_approved",
    "submitted",
)

# Statuts d'ordre signifiant "position ouverte avec OCO actif"
OPEN_POSITION_STATUSES = ("filled",)


# Actions "préparatoires" : le candidat a fait un pas (poser une stratégie,
# interroger l'oracle, envoyer un message) mais n'a pas pris de décision
# finale de trading. Il doit pouvoir agir rapidement sur le résultat.
# Le wake suit `preparation_interval_seconds` (60s par défaut) au lieu de
# l'heure pleine du default_interval.
PREPARATORY_ACTIONS = ("QUERY_ORACLE", "UPDATE_STRATEGY", "SEND_MESSAGE")


# =============================================================================
# Types
# =============================================================================


@dataclass(slots=True, frozen=True)
class WakeDecision:
    should_wake: bool
    reason: str
    """Champ libre pour le logging et le débug."""


def _to_utc(dt: datetime | None) -> datetime | None:
    """Normalise un datetime en UTC. SQLite peut renvoyer du naive."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# =============================================================================
# Scheduler
# =============================================================================


class Scheduler:
    """Calcule, par candidat, s'il faut le réveiller maintenant."""

    def __init__(
        self,
        db: Session,
        episode_manager: EpisodeManager,
        default_interval_seconds: int = 3600,
        max_sleep_seconds: int = 86400,
        preparation_interval_seconds: int = 60,
    ) -> None:
        if default_interval_seconds <= 0:
            raise ValueError("default_interval_seconds must be > 0")
        if max_sleep_seconds < default_interval_seconds:
            raise ValueError("max_sleep_seconds must be >= default_interval_seconds")
        if preparation_interval_seconds <= 0:
            raise ValueError("preparation_interval_seconds must be > 0")
        self.db = db
        self.episodes = episode_manager
        self.default_interval = default_interval_seconds
        self.max_sleep = max_sleep_seconds
        self.preparation_interval = preparation_interval_seconds

    # =========================================================================
    # API principale
    # =========================================================================

    def should_wake(
        self, candidate: Candidate, now: datetime | None = None
    ) -> WakeDecision:
        now = now or datetime.now(timezone.utc)

        # 1) Expérience finie
        if self.episodes.is_finished(now):
            return WakeDecision(False, "experiment_finished")

        # 2) Cycle en cours (un ordre est entre la décision et l'exécution)
        in_flight = self._has_order_in_status(candidate.id, IN_FLIGHT_STATUSES)
        if in_flight:
            return WakeDecision(False, "order_in_flight")

        # 3) Position ouverte (BUY filled avec OCO actif) → on attend OCO closure
        open_position = self._has_order_in_status(candidate.id, OPEN_POSITION_STATUSES, side="BUY")
        if open_position:
            return WakeDecision(False, "waiting_for_oco_closure")

        # 4) Dernière décision HOLD avec next_wake_at_utc
        last = self._last_decision(candidate.id)
        if last is not None:
            scheduled = self._extract_scheduled_wake(last, now)
            if scheduled is not None:
                if now >= scheduled:
                    return WakeDecision(True, "scheduled_by_model")
                return WakeDecision(False, "waiting_scheduled")

        # 5) First run
        if last is None:
            return WakeDecision(True, "first_run")

        last_at = _to_utc(last.created_at) or now
        elapsed = (now - last_at).total_seconds()

        # 6) Action préparatoire (QUERY_ORACLE, UPDATE_STRATEGY, SEND_MESSAGE)
        # → wake rapide (60s) pour que le candidat puisse agir sur le résultat.
        action_kind = (last.parsed_action or {}).get("action")
        if action_kind in PREPARATORY_ACTIONS:
            if elapsed >= self.preparation_interval:
                return WakeDecision(True, f"after_{action_kind.lower()}")
            return WakeDecision(False, "preparing")

        # 7) Hourly default
        if elapsed >= self.default_interval:
            return WakeDecision(True, "hourly_default")
        return WakeDecision(False, "within_interval")

    def candidates_ready_to_wake(
        self, now: datetime | None = None
    ) -> list[tuple[Candidate, WakeDecision]]:
        """Renvoie tous les candidats actifs prêts à être réveillés maintenant."""
        now = now or datetime.now(timezone.utc)
        candidates = list(
            self.db.execute(select(Candidate).where(Candidate.is_active.is_(True))).scalars()
        )
        out: list[tuple[Candidate, WakeDecision]] = []
        for c in candidates:
            decision = self.should_wake(c, now)
            if decision.should_wake:
                out.append((c, decision))
        return out

    # =========================================================================
    # Internals
    # =========================================================================

    def _last_decision(self, candidate_id: int) -> Decision | None:
        return self.db.execute(
            select(Decision)
            .where(Decision.candidate_id == candidate_id)
            .order_by(desc(Decision.created_at))
            .limit(1)
        ).scalar_one_or_none()

    def _has_order_in_status(
        self,
        candidate_id: int,
        statuses: tuple[str, ...],
        side: str | None = None,
    ) -> bool:
        stmt = select(OrderRecord.id).where(
            OrderRecord.candidate_id == candidate_id,
            OrderRecord.status.in_(statuses),
        )
        if side is not None:
            stmt = stmt.where(OrderRecord.side == side)
        return self.db.execute(stmt.limit(1)).scalar() is not None

    def _extract_scheduled_wake(
        self, decision: Decision, now: datetime
    ) -> datetime | None:
        """Si la dernière action est HOLD avec next_wake_at_utc, renvoie le datetime
        plafonné par max_sleep_seconds. Sinon None."""
        action = decision.parsed_action
        if not action or action.get("action") != "HOLD":
            return None
        raw = action.get("next_wake_at_utc")
        if not raw:
            return None
        try:
            requested = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            logger.warning("Invalid next_wake_at_utc on decision %d: %r", decision.id, raw)
            return None
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        decision_at = _to_utc(decision.created_at) or now
        max_allowed = decision_at + timedelta(seconds=self.max_sleep)
        return min(requested, max_allowed)
