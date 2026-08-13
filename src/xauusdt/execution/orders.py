"""Order intent and execution result models (PROJECT-MT5-001).

``OrderIntent`` is the immutable contract that flows from risk engine to the
execution adapter. Adapters translate it into venue requests; they must not
receive strategy internals.

``ExecutionResult`` is the adapter's reply: either a success with a venue
order id, or a structured rejection. Both paths are explicit so callers can
reconcile ambiguous broker responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from xauusdt.execution.models import OrderKind, OrderSide, OrderState


@dataclass(frozen=True)
class OrderIntent:
    """Fully validated order request produced by the risk layer.

    Fields are normalized domain values (no venue-specific units).
    """

    symbol: str
    side: OrderSide
    kind: OrderKind
    volume: float  # normalized to symbol's volume grid by risk/symbol info
    entry_price: float  # reference price (market = expected fill, limit/stop = trigger)
    stop_loss: float = 0.0  # 0 = none
    take_profit: float = 0.0  # 0 = none
    comment: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    client_order_id: str = ""  # idempotency key (empty = not tracked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "kind": self.kind.value,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "comment": self.comment,
            "created_at": self.created_at.isoformat(),
            "client_order_id": self.client_order_id,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an execution call against the venue."""

    ok: bool
    state: OrderState
    venue_order_id: str = ""
    message: str = ""
    rejection_code: str = ""  # structured code when rejected (e.g. "margin_insufficient")
    filled_price: float = 0.0
    filled_volume: float = 0.0
    filled_time: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def rejected(
        cls, code: str, message: str = "", state: OrderState = OrderState.REJECTED
    ) -> ExecutionResult:
        return cls(ok=False, state=state, rejection_code=code, message=message)

    @classmethod
    def accepted(
        cls,
        venue_order_id: str,
        state: OrderState,
        filled_price: float = 0.0,
        filled_volume: float = 0.0,
        message: str = "",
    ) -> ExecutionResult:
        return cls(
            ok=True,
            state=state,
            venue_order_id=venue_order_id,
            filled_price=filled_price,
            filled_volume=filled_volume,
            message=message,
        )
