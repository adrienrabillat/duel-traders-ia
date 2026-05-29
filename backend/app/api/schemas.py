"""Schémas Pydantic exposés par l'API publique et admin.

On garde les modèles d'API distincts des modèles SQLAlchemy pour pouvoir
faire évoluer la forme du wire sans toucher au schéma DB.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Common
# =============================================================================


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Experiment
# =============================================================================


class ExperimentOut(_Base):
    id: int
    name: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    duration_days: int
    current_episode: int


# =============================================================================
# Candidate & wallet
# =============================================================================


class PositionOut(_Base):
    symbol: str
    quantity: Decimal
    avg_cost_usdc: Decimal


class WalletOut(_Base):
    usdc_balance: Decimal
    initial_balance: Decimal
    positions: list[PositionOut]


class CandidateOut(_Base):
    id: int
    name: str
    model: str
    kind: str
    is_active: bool
    wallet: WalletOut


class TradeOut(_Base):
    id: int
    side: str
    symbol: str
    quantity: Decimal
    price: Decimal
    notional_usdc: Decimal
    fee_usdc: Decimal
    pnl_realized_usdc: Decimal | None
    executed_at: datetime


class LeaderboardEntry(BaseModel):
    candidate_id: int
    candidate_name: str
    model: str
    kind: str
    portfolio_value_usdc: Decimal
    roi_pct: Decimal
    realized_pnl_usdc: Decimal


# =============================================================================
# Strategy
# =============================================================================


class StrategyOut(_Base):
    version: int
    text: str
    episode_number: int
    created_at: datetime


# =============================================================================
# Orders
# =============================================================================


class OrderOut(_Base):
    id: int
    candidate_id: int
    side: str
    symbol: str
    quote_amount_usdc: Decimal
    stop_loss_pct: Decimal | None
    take_profit_pct: Decimal | None
    status: str
    decision_price: Decimal | None
    fill_price: Decimal | None
    fill_quantity: Decimal | None
    fee_usdc: Decimal | None
    error_message: str | None
    created_at: datetime
    validated_at: datetime | None
    filled_at: datetime | None
    closed_at: datetime | None


class RejectOrderIn(BaseModel):
    reason: str = Field("", max_length=500)


# =============================================================================
# Decisions, messages, oracle
# =============================================================================


class DecisionOut(_Base):
    id: int
    candidate_id: int
    episode_number: int
    raw_response: str
    parsed_action: dict | None
    parse_error: str | None
    provider: str
    model: str
    tokens_input: int
    tokens_output: int
    latency_ms: int
    attempt_count: int
    created_at: datetime


class MessageOut(_Base):
    id: int
    sender_name: str
    channel: str
    recipients: list[str]
    content: str
    episode_number: int
    sent_at: datetime


class OracleQueryOut(_Base):
    id: int
    episode_number: int
    question: str
    answer: str | None
    asked_at: datetime


# =============================================================================
# Costs
# =============================================================================


class CostSummaryEntry(BaseModel):
    candidate_id: int
    candidate_name: str
    model: str
    provider: str
    call_count: int
    total_tokens_input: int
    total_tokens_output: int
    total_cost_usd: Decimal
    last_call_at: datetime | None


class CostSummary(BaseModel):
    entries: list[CostSummaryEntry]
    total_cost_usd: Decimal
    total_calls: int
    total_tokens_input: int
    total_tokens_output: int


# =============================================================================
# Admin
# =============================================================================


class CreateExperimentIn(BaseModel):
    name: str
    duration_days: int = 14


class CreateCandidateIn(BaseModel):
    name: str
    model: str
    kind: str = "individual"
    initial_balance_usdc: Decimal = Decimal("10")


class CreateEpisodeEventIn(BaseModel):
    episode_number: int
    kind: str
    announcement_text: str
    target_candidate_id: int | None = None


class KillSwitchResult(BaseModel):
    stopped: bool
    pending_cancelled: int


class OperationOk(BaseModel):
    ok: bool = True
    message: str = ""
