"""Connecteur Binance Spot.

Abstraction unifiée derrière laquelle on a :
- PaperBinance : exécution simulée en local (pour tests et pour la phase
  paper trading préalable au live). Maintient un carnet d'OCOs et déclenche
  les SL/TP dès qu'un appel `tick` pousse le prix dans la zone.
- LiveBinance : appels réels via python-binance (stub à compléter avec les
  vraies clés API + IP whitelist + spot only + withdrawal off).

Tous les ordres sont OCO (Stop-Loss + Take-Profit posés à l'ouverture).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Protocol, runtime_checkable

from app.config import BINANCE_FEE_RATE, MIN_ORDER_USDC, MONEY_QUANTUM


# =============================================================================
# Exceptions
# =============================================================================


class BinanceError(Exception):
    pass


class BinanceMinNotionalError(BinanceError):
    """Notional inférieur au minimum 5 USDC."""


class BinanceInsufficientBalanceError(BinanceError):
    """Le compte (ou le wallet virtuel) ne couvre pas l'ordre."""


class BinanceUnknownSymbolError(BinanceError):
    pass


# =============================================================================
# Types
# =============================================================================


@dataclass(slots=True)
class OrderFill:
    """Résultat d'un fill (achat ou clôture)."""

    client_order_id: str
    binance_order_id: str
    symbol: str
    side: str
    fill_price: Decimal
    fill_quantity: Decimal
    fee_usdc: Decimal
    filled_at: datetime


@dataclass(slots=True)
class OcoClosure:
    """Notification de clôture d'un OCO (SL ou TP déclenché)."""

    client_order_id: str
    symbol: str
    closed_by: str  # "stop_loss" ou "take_profit"
    fill_price: Decimal
    fill_quantity: Decimal
    fee_usdc: Decimal
    pnl_usdc: Decimal
    closed_at: datetime


# =============================================================================
# Protocol
# =============================================================================


@runtime_checkable
class BinanceConnector(Protocol):
    """Interface du connecteur Binance."""

    async def get_current_price(self, symbol: str) -> Decimal: ...

    async def open_oco_buy(
        self,
        symbol: str,
        quote_amount_usdc: Decimal,
        stop_loss_pct: Decimal,
        take_profit_pct: Decimal,
        client_order_id: str | None = None,
    ) -> tuple[OrderFill, str, str]:
        """Ouvre une position long avec OCO. Renvoie (fill_achat, sl_id, tp_id)."""
        ...

    async def close_position_market(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> OrderFill: ...

    async def poll_oco_closures(self) -> list[OcoClosure]:
        """Renvoie la liste des OCOs déclenchés depuis le dernier appel."""
        ...


# =============================================================================
# PaperBinance
# =============================================================================


def _q(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)


@dataclass(slots=True)
class _OpenOco:
    client_order_id: str
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    sl_order_id: str
    tp_order_id: str
    opened_at: datetime


@dataclass(slots=True)
class PaperBinance:
    """Binance simulé : prix injectés manuellement via `set_price()`.

    À chaque `set_price()`, on vérifie tous les OCOs ouverts : si le prix
    courant a franchi le SL ou le TP, on les ferme et on enfile une notification
    dans `_pending_closures`, retournée par `poll_oco_closures()`.
    """

    _prices: dict[str, Decimal] = field(default_factory=dict)
    _open_ocos: dict[str, _OpenOco] = field(default_factory=dict)
    _pending_closures: list[OcoClosure] = field(default_factory=list)
    name: str = "paper"

    # ---------------------------- Prix

    def set_price(self, symbol: str, price: Decimal) -> None:
        """Met à jour le prix d'un symbole et déclenche les OCOs concernés."""
        self._prices[symbol] = _q(price)
        self._check_ocos(symbol)

    async def get_current_price(self, symbol: str) -> Decimal:
        if symbol not in self._prices:
            raise BinanceUnknownSymbolError(f"No price set for {symbol}")
        return self._prices[symbol]

    # ---------------------------- Ouvertures

    async def open_oco_buy(
        self,
        symbol: str,
        quote_amount_usdc: Decimal,
        stop_loss_pct: Decimal,
        take_profit_pct: Decimal,
        client_order_id: str | None = None,
    ) -> tuple[OrderFill, str, str]:
        if quote_amount_usdc < MIN_ORDER_USDC:
            raise BinanceMinNotionalError(
                f"Notional {quote_amount_usdc} < min {MIN_ORDER_USDC}"
            )
        if symbol not in self._prices:
            raise BinanceUnknownSymbolError(f"No price set for {symbol}")
        price = self._prices[symbol]

        notional = _q(quote_amount_usdc)
        fee = _q(notional * BINANCE_FEE_RATE)
        # quantité achetée nette (les frais sont en USDC, donc la qty acquise
        # correspond bien au notional / prix complet)
        quantity = _q(notional / price)
        client_id = client_order_id or f"paper-{uuid.uuid4().hex[:12]}"
        binance_id = f"paper-{uuid.uuid4().hex[:10]}"

        sl_price = _q(price * (Decimal("1") - stop_loss_pct / Decimal("100")))
        tp_price = _q(price * (Decimal("1") + take_profit_pct / Decimal("100")))
        sl_id = f"sl-{uuid.uuid4().hex[:10]}"
        tp_id = f"tp-{uuid.uuid4().hex[:10]}"

        self._open_ocos[client_id] = _OpenOco(
            client_order_id=client_id,
            symbol=symbol,
            quantity=quantity,
            entry_price=price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
            opened_at=datetime.now(timezone.utc),
        )

        fill = OrderFill(
            client_order_id=client_id,
            binance_order_id=binance_id,
            symbol=symbol,
            side="BUY",
            fill_price=price,
            fill_quantity=quantity,
            fee_usdc=fee,
            filled_at=datetime.now(timezone.utc),
        )
        return fill, sl_id, tp_id

    # ---------------------------- Clôture manuelle

    async def close_position_market(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> OrderFill:
        if symbol not in self._prices:
            raise BinanceUnknownSymbolError(f"No price set for {symbol}")
        price = self._prices[symbol]
        notional = _q(quantity * price)
        fee = _q(notional * BINANCE_FEE_RATE)
        client_id = client_order_id or f"paper-{uuid.uuid4().hex[:12]}"
        binance_id = f"paper-{uuid.uuid4().hex[:10]}"

        # Si on clôture manuellement une position OCO existante, on l'annule.
        to_remove = [
            cid
            for cid, oco in self._open_ocos.items()
            if oco.symbol == symbol
        ]
        for cid in to_remove:
            del self._open_ocos[cid]

        return OrderFill(
            client_order_id=client_id,
            binance_order_id=binance_id,
            symbol=symbol,
            side="SELL",
            fill_price=price,
            fill_quantity=quantity,
            fee_usdc=fee,
            filled_at=datetime.now(timezone.utc),
        )

    # ---------------------------- OCO triggers

    async def poll_oco_closures(self) -> list[OcoClosure]:
        out = self._pending_closures
        self._pending_closures = []
        return out

    def _check_ocos(self, symbol: str) -> None:
        price = self._prices.get(symbol)
        if price is None:
            return
        to_close: list[tuple[_OpenOco, str]] = []
        for oco in self._open_ocos.values():
            if oco.symbol != symbol:
                continue
            if price <= oco.stop_loss_price:
                to_close.append((oco, "stop_loss"))
            elif price >= oco.take_profit_price:
                to_close.append((oco, "take_profit"))
        for oco, reason in to_close:
            exit_price = oco.stop_loss_price if reason == "stop_loss" else oco.take_profit_price
            notional = _q(oco.quantity * exit_price)
            fee = _q(notional * BINANCE_FEE_RATE)
            entry_cost = _q(oco.quantity * oco.entry_price)
            pnl = _q(notional - fee - entry_cost)
            self._pending_closures.append(
                OcoClosure(
                    client_order_id=oco.client_order_id,
                    symbol=oco.symbol,
                    closed_by=reason,
                    fill_price=exit_price,
                    fill_quantity=oco.quantity,
                    fee_usdc=fee,
                    pnl_usdc=pnl,
                    closed_at=datetime.now(timezone.utc),
                )
            )
            del self._open_ocos[oco.client_order_id]


# =============================================================================
# LiveBinance (stub)
# =============================================================================


class LiveBinance:
    """Stub d'intégration python-binance.

    À compléter avec les vraies clés et l'IP whitelist active. Toutes les
    opérations sont async via httpx ou via la lib python-binance en mode async.

    Pour la première phase paper trading, on n'utilise PAS cette classe.
    """

    name = "live"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        # TODO: instancier le client python-binance asynchrone, vérifier les
        # permissions de la clé au démarrage (spot only, no withdrawal), valider
        # que l'IP source est whitelistée. Fail-fast en cas de mauvaise config.

    async def get_current_price(self, symbol: str) -> Decimal:
        raise NotImplementedError("LiveBinance non encore implémenté")

    async def open_oco_buy(
        self,
        symbol: str,
        quote_amount_usdc: Decimal,
        stop_loss_pct: Decimal,
        take_profit_pct: Decimal,
        client_order_id: str | None = None,
    ) -> tuple[OrderFill, str, str]:
        raise NotImplementedError

    async def close_position_market(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> OrderFill:
        raise NotImplementedError

    async def poll_oco_closures(self) -> list[OcoClosure]:
        raise NotImplementedError
