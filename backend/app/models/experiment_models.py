"""Modèles SQLAlchemy de la couche expérience (au-dessus du ledger).

Contient tout ce qui n'est pas strictement comptable :
- Experiment    : métadonnées du run (start/end, épisode courant).
- Strategy      : versions successives de la stratégie écrite d'un candidat.
- Decision      : trace de chaque appel LLM (prompt, raw, action parsée).
- OrderRecord   : cycle de vie d'un ordre Binance, avec state machine.
- Message       : messages inter-modèles (public / confessionnal / clan).
- OracleQuery   : questions Tavily, alimente le feed partagé.
- EpisodeEvent  : annonces de jalons aux candidats.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.ledger_models import Base, Candidate, utcnow


# =============================================================================
# Experiment
# =============================================================================


class Experiment(Base):
    """Run unique de l'expérience. Référencé par tous les autres modèles temporels."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="paper")
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('paper', 'live', 'completed', 'aborted')",
            name="ck_experiments_status",
        ),
    )


# =============================================================================
# Strategy (versionnée)
# =============================================================================


class Strategy(Base):
    """Version horodatée de la stratégie écrite d'un candidat.

    La stratégie courante est celle dont version est la plus haute pour un
    candidat donné. On ne supprime jamais les anciennes versions : c'est le
    matériel d'analyse comportementale.
    """

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[Candidate] = relationship("Candidate")

    __table_args__ = (
        Index("ix_strategies_candidate_version", "candidate_id", "version"),
    )


# =============================================================================
# Decision (chaque appel LLM est traçable)
# =============================================================================


class Decision(Base):
    """Trace immuable d'un appel LLM pour un candidat à un instant donné.

    parsed_action est stocké en JSON pour pouvoir replay les actions sans
    avoir à reparcer le raw_response.
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[Candidate] = relationship("Candidate")
    orders: Mapped[list[OrderRecord]] = relationship(
        "OrderRecord", back_populates="decision", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_decisions_candidate_time", "candidate_id", "created_at"),
    )


# =============================================================================
# OrderRecord (state machine)
# =============================================================================


# Statuts possibles d'un ordre. Le passage de l'un à l'autre est strictement
# contrôlé par le service Binance.
ORDER_STATUSES = (
    "pending_validation",  # créé, en attente d'un humain (ou du timer)
    "approved",            # validé manuellement
    "auto_approved",       # validé par timer (2 min)
    "rejected",            # refusé manuellement
    "submitted",           # envoyé à Binance
    "filled",              # rempli (position ouverte ou clôturée)
    "closed_by_sl",        # OCO : stop-loss déclenché
    "closed_by_tp",        # OCO : take-profit déclenché
    "closed_manually",     # action CLOSE_POSITION
    "failed",              # Binance a rejeté
)


class OrderRecord(Base):
    """Cycle de vie d'un ordre. Chaque décision PLACE_ORDER crée un OrderRecord."""

    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quote_amount_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    take_profit_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_validation")
    decision_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    fill_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    fee_usdc: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    binance_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    client_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    decision: Mapped[Decision | None] = relationship("Decision", back_populates="orders")
    candidate: Mapped[Candidate] = relationship("Candidate")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in ORDER_STATUSES)})",
            name="ck_order_records_status",
        ),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_order_records_side"),
        Index("ix_order_records_status", "status"),
        Index("ix_order_records_candidate_time", "candidate_id", "created_at"),
    )


# =============================================================================
# Messages inter-modèles
# =============================================================================


class Message(Base):
    """Message envoyé par un candidat (canal public, confessionnal, clan)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    # Pour 'public' : []. Pour 'private' : [nom_destinataire]. Pour 'clan' : [nom_clan].
    recipients: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    sender: Mapped[Candidate] = relationship("Candidate")

    __table_args__ = (
        CheckConstraint("channel IN ('public', 'private', 'clan')", name="ck_messages_channel"),
        Index("ix_messages_episode_channel", "episode_number", "channel"),
    )


# =============================================================================
# Oracle queries (feed Tavily partagé, anonymisé côté affichage)
# =============================================================================


class OracleQuery(Base):
    """Requête envoyée à Tavily par un candidat.

    Le candidate_id est conservé pour analyse forensique post-expérience, mais
    le feed affiché aux modèles est anonymisé (on ne montre que question + réponse).
    """

    __tablename__ = "oracle_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[Candidate] = relationship("Candidate")

    __table_args__ = (
        Index("ix_oracle_queries_episode", "episode_number"),
    )


# =============================================================================
# Episode events (annonces aux candidats)
# =============================================================================


class TeamDeliberation(Base):
    """Trace tour par tour d'une délibération du candidat Team.

    À chaque cycle du candidat équipe, on appelle séquentiellement les 5
    modèles (Claude, ChatGPT, Mistral, Gemini, Grok). Chacun produit un
    message adressé au conseil + une action proposée. La majorité simple
    sur l'action_kind décide.
    """

    __tablename__ = "team_deliberations"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_name: Mapped[str] = mapped_column(String(50), nullable=False)
    speaker_model: Mapped[str] = mapped_column(String(100), nullable=False)
    speaker_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    message_to_team: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_action_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_team_deliberations_decision", "decision_id"),
        Index("ix_team_deliberations_decision_turn", "decision_id", "turn_number"),
    )


class EpisodeEvent(Base):
    """Annonce de jalon ou injection de Twist dans le prompt d'un (ou plusieurs) candidats."""

    __tablename__ = "episode_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    # target_candidate_id NULL = annonce à tous
    target_candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=True
    )
    announcement_text: Mapped[str] = mapped_column(Text, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_episode_events_episode", "episode_number"),
    )
