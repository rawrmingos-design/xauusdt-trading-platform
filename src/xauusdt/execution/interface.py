"""Execution abstraction (PROJECT-MT5-001).

Boundary rule: strategy and risk layers depend ONLY on this interface and
the domain models in ``execution/orders.py`` / ``execution/models.py``.
They must never import MetaTrader5 (or any venue-specific package).

Venue adapters (paper, MT5, future brokers) implement :class:`ExecutionAdapter`.
"""

from __future__ import annotations

from abc import ABC
from typing import Protocol, runtime_checkable

from xauusdt.execution.models import (
    AccountSnapshot,
    MarketTick,
    OrderState,
    PositionSnapshot,
    SymbolInfo,
)
from xauusdt.execution.orders import ExecutionResult, OrderIntent


@runtime_checkable
class ExecutionAdapter(Protocol):
    """Read/write boundary between domain logic and an execution venue.

    Read path (positions/orders/history) and write path
    (check_order/place_order/modify/close) are deliberately separate methods
    so callers can treat them independently.
    """

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    @property
    def connected(self) -> bool: ...

    # ------------------------------------------------------------- read path
    def account_info(self) -> AccountSnapshot: ...

    def resolve_symbol(self, symbol: str) -> str: ...

    def symbol_info(self, symbol: str) -> SymbolInfo: ...

    def tick(self, symbol: str) -> MarketTick: ...

    def positions(self) -> list[PositionSnapshot]: ...

    def orders(self) -> list[OrderState]: ...

    def history(self, limit: int = 100) -> list[OrderState]: ...

    # ------------------------------------------------------------- write path
    def check_order(self, intent: OrderIntent) -> ExecutionResult: ...

    def place_order(self, intent: OrderIntent) -> ExecutionResult: ...

    def modify_position(
        self, position_id: str, *, sl: float | None = None, tp: float | None = None
    ) -> ExecutionResult: ...

    def close_position(self, position_id: str) -> ExecutionResult: ...

    # ------------------------------------------------------------- reconcile
    def reconcile(self) -> list[str]:
        """Compare local execution state vs venue truth; return mismatch ids."""
        ...


class AbstractExecutionAdapter(ABC):
    """Convenience ABC base implementing the read-only defaults.

    Venue adapters can subclass this and override only what they support.
    Write-path methods raise ``NotImplementedError`` by default so a
    read-only adapter cannot silently place orders.
    """

    def connect(self) -> None:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        return False

    def account_info(self) -> AccountSnapshot:
        raise NotImplementedError

    def resolve_symbol(self, symbol: str) -> str:
        raise NotImplementedError

    def symbol_info(self, symbol: str) -> SymbolInfo:
        raise NotImplementedError

    def tick(self, symbol: str) -> MarketTick:
        raise NotImplementedError

    def positions(self) -> list[PositionSnapshot]:
        return []

    def orders(self) -> list[OrderState]:
        return []

    def history(self, limit: int = 100) -> list[OrderState]:
        return []

    def check_order(self, intent: OrderIntent) -> ExecutionResult:
        raise NotImplementedError

    def place_order(self, intent: OrderIntent) -> ExecutionResult:
        raise NotImplementedError

    def modify_position(
        self, position_id: str, *, sl: float | None = None, tp: float | None = None
    ) -> ExecutionResult:
        raise NotImplementedError

    def close_position(self, position_id: str) -> ExecutionResult:
        raise NotImplementedError

    def reconcile(self) -> list[str]:
        return []
