"""Suite de tests du Ledger.

Couvre :
- Création de candidat et wallet
- Achats : impact USDC, position, frais, minimum d'ordre, fonds insuffisants
- Coût moyen pondéré sur achats successifs
- Ventes : impact USDC, position, P&L réalisé profit / perte, vente partielle
- Vente totale = suppression de la position
- Vente sans position / supérieure à la position
- Valorisation portefeuille
- P&L réalisé / latent / total
- Précision Decimal (pas de drift float)
- Validation des entrées (Decimal obligatoire, > 0)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import BINANCE_FEE_RATE
from app.models.ledger_models import Candidate
from app.services.ledger import (
    BelowMinOrderError,
    CandidateNotFoundError,
    InsufficientFundsError,
    InsufficientPositionError,
    InvalidPriceError,
    InvalidQuantityError,
    Ledger,
    LedgerError,
)


# ============================================================================
# Création de candidats
# ============================================================================


class TestCandidateCreation:
    def test_create_candidate_basic(self, ledger: Ledger) -> None:
        candidate = ledger.create_candidate(
            name="Claude", model="claude-opus-4-7", initial_balance_usdc=Decimal("10")
        )
        assert candidate.id is not None
        assert candidate.name == "Claude"
        assert candidate.model == "claude-opus-4-7"
        assert candidate.kind == "individual"
        assert candidate.is_active is True
        assert candidate.wallet is not None
        assert candidate.wallet.usdc_balance == Decimal("10")
        assert candidate.wallet.initial_balance == Decimal("10")

    def test_create_candidate_kinds(self, ledger: Ledger) -> None:
        ledger.create_candidate("Team", "multi", Decimal("10"), kind="team")
        ledger.create_candidate("HODL_BTC", "passive", Decimal("10"), kind="ghost")
        # pas d'exception levée = OK

    def test_negative_initial_balance_rejected(self, ledger: Ledger) -> None:
        with pytest.raises(LedgerError):
            ledger.create_candidate("Bad", "x", Decimal("-1"))

    def test_get_wallet_unknown_candidate(self, ledger: Ledger) -> None:
        with pytest.raises(CandidateNotFoundError):
            ledger.get_wallet(99999)


# ============================================================================
# Achats
# ============================================================================


class TestBuy:
    def test_buy_basic_decreases_usdc(self, ledger: Ledger, claude: Candidate) -> None:
        # BTC à 100 USDC, on achète 0.05 BTC = 5 USDC notional
        trade = ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        assert trade.side == "BUY"
        assert trade.symbol == "BTCUSDC"
        assert trade.quantity == Decimal("0.05")
        assert trade.notional_usdc == Decimal("5")
        # 5 USDC notional * 0.001 = 0.005 USDC de frais
        assert trade.fee_usdc == Decimal("0.005")
        # Wallet : 10 - 5 - 0.005 = 4.995
        wallet = ledger.get_wallet(claude.id)
        assert wallet.usdc_balance == Decimal("4.995")

    def test_buy_creates_position(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        position = ledger.get_position(claude.id, "BTCUSDC")
        assert position is not None
        assert position.quantity == Decimal("0.05")
        assert position.avg_cost_usdc == Decimal("100")

    def test_buy_below_min_order_raises(self, ledger: Ledger, claude: Candidate) -> None:
        # 0.04 BTC * 100 USDC = 4 USDC < 5 USDC min
        with pytest.raises(BelowMinOrderError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.04"), Decimal("100"))

    def test_buy_at_exactly_min_order_ok(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))

    def test_buy_insufficient_funds_raises(self, ledger: Ledger, claude: Candidate) -> None:
        # Tentative d'acheter 11 USDC de BTC avec un wallet de 10
        with pytest.raises(InsufficientFundsError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.11"), Decimal("100"))

    def test_buy_insufficient_for_fees_only(self, ledger: Ledger, claude: Candidate) -> None:
        # 9.995 USDC d'achat → besoin de 9.995 + 0.009995 = 10.004995, on n'a que 10
        with pytest.raises(InsufficientFundsError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.09995"), Decimal("100"))

    def test_buy_invalid_quantity(self, ledger: Ledger, claude: Candidate) -> None:
        with pytest.raises(InvalidQuantityError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("0"), Decimal("100"))
        with pytest.raises(InvalidQuantityError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("-1"), Decimal("100"))

    def test_buy_invalid_price(self, ledger: Ledger, claude: Candidate) -> None:
        with pytest.raises(InvalidPriceError):
            ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("0"))


class TestBuyAveraging:
    def test_two_buys_same_price_aggregate_qty(self, ledger: Ledger) -> None:
        c = ledger.create_candidate("Big", "x", Decimal("100"))
        ledger.record_buy(c.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        ledger.record_buy(c.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        pos = ledger.get_position(c.id, "BTCUSDC")
        assert pos is not None
        assert pos.quantity == Decimal("0.10")
        assert pos.avg_cost_usdc == Decimal("100")

    def test_two_buys_different_price_weighted_avg(self, ledger: Ledger) -> None:
        c = ledger.create_candidate("Big", "x", Decimal("200"))
        # Achat 1 : 1 unité @ 50 USDC
        ledger.record_buy(c.id, "ETHUSDC", Decimal("1"), Decimal("50"))
        # Achat 2 : 1 unité @ 60 USDC
        ledger.record_buy(c.id, "ETHUSDC", Decimal("1"), Decimal("60"))
        pos = ledger.get_position(c.id, "ETHUSDC")
        assert pos is not None
        assert pos.quantity == Decimal("2")
        # avg = (1*50 + 1*60) / 2 = 55
        assert pos.avg_cost_usdc == Decimal("55")


# ============================================================================
# Ventes
# ============================================================================


class TestSell:
    def test_sell_full_position_deletes_it(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        assert ledger.get_position(claude.id, "BTCUSDC") is None

    def test_sell_partial_keeps_position(self, ledger: Ledger) -> None:
        c = ledger.create_candidate("Big", "x", Decimal("100"))
        ledger.record_buy(c.id, "ETHUSDC", Decimal("2"), Decimal("10"))  # 20 USDC notional
        ledger.record_sell(c.id, "ETHUSDC", Decimal("1"), Decimal("10"))  # vente partielle
        pos = ledger.get_position(c.id, "ETHUSDC")
        assert pos is not None
        assert pos.quantity == Decimal("1")
        assert pos.avg_cost_usdc == Decimal("10")  # inchangé

    def test_sell_returns_usdc_minus_fee(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        balance_after_buy = ledger.get_wallet(claude.id).usdc_balance
        # Vente au même prix : on récupère 5 - 0.005 = 4.995 USDC
        ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        balance_after_sell = ledger.get_wallet(claude.id).usdc_balance
        assert balance_after_sell == balance_after_buy + Decimal("4.995")
        # Net total : 10 - 0.005 (buy fee) - 0.005 (sell fee) = 9.99
        assert balance_after_sell == Decimal("9.99")

    def test_sell_pnl_profit(self, ledger: Ledger, claude: Candidate) -> None:
        # Achat 0.05 BTC @ 100 = 5 USDC, vente @ 200 = 10 USDC notional
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        trade = ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("200"))
        # net_proceeds = 10 - 0.01 = 9.99 USDC
        # cost_basis = 0.05 * 100 = 5 USDC
        # pnl = 9.99 - 5 = 4.99
        assert trade.pnl_realized_usdc == Decimal("4.99")

    def test_sell_pnl_loss(self, ledger: Ledger) -> None:
        # Achat 0.1 BTC @ 100 = 10 USDC, vente @ 80 = 8 USDC notional (>= 5 min)
        c = ledger.create_candidate("Big", "x", Decimal("100"))
        ledger.record_buy(c.id, "BTCUSDC", Decimal("0.1"), Decimal("100"))
        trade = ledger.record_sell(c.id, "BTCUSDC", Decimal("0.1"), Decimal("80"))
        # net_proceeds = 0.1*80 - frais = 8 - 0.008 = 7.992
        # cost_basis = 10
        # pnl = 7.992 - 10 = -2.008
        assert trade.pnl_realized_usdc == Decimal("-2.008")

    def test_sell_no_position_raises(self, ledger: Ledger, claude: Candidate) -> None:
        with pytest.raises(InsufficientPositionError):
            ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))

    def test_sell_more_than_position_raises(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        with pytest.raises(InsufficientPositionError):
            ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.06"), Decimal("100"))

    def test_sell_below_min_order_raises(self, ledger: Ledger) -> None:
        c = ledger.create_candidate("Big", "x", Decimal("100"))
        ledger.record_buy(c.id, "BTCUSDC", Decimal("0.1"), Decimal("100"))
        # Vente de 0.04 BTC * 100 = 4 USDC < 5 min
        with pytest.raises(BelowMinOrderError):
            ledger.record_sell(c.id, "BTCUSDC", Decimal("0.04"), Decimal("100"))


# ============================================================================
# Portfolio value & P&L
# ============================================================================


class TestPortfolioValue:
    def test_only_usdc_no_positions(self, ledger: Ledger, claude: Candidate) -> None:
        assert ledger.portfolio_value(claude.id, {}) == Decimal("10")

    def test_with_position_at_unchanged_price(
        self, ledger: Ledger, claude: Candidate
    ) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        # 4.995 USDC + 0.05 BTC * 100 = 4.995 + 5 = 9.995
        value = ledger.portfolio_value(claude.id, {"BTCUSDC": Decimal("100")})
        assert value == Decimal("9.995")

    def test_with_position_at_higher_price(
        self, ledger: Ledger, claude: Candidate
    ) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        # BTC monte à 200 → position vaut 10, total = 4.995 + 10 = 14.995
        value = ledger.portfolio_value(claude.id, {"BTCUSDC": Decimal("200")})
        assert value == Decimal("14.995")

    def test_missing_price_raises(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        with pytest.raises(LedgerError):
            ledger.portfolio_value(claude.id, {})


class TestPnL:
    def test_realized_pnl_no_trades(self, ledger: Ledger, claude: Candidate) -> None:
        assert ledger.realized_pnl(claude.id) == Decimal("0")

    def test_realized_pnl_buy_only(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        # Pas de vente = pas de P&L réalisé
        assert ledger.realized_pnl(claude.id) == Decimal("0")

    def test_realized_pnl_after_profitable_sell(
        self, ledger: Ledger, claude: Candidate
    ) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        ledger.record_sell(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("200"))
        assert ledger.realized_pnl(claude.id) == Decimal("4.99")

    def test_unrealized_pnl_position_up(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        # BTC à 200 → unrealized = 0.05 * (200 - 100) = 5
        assert ledger.unrealized_pnl(claude.id, {"BTCUSDC": Decimal("200")}) == Decimal("5")

    def test_unrealized_pnl_position_down(self, ledger: Ledger, claude: Candidate) -> None:
        ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        assert ledger.unrealized_pnl(claude.id, {"BTCUSDC": Decimal("80")}) == Decimal("-1")

    def test_total_pnl(self, ledger: Ledger) -> None:
        c = ledger.create_candidate("Big", "x", Decimal("100"))
        # Achat 1 ETH @ 10
        ledger.record_buy(c.id, "ETHUSDC", Decimal("1"), Decimal("10"))
        # Achat 1 BTC @ 50
        ledger.record_buy(c.id, "BTCUSDC", Decimal("1"), Decimal("50"))
        # Vente ETH @ 20 (notional 20) — réalisé positif
        ledger.record_sell(c.id, "ETHUSDC", Decimal("1"), Decimal("20"))
        # BTC monte à 60 → latent = 1 * (60-50) = 10
        realized = ledger.realized_pnl(c.id)
        unrealized = ledger.unrealized_pnl(c.id, {"BTCUSDC": Decimal("60")})
        total = ledger.total_pnl(c.id, {"BTCUSDC": Decimal("60")})
        assert total == realized + unrealized


# ============================================================================
# can_afford
# ============================================================================


class TestCanAfford:
    def test_can_afford_yes(self, ledger: Ledger, claude: Candidate) -> None:
        assert ledger.can_afford(claude.id, Decimal("5")) is True

    def test_can_afford_no(self, ledger: Ledger, claude: Candidate) -> None:
        assert ledger.can_afford(claude.id, Decimal("11")) is False

    def test_can_afford_accounts_for_fees(self, ledger: Ledger, claude: Candidate) -> None:
        # Wallet : 10 USDC. Notional 9.995 + frais 0.009995 = 10.004995 > 10
        assert ledger.can_afford(claude.id, Decimal("9.995")) is False


# ============================================================================
# Précision Decimal
# ============================================================================


class TestPrecision:
    def test_no_float_drift_on_many_small_trades(self, ledger: Ledger) -> None:
        """Vérifie qu'on n'accumule pas d'erreur d'arrondi sur de nombreux trades."""
        c = ledger.create_candidate("Big", "x", Decimal("1000"))
        # 50 cycles achat/vente au même prix : on perd uniquement les frais
        for _ in range(50):
            ledger.record_buy(c.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
            ledger.record_sell(c.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        wallet = ledger.get_wallet(c.id)
        # Chaque cycle coûte 0.01 USDC (0.005 buy fee + 0.005 sell fee)
        # 50 cycles = 0.5 USDC de frais → reste 999.5
        assert wallet.usdc_balance == Decimal("999.5")

    def test_fee_calculation_matches_constant(
        self, ledger: Ledger, claude: Candidate
    ) -> None:
        trade = ledger.record_buy(claude.id, "BTCUSDC", Decimal("0.05"), Decimal("100"))
        # frais = notional * 0.001 = 5 * 0.001 = 0.005
        expected = Decimal("5") * BINANCE_FEE_RATE
        assert trade.fee_usdc == expected
