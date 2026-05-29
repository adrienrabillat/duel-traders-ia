"""Modèles SQLAlchemy pour le Ledger virtuel.

Le Ledger est la couche de comptabilité interne qui maintient les portefeuilles
virtuels des candidats à partir d'un unique compte Binance physique.

Précision numérique :
- USDC : Numeric(20, 8) — 12 chiffres avant la virgule, 8 après.
- Quantités d'actifs : Numeric(28, 12) — gère les très petites quantités de
  jetons à faible prix unitaire (ex. SHIB, PEPE).

Les colonnes monétaires utilisent Decimal en Python (jamais float) pour
éviter toute erreur d'arrondi cumulée sur des centaines de trades.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Datetime UTC tz-aware (les naive sont à éviter)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base déclarative SQLAlchemy 2.0 typée."""

    pass


class Candidate(Base):
    """Un candidat de l'expérience.

    Couvre les cinq candidats individuels (Claude, ChatGPT, Mistral, Gemini,
    Grok), le candidat équipe (Team), et le candidat fantôme HODL BTC.
    """

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="individual")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    wallet: Mapped[Wallet] = relationship(
        "Wallet", back_populates="candidate", uselist=False, cascade="all, delete-orphan"
    )
    trades: Mapped[list[Trade]] = relationship(
        "Trade", back_populates="candidate", cascade="all, delete-orphan", order_by="Trade.executed_at"
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('individual', 'team', 'ghost')",
            name="ck_candidates_kind",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Candidate name={self.name!r} model={self.model!r} kind={self.kind!r}>"


class Wallet(Base):
    """Portefeuille virtuel d'un candidat.

    Stocke le solde USDC et a une relation 1-N vers les positions ouvertes.
    """

    __tablename__ = "wallets"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    usdc_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[Candidate] = relationship("Candidate", back_populates="wallet")
    positions: Mapped[list[Position]] = relationship(
        "Position", back_populates="wallet", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("usdc_balance >= 0", name="ck_wallets_usdc_nonneg"),)


class Position(Base):
    """Position ouverte sur un actif donné, pour un wallet donné.

    Une position agrège toutes les unités détenues d'un symbole, avec un
    coût moyen pondéré (avg_cost_usdc). Quand une position est entièrement
    vendue, son enregistrement est supprimé (pour ne pas polluer les
    requêtes — l'historique reste dans la table trades).
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    avg_cost_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    wallet: Mapped[Wallet] = relationship("Wallet", back_populates="positions")

    __table_args__ = (
        UniqueConstraint("wallet_id", "symbol", name="uq_positions_wallet_symbol"),
        CheckConstraint("quantity > 0", name="ck_positions_qty_positive"),
        CheckConstraint("avg_cost_usdc > 0", name="ck_positions_cost_positive"),
        Index("ix_positions_wallet", "wallet_id"),
    )


class Trade(Base):
    """Trace immuable d'un trade exécuté.

    Chaque insertion représente un événement de marché (achat ou vente).
    Pour un SELL, pnl_realized_usdc indique le P&L réalisé sur la quantité
    vendue, calculé contre le coût moyen pondéré de la position avant vente.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    notional_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee_usdc: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"))
    pnl_realized_usdc: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    candidate: Mapped[Candidate] = relationship("Candidate", back_populates="trades")

    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
        CheckConstraint("quantity > 0", name="ck_trades_qty_positive"),
        CheckConstraint("price > 0", name="ck_trades_price_positive"),
        Index("ix_trades_candidate_time", "candidate_id", "executed_at"),
    )
