"""MT5-specific value types kept inside the adapter boundary.

These types are allowed to reference MetaTrader5 constants/names because
they never leave the ``execution/mt5`` package. Domain models in
``execution/models.py`` and ``execution/orders.py`` remain venue-free.

Trade return codes below follow the official MQL5 documentation
(ENUM_TRADE_RETCODE) — do NOT guess or invent codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TradeRetcode(Enum):
    """Official MQL5 trade return codes (ENUM_TRADE_RETCODE), documented subset.

    Reference: https://www.mql5.com/en/docs/constants/tradingconstants/enum_trade_retcode
    """

    REQUOTE = 10004  # Requote
    REJECT = 10005  # Request rejected
    CANCEL = 10006  # Request canceled by trader
    PLACED = 10007  # Order placed
    DONE = 10008  # Request completed (order executed)
    DONE_PARTIAL = 10009  # Only part of the request was completed
    ERROR = 10010  # Request processing error
    TIMEOUT = 10011  # Request canceled by timeout
    INVALID = 10012  # Invalid request
    INVALID_VOLUME = 10013  # Invalid volume in the request
    INVALID_PRICE = 10014  # Invalid price in the request
    INVALID_STOPS = 10015  # Invalid stops in the request
    TRADE_DISABLED = 10016  # Trade is disabled
    MARKET_CLOSED = 10017  # Market is closed
    NO_MONEY = 10018  # Not enough money to complete the request
    PRICE_CHANGED = 10019  # Prices changed
    PRICE_OFF = 10020  # No prices to process the request
    INVALID_EXPIRATION = 10021  # Invalid order expiration date
    ORDER_CHANGED = 10022  # Order state changed
    TOO_MANY_REQUESTS = 10023  # Too frequent requests
    NO_CHANGES = 10024  # No changes in the request
    SERVER_DISABLES_AT = 10025  # Autotrading disabled by server
    CLIENT_DISABLES_AT = 10026  # Autotrading disabled by client terminal
    LOCKED = 10027  # Request locked for processing
    FROZEN = 10028  # Order or position frozen
    INVALID_FILL = 10029  # Invalid order filling type
    INVALID_MODE = 10030  # No order execution mode
    INVALID_TYPE = 10031  # Invalid order type
    POSITION_CLOSED = 10032  # Position already closed
    INVALID_VOLUME_RANGE = 10033  # Invalid volume range
    INVALID_VOLUME_STEP = 10034  # Invalid volume step
    MARKET_CLOSED_WS = 10035  # Market is closed (WebSocket)
    NO_PROFIT = 10036  # Cannot change order
    NO_MONEY_MARGIN = 10037  # Not enough margin
    NO_CHANGES_WS = 10038  # No changes in the request (WebSocket)
    POSITION_LOCKED = 10039  # Position locked
    INVALID_ORDER_STATE = 10040  # Invalid order state
    INVALID_ORDER_TYPE = 10041  # Invalid order type
    INVALID_ORDER_FILLING = 10042  # Invalid order filling type
    INVALID_ORDER_TIME = 10043  # Invalid order time
    INVALID_ORDER_EXPIRATION = 10044  # Invalid order expiration date
    INVALID_ORDER_PRICE = 10045  # Invalid order price
    INVALID_ORDER_STOPS = 10046  # Invalid order stops
    UNKNOWN = 10047  # Unknown retcode


class RetcodeClass(Enum):
    """Classification of a trade retcode for adapter decision logic."""

    SUCCESS = "success"  # order executed / placed / done (partial ok)
    PARTIAL = "partial"  # partially filled — must reconcile
    RETRYABLE = "retryable"  # transient: requote, price changed, timeout
    REJECTED = "rejected"  # deterministic rejection: invalid request/volume/price...
    VENUE_ERROR = "venue_error"  # terminal/broker failure: disabled, frozen, locked
    UNKNOWN = "unknown"  # not classified — never treat as success


# Canonical classification table. Unknown codes fall back to UNKNOWN and are
# never treated as success.
_RETCODE_CLASS: dict[TradeRetcode, RetcodeClass] = {
    TradeRetcode.PLACED: RetcodeClass.SUCCESS,
    TradeRetcode.DONE: RetcodeClass.SUCCESS,
    TradeRetcode.DONE_PARTIAL: RetcodeClass.PARTIAL,
    # retryable — transient market conditions
    TradeRetcode.REQUOTE: RetcodeClass.RETRYABLE,
    TradeRetcode.PRICE_CHANGED: RetcodeClass.RETRYABLE,
    TradeRetcode.PRICE_OFF: RetcodeClass.RETRYABLE,
    TradeRetcode.TIMEOUT: RetcodeClass.RETRYABLE,
    # rejected — deterministic invalid requests
    TradeRetcode.REJECT: RetcodeClass.REJECTED,
    TradeRetcode.CANCEL: RetcodeClass.REJECTED,
    TradeRetcode.ERROR: RetcodeClass.REJECTED,
    TradeRetcode.INVALID: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_VOLUME: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_PRICE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_STOPS: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_EXPIRATION: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_FILL: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_MODE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_TYPE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_VOLUME_RANGE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_VOLUME_STEP: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_STATE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_TYPE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_FILLING: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_TIME: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_EXPIRATION: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_PRICE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER_STOPS: RetcodeClass.REJECTED,
    TradeRetcode.NO_MONEY: RetcodeClass.REJECTED,
    TradeRetcode.NO_MONEY_MARGIN: RetcodeClass.REJECTED,
    TradeRetcode.NO_PROFIT: RetcodeClass.REJECTED,
    # venue/broker failure — do not retry blindly
    TradeRetcode.TRADE_DISABLED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.MARKET_CLOSED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.MARKET_CLOSED_WS: RetcodeClass.VENUE_ERROR,
    TradeRetcode.SERVER_DISABLES_AT: RetcodeClass.VENUE_ERROR,
    TradeRetcode.CLIENT_DISABLES_AT: RetcodeClass.VENUE_ERROR,
    TradeRetcode.LOCKED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.FROZEN: RetcodeClass.VENUE_ERROR,
    TradeRetcode.POSITION_CLOSED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.POSITION_LOCKED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.TOO_MANY_REQUESTS: RetcodeClass.RETRYABLE,
    TradeRetcode.NO_CHANGES: RetcodeClass.REJECTED,
    TradeRetcode.NO_CHANGES_WS: RetcodeClass.REJECTED,
    TradeRetcode.ORDER_CHANGED: RetcodeClass.RETRYABLE,
}


def classify_retcode(retcode: int | TradeRetcode) -> RetcodeClass:
    """Classify a trade retcode; unknown codes are never success."""
    try:
        rc = retcode if isinstance(retcode, TradeRetcode) else TradeRetcode(retcode)
    except ValueError:
        return RetcodeClass.UNKNOWN
    return _RETCODE_CLASS.get(rc, RetcodeClass.UNKNOWN)


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
