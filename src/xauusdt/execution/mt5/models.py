"""MT5-specific value types kept inside the adapter boundary.

These types are allowed to reference MetaTrader5 constants/names because
they never leave the ``execution/mt5`` package. Domain models in
``execution/models.py`` and ``execution/orders.py`` remain venue-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Mt5Retcode:
    """Symbolic retcodes for order_check / order_send (subset used here)."""

    DONE = 10009
    PLACED = 10008
    REJECTED = 10004
    INVALID_REQUEST = 10014
    MARGIN_INSUFFICIENT = 10019
    TRADE_DISABLED = 10020
    MARKET_CLOSED = 10021
    PRICE_OFF = 10022
    INVALID_PRICE = 10027
    INVALID_VOLUME = 10031
    NO_MONEY = 10024


@dataclass(frozen=True)
class Mt5OrderRequest:
    """Direct MT5 order request payload (MqlTradeRequest subset)."""

    action: int
    symbol: str
    volume: float
    type: int
    price: float
    sl: float = 0.0
    tp: float = 0.0
    deviation: int = 20
    magic: int = 0
    comment: str = ""
    ticket: int = 0  # modify/close target

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "volume": self.volume,
            "type": self.type,
            "price": self.price,
            "sl": self.sl,
            "tp": self.tp,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": self.comment,
            "ticket": self.ticket,
        }
