"""Venue-agnostic execution domain models (PROJECT-MT5-001).

These models are the ONLY execution types strategy/risk layers may touch.
They intentionally contain no venue-specific fields (no MqlTradeRequest,
no MT5 ticket ids beyond a plain string).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class OrderState(Enum):
    """Explicit order lifecycle state machine.

    CREATED → VALIDATED → RISK_APPROVED → SUBMITTING → SUBMITTED
    SUBMITTED → { REJECTED | PARTIALLY_FILLED | FILLED }
    FILLED → POSITION → CLOSED
    """

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    POSITION = "POSITION"
    CLOSED = "CLOSED"


# Allowed transitions per state (whitelist, not just a flat enum).
_ORDER_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATED, OrderState.REJECTED}),
    OrderState.VALIDATED: frozenset({OrderState.RISK_APPROVED, OrderState.REJECTED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.SUBMITTING, OrderState.REJECTED}),
    OrderState.SUBMITTING: frozenset({OrderState.SUBMITTED, OrderState.REJECTED}),
    OrderState.SUBMITTED: frozenset(
        {OrderState.REJECTED, OrderState.PARTIALLY_FILLED, OrderState.FILLED}
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.REJECTED, OrderState.PARTIALLY_FILLED}
    ),
    OrderState.FILLED: frozenset({OrderState.POSITION, OrderState.CLOSED}),
    OrderState.POSITION: frozenset({OrderState.CLOSED}),
    OrderState.REJECTED: frozenset(),
    OrderState.CLOSED: frozenset(),
}


def order_can_transition(current: OrderState, target: OrderState) -> bool:
    """Return True when ``target`` is a legal next state from ``current``."""
    return target in _ORDER_TRANSITIONS[current]


class OrderSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderKind(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass(frozen=True)
class MarketTick:
    """Normalized last tick for a symbol."""

    symbol: str
    bid: float
    ask: float
    time: datetime
    volume: float = 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class SymbolInfo:
    """Normalized symbol trading constraints (no hardcoded lot assumptions)."""

    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    trade_mode: str = "FULL"  # FULL | CLOSEDONLY | NO | LONGONLY | SHORTONLY
    stop_level: int = 0  # minimum stop distance in points (0 = none)
    spread: float = 0.0

    def normalize_volume(self, raw: float) -> float:
        """Floor a requested volume to the symbol's step grid."""
        if raw < self.volume_min:
            return self.volume_min
        if raw > self.volume_max:
            return self.volume_max
        steps = round((raw - self.volume_min) / self.volume_step)
        return round(self.volume_min + steps * self.volume_step, 8)


@dataclass(frozen=True)
class AccountSnapshot:
    """Normalized account state."""

    login: int
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    leverage: int
    mode: str  # DEMO | LIVE | PAPER (explicit, never inferred from balance)
    name: str = ""
    trade_allowed: bool = True


@dataclass(frozen=True)
class PositionSnapshot:
    """Normalized open position."""

    position_id: str
    symbol: str
    side: OrderSide
    volume: float
    open_price: float
    open_time: datetime
    sl: float = 0.0
    tp: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    profit: float = 0.0
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "volume": self.volume,
            "open_price": self.open_price,
            "open_time": self.open_time.isoformat(),
            "sl": self.sl,
            "tp": self.tp,
            "commission": self.commission,
            "swap": self.swap,
            "profit": self.profit,
            "comment": self.comment,
        }
