"""Service Ledger : comptabilité virtuelle des candidats.

Le Ledger est la pièce la plus critique du système. Une erreur ici invalide
toute l'expérience. Toute opération monétaire passe par lui et il est testé
de manière exhaustive (cf. tests/test_ledger.py).

Règles métier appliquées :
- Toutes les valeurs monétaires sont des Decimal (jamais float).
- Les frais Binance (0,1 %) sont appliqués sur le notional brut.
- Le minimum d'ordre Binance USDC (5 USDC) est vérifié à l'ouverture comme à la clôture.
- Le P&L réalisé d'une vente est calculé contre le coût moyen pondéré de la position.
- Les positions à quantité nulle sont supprimées après vente totale.

Le Ledger NE COMMIT PAS la session : c'est à l'appelant d'appeler db.commit()
(ou de rollback en cas d'erreur). Ce design permet d'inclure plusieurs
opérations Ledger dans une même transaction.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BINANCE_FEE_RATE, MIN_ORDER_USDC, MONEY_QUANTUM
from app.models.ledger_models import Candidate, Position, Trade, Wallet

if TYPE_CHECKING:  # pragma: no cover
    pass


# =============================================================================
# Exceptions
# =============================================================================


class LedgerError(Exception):
    """Erreur de base du Ledger."""


class InsufficientFundsError(LedgerError):
    """USDC insuffisant pour couvrir l'achat (notional + frais)."""


class InsufficientPositionError(LedgerError):
    """Quantité demandée supérieure à la position détenue."""


class BelowMinOrderError(LedgerError):
    """Notional de l'ordre inférieur au minimum Binance (5 USDC)."""


class InvalidQuantityError(LedgerError):
    """Quantité ≤ 0."""


class InvalidPriceError(LedgerError):
    """Prix ≤ 0."""


class CandidateNotFoundError(LedgerError):
    """Candidat (ou son wallet) introuvable."""


# =============================================================================
# Helpers d'arrondi
# =============================================================================


def _money(value: Decimal) -> Decimal:
    """Quantification monétaire à 8 décimales, arrondi vers le bas (en faveur de l'exchange)."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)


# =============================================================================
# Service
# =============================================================================


class Ledger:
    """Couche d'accès au Ledger.

    Le Ledger ne commit pas — l'appelant gère la transaction.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ Setup

    def create_candidate(
        self,
        name: str,
        model: str,
        initial_balance_usdc: Decimal,
        kind: str = "individual",
    ) -> Candidate:
        """Crée un candidat et son wallet associé."""
        if initial_balance_usdc < 0:
            raise LedgerError("initial_balance_usdc must be >= 0")
        candidate = Candidate(name=name, model=model, kind=kind)
        self.db.add(candidate)
        self.db.flush()
        wallet = Wallet(
            candidate_id=candidate.id,
            usdc_balance=_money(initial_balance_usdc),
            initial_balance=_money(initial_balance_usdc),
        )
        self.db.add(wallet)
        self.db.flush()
        # Refresh pour avoir wallet attaché à candidate
        self.db.refresh(candidate)
        return candidate

    # ----------------------------------------------------------------- Lookup

    def get_wallet(self, candidate_id: int) -> Wallet:
        wallet = self.db.execute(
            select(Wallet).where(Wallet.candidate_id == candidate_id)
        ).scalar_one_or_none()
        if wallet is None:
            raise CandidateNotFoundError(f"No wallet for candidate id={candidate_id}")
        return wallet

    def get_position(self, candidate_id: int, symbol: str) -> Position | None:
        wallet = self.get_wallet(candidate_id)
        return self.db.execute(
            select(Position).where(
                Position.wallet_id == wallet.id, Position.symbol == symbol
            )
        ).scalar_one_or_none()

    # -------------------------------------------------------------------- Ops

    def record_buy(
        self,
        candidate_id: int,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
    ) -> Trade:
        """Enregistre un achat.

        Effets :
        - Diminue le solde USDC de (notional + frais).
        - Crée ou met à jour la position correspondante (coût moyen pondéré).
        - Insère un Trade côté BUY.

        Le coût moyen pondéré N'INCLUT PAS les frais (convention métier
        habituelle : les frais sont une perte immédiate, pas une augmentation
        du prix de revient).
        """
        self._validate_quantity(quantity)
        self._validate_price(price)

        notional = _money(quantity * price)
        fee = _money(notional * BINANCE_FEE_RATE)
        total_cost = notional + fee

        if notional < MIN_ORDER_USDC:
            raise BelowMinOrderError(
                f"Order notional {notional} USDC < min {MIN_ORDER_USDC} USDC"
            )

        wallet = self.get_wallet(candidate_id)
        if wallet.usdc_balance < total_cost:
            raise InsufficientFundsError(
                f"Insufficient USDC for {symbol}: have {wallet.usdc_balance}, need {total_cost}"
            )

        wallet.usdc_balance = _money(wallet.usdc_balance - total_cost)

        existing = self.get_position(candidate_id, symbol)
        if existing is not None:
            new_quantity = existing.quantity + quantity
            cost_basis_existing = existing.quantity * existing.avg_cost_usdc
            cost_basis_new = notional
            new_avg_cost = (cost_basis_existing + cost_basis_new) / new_quantity
            existing.quantity = new_quantity
            existing.avg_cost_usdc = _money(new_avg_cost)
        else:
            position = Position(
                wallet_id=wallet.id,
                symbol=symbol,
                quantity=quantity,
                avg_cost_usdc=_money(price),
            )
            self.db.add(position)

        trade = Trade(
            candidate_id=candidate_id,
            side="BUY",
            symbol=symbol,
            quantity=quantity,
            price=_money(price),
            notional_usdc=notional,
            fee_usdc=fee,
            pnl_realized_usdc=None,
        )
        self.db.add(trade)
        self.db.flush()
        return trade

    def record_sell(
        self,
        candidate_id: int,
        symbol: str,
        quantity: Decimal,
        price: Decimal,
    ) -> Trade:
        """Enregistre une vente.

        Effets :
        - Diminue la quantité de la position (ou la supprime si vendue en totalité).
        - Augmente le solde USDC du net (notional - frais).
        - Insère un Trade côté SELL avec pnl_realized_usdc renseigné.
        """
        self._validate_quantity(quantity)
        self._validate_price(price)

        position = self.get_position(candidate_id, symbol)
        if position is None:
            raise InsufficientPositionError(f"No position on {symbol}")
        if position.quantity < quantity:
            raise InsufficientPositionError(
                f"Cannot sell {quantity} of {symbol}: have {position.quantity}"
            )

        notional = _money(quantity * price)
        fee = _money(notional * BINANCE_FEE_RATE)
        net_proceeds = notional - fee

        if notional < MIN_ORDER_USDC:
            raise BelowMinOrderError(
                f"Order notional {notional} USDC < min {MIN_ORDER_USDC} USDC"
            )

        cost_basis = _money(quantity * position.avg_cost_usdc)
        pnl_realized = _money(net_proceeds - cost_basis)

        wallet = self.get_wallet(candidate_id)
        wallet.usdc_balance = _money(wallet.usdc_balance + net_proceeds)

        if position.quantity == quantity:
            self.db.delete(position)
        else:
            position.quantity = position.quantity - quantity
            # avg_cost_usdc inchangé pour le reliquat

        trade = Trade(
            candidate_id=candidate_id,
            side="SELL",
            symbol=symbol,
            quantity=quantity,
            price=_money(price),
            notional_usdc=notional,
            fee_usdc=fee,
            pnl_realized_usdc=pnl_realized,
        )
        self.db.add(trade)
        self.db.flush()
        return trade

    # --------------------------------------------------------------- Reporting

    def portfolio_value(
        self, candidate_id: int, current_prices: dict[str, Decimal]
    ) -> Decimal:
        """Valeur marchande totale du portefeuille = USDC + somme des positions valorisées."""
        wallet = self.get_wallet(candidate_id)
        total = wallet.usdc_balance
        for position in wallet.positions:
            if position.symbol not in current_prices:
                raise LedgerError(f"Missing current price for {position.symbol}")
            total += position.quantity * current_prices[position.symbol]
        return _money(total)

    def realized_pnl(self, candidate_id: int) -> Decimal:
        """Somme des P&L réalisés sur les ventes."""
        result = self.db.execute(
            select(Trade).where(Trade.candidate_id == candidate_id, Trade.side == "SELL")
        ).scalars()
        total = Decimal("0")
        for trade in result:
            if trade.pnl_realized_usdc is not None:
                total += trade.pnl_realized_usdc
        return _money(total)

    def unrealized_pnl(
        self, candidate_id: int, current_prices: dict[str, Decimal]
    ) -> Decimal:
        """P&L latent = somme sur positions de (qte * (prix_courant - avg_cost))."""
        wallet = self.get_wallet(candidate_id)
        total = Decimal("0")
        for position in wallet.positions:
            if position.symbol not in current_prices:
                raise LedgerError(f"Missing current price for {position.symbol}")
            current_value = position.quantity * current_prices[position.symbol]
            cost_basis = position.quantity * position.avg_cost_usdc
            total += current_value - cost_basis
        return _money(total)

    def total_pnl(
        self, candidate_id: int, current_prices: dict[str, Decimal]
    ) -> Decimal:
        """Réalisé + latent."""
        return _money(
            self.realized_pnl(candidate_id) + self.unrealized_pnl(candidate_id, current_prices)
        )

    def can_afford(self, candidate_id: int, notional_usdc: Decimal) -> bool:
        """Vérifie si le wallet peut couvrir un ordre de ce notional (frais inclus)."""
        wallet = self.get_wallet(candidate_id)
        total_cost = _money(notional_usdc + notional_usdc * BINANCE_FEE_RATE)
        return wallet.usdc_balance >= total_cost

    # -------------------------------------------------------------- Validation

    @staticmethod
    def _validate_quantity(quantity: Decimal) -> None:
        if not isinstance(quantity, Decimal):
            raise InvalidQuantityError("quantity must be a Decimal")
        if quantity <= 0:
            raise InvalidQuantityError(f"quantity must be > 0, got {quantity}")

    @staticmethod
    def _validate_price(price: Decimal) -> None:
        if not isinstance(price, Decimal):
            raise InvalidPriceError("price must be a Decimal")
        if price <= 0:
            raise InvalidPriceError(f"price must be > 0, got {price}")
