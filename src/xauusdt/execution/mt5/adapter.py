"""MT5 execution adapter (PROJECT-MT5-001) — Phase 2: READ-ONLY.

Implements the domain :class:`ExecutionAdapter` over the thin
:class:`Mt5Client`. This phase deliberately supports NO order submission:
``check_order`` / ``place_order`` / ``modify_position`` / ``close_position``
inherit the ``NotImplementedError`` defaults from
:class:`AbstractExecutionAdapter`, so a read-only adapter can never place an
order even by mistake.

The write path (Phase 3) will add demo-only execution behind the same
interface, gated by ``Mt5Settings.ensure_demo_or_paper``.
"""

from __future__ import annotations

import logging

from xauusdt.execution.errors import (
    SymbolUnavailableError,
)
from xauusdt.execution.interface import AbstractExecutionAdapter
from xauusdt.execution.models import (
    AccountSnapshot,
    MarketTick,
    OrderState,
    PositionSnapshot,
    SymbolInfo,
)
from xauusdt.execution.mt5.client import Mt5Client
from xauusdt.execution.mt5.config import Mt5Settings
from xauusdt.execution.mt5.mapper import (
    account_to_domain,
    order_to_domain,
    position_to_domain,
    symbol_to_domain,
    tick_to_domain,
)

log = logging.getLogger(__name__)


class Mt5ExecutionAdapter(AbstractExecutionAdapter):
    """Read-only MT5 adapter. Write methods raise until Phase 3."""

    def __init__(self, settings: Mt5Settings) -> None:
        self._settings = settings
        self._client = Mt5Client(settings)
        self._resolved_symbol: str | None = None
        # Mode guard: a live account may never be touched implicitly.
        self._settings.ensure_demo_or_paper("read")

    # ------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        self._client.connect()
        log.info("mt5_adapter_connected mode=%s", self._settings.mode)

    def disconnect(self) -> None:
        self._client.disconnect()
        log.info("mt5_adapter_disconnected")

    @property
    def connected(self) -> bool:
        return self._client.connected

    # ------------------------------------------------------------- read path

    def account_info(self) -> AccountSnapshot:
        return account_to_domain(self._client.account_info())

    def resolve_symbol(self, symbol: str) -> str:
        """Resolve a configured/requested symbol against the venue.

        If ``symbol`` is empty, auto-discover the gold symbol (XAUUSD) from
        the venue's symbol list. Returns the venue's exact symbol name.
        """
        if symbol:
            info = self._client.symbol_info(symbol)
            if info is None:
                raise SymbolUnavailableError(f"symbol {symbol!r} not found on venue")
            self._resolved_symbol = symbol
            return symbol

        candidates = ["XAUUSD", "GOLD", "XAUUSDm", "XAUUSD.a"]
        available = set(self._client.symbols_all())
        for cand in candidates:
            if cand in available:
                self._resolved_symbol = cand
                log.info("mt5_symbol_resolved symbol=%s", cand)
                return cand
        raise SymbolUnavailableError(
            "no gold symbol (XAUUSD/GOLD/...) found; set MT5_SYMBOL explicitly"
        )

    def symbol_info(self, symbol: str) -> SymbolInfo:
        info = self._client.symbol_info(symbol)
        if info is None:
            raise SymbolUnavailableError(f"symbol {symbol!r} not found on venue")
        return symbol_to_domain(info)

    def tick(self, symbol: str) -> MarketTick:
        return tick_to_domain(self._client.tick(symbol))

    def positions(self) -> list[PositionSnapshot]:
        return [position_to_domain(p) for p in self._client.positions()]

    def orders(self) -> list[OrderState]:
        # Read path: expose venue pending orders as domain states.
        return [order_to_domain(o) for o in self._client.orders()]

    def history(self, limit: int = 100) -> list[OrderState]:
        # Phase 2: no local order history tracked yet.
        return []

    # ------------------------------------------------------------- write path
    # Deliberately NOT implemented in Phase 2: AbstractExecutionAdapter
    # raises NotImplementedError, so no order can be placed by accident.

    def reconcile(self) -> list[str]:
        # No local state to reconcile yet in Phase 2 (read-only).
        return []
