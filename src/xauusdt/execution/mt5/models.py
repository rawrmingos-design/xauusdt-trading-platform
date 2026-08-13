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
    """Official MQL5 trade return codes (ENUM_TRADE_RETCODE).

    Values verified against the official MQL5 documentation:
    https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes
    Do NOT guess or invent codes. Note the gaps (10005, 10037 are unused).
    """

    REQUOTE = 10004  # Requote
    REJECT = 10006  # Request rejected
    CANCEL = 10007  # Request canceled by trader
    PLACED = 10008  # Order placed
    DONE = 10009  # Request completed (order executed)
    DONE_PARTIAL = 10010  # Only part of the request was completed
    ERROR = 10011  # Request processing error
    TIMEOUT = 10012  # Request canceled by timeout
    INVALID = 10013  # Invalid request
    INVALID_VOLUME = 10014  # Invalid volume in the request
    INVALID_PRICE = 10015  # Invalid price in the request
    INVALID_STOPS = 10016  # Invalid stops in the request
    TRADE_DISABLED = 10017  # Trade is disabled
    MARKET_CLOSED = 10018  # Market is closed
    NO_MONEY = 10019  # Not enough money to complete the request
    PRICE_CHANGED = 10020  # Prices changed
    PRICE_OFF = 10021  # No prices to process the request
    INVALID_EXPIRATION = 10022  # Invalid order expiration date
    ORDER_CHANGED = 10023  # Order state changed
    TOO_MANY_REQUESTS = 10024  # Too frequent requests
    NO_CHANGES = 10025  # No changes in the request
    SERVER_DISABLES_AT = 10026  # Autotrading disabled by server
    CLIENT_DISABLES_AT = 10027  # Autotrading disabled by client terminal
    LOCKED = 10028  # Request locked for processing
    FROZEN = 10029  # Order or position frozen
    INVALID_FILL = 10030  # Invalid order filling type
    CONNECTION = 10031  # No connection with the trade server
    ONLY_REAL = 10032  # Operation is allowed only for live accounts
    LIMIT_ORDERS = 10033  # Number of pending orders has reached the limit
    LIMIT_VOLUME = 10034  # Volume of orders and positions has reached the limit
    INVALID_ORDER = 10035  # Incorrect or prohibited order type
    POSITION_CLOSED = 10036  # Position already closed
    INVALID_CLOSE_VOLUME = 10038  # Invalid close volume
    CLOSE_ORDER_EXIST = 10039  # A close order already exists
    LIMIT_POSITIONS = 10040  # Number of open positions has reached the limit
    REJECT_CANCEL = 10041  # Pending order activation rejected, order canceled
    LONG_ONLY = 10042  # Only long positions allowed
    SHORT_ONLY = 10043  # Only short positions allowed
    CLOSE_ONLY = 10044  # Position closing is allowed only
    FIFO_CLOSE = 10045  # Position may be closed only by FIFO rule
    HEDGE_PROHIBITED = 10046  # Hedging prohibited


class RetcodeClass(Enum):
    """Classification of a trade retcode for adapter decision logic."""

    SUCCESS = "success"  # order executed / placed / done (partial ok)
    PARTIAL = "partial"  # partially filled — must reconcile
    RETRYABLE = "retryable"  # transient: requote, price changed, timeout
    REJECTED = "rejected"  # deterministic rejection: invalid request/volume/price...
    VENUE_ERROR = "venue_error"  # terminal/broker failure: disabled, frozen, locked
    UNKNOWN = "unknown"  # not classified — never treat as success


# Canonical classification table. Unknown codes fall back to UNKNOWN and are
# never treated as success. Classification is conservative: only codes that
# are documented as transient market conditions are RETRYABLE; everything
# else is either SUCCESS/PARTIAL (execution outcome) or REJECTED/VENUE_ERROR.
_RETCODE_CLASS: dict[TradeRetcode, RetcodeClass] = {
    # success — order placed or fully executed
    TradeRetcode.PLACED: RetcodeClass.SUCCESS,  # pending order placed (not filled)
    TradeRetcode.DONE: RetcodeClass.SUCCESS,  # fully executed
    TradeRetcode.DONE_PARTIAL: RetcodeClass.PARTIAL,  # partially executed
    # retryable — documented transient market conditions only
    TradeRetcode.REQUOTE: RetcodeClass.RETRYABLE,
    TradeRetcode.PRICE_CHANGED: RetcodeClass.RETRYABLE,
    TradeRetcode.PRICE_OFF: RetcodeClass.RETRYABLE,
    TradeRetcode.TIMEOUT: RetcodeClass.RETRYABLE,
    TradeRetcode.ORDER_CHANGED: RetcodeClass.RETRYABLE,
    # rejected — deterministic invalid requests (do not retry same payload)
    TradeRetcode.REJECT: RetcodeClass.REJECTED,
    TradeRetcode.CANCEL: RetcodeClass.REJECTED,
    TradeRetcode.ERROR: RetcodeClass.REJECTED,
    TradeRetcode.INVALID: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_VOLUME: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_PRICE: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_STOPS: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_EXPIRATION: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_FILL: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_ORDER: RetcodeClass.REJECTED,
    TradeRetcode.INVALID_CLOSE_VOLUME: RetcodeClass.REJECTED,
    TradeRetcode.CLOSE_ORDER_EXIST: RetcodeClass.REJECTED,
    TradeRetcode.REJECT_CANCEL: RetcodeClass.REJECTED,
    TradeRetcode.LONG_ONLY: RetcodeClass.REJECTED,
    TradeRetcode.SHORT_ONLY: RetcodeClass.REJECTED,
    TradeRetcode.CLOSE_ONLY: RetcodeClass.REJECTED,
    TradeRetcode.FIFO_CLOSE: RetcodeClass.REJECTED,
    TradeRetcode.HEDGE_PROHIBITED: RetcodeClass.REJECTED,
    TradeRetcode.NO_MONEY: RetcodeClass.REJECTED,
    TradeRetcode.LIMIT_ORDERS: RetcodeClass.REJECTED,
    TradeRetcode.LIMIT_VOLUME: RetcodeClass.REJECTED,
    TradeRetcode.LIMIT_POSITIONS: RetcodeClass.REJECTED,
    TradeRetcode.NO_CHANGES: RetcodeClass.REJECTED,
    # venue/broker failure — do not retry blindly
    TradeRetcode.TRADE_DISABLED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.MARKET_CLOSED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.SERVER_DISABLES_AT: RetcodeClass.VENUE_ERROR,
    TradeRetcode.CLIENT_DISABLES_AT: RetcodeClass.VENUE_ERROR,
    TradeRetcode.LOCKED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.FROZEN: RetcodeClass.VENUE_ERROR,
    TradeRetcode.CONNECTION: RetcodeClass.VENUE_ERROR,
    TradeRetcode.POSITION_CLOSED: RetcodeClass.VENUE_ERROR,
    TradeRetcode.ONLY_REAL: RetcodeClass.VENUE_ERROR,  # wrong account type for op
    TradeRetcode.TOO_MANY_REQUESTS: RetcodeClass.RETRYABLE,
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
