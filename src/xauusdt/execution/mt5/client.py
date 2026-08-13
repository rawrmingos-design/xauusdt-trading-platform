"""Thin MT5 Python API wrapper (PROJECT-MT5-001).

The ONLY module in the codebase that imports ``MetaTrader5`` — and it does so
lazily, inside methods, so that importing this package never requires a
terminal and the rest of the platform (paper path, tests, forward OOS) runs
without the MT5 package installed.

Wraps the raw ``MetaTrader5`` module behind a small facade with explicit
error translation into :mod:`xauusdt.execution.errors`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from xauusdt.execution.errors import (
    DisconnectedError,
    InitializeError,
    SymbolUnavailableError,
    VenueUnavailableError,
)
from xauusdt.execution.mt5.config import Mt5Settings

log = logging.getLogger(__name__)

# Number of retries with backoff when the terminal is initializing.
_INIT_RETRIES = 5
_INIT_RETRY_DELAY_S = 2.0


class Mt5Client:
    """Facade over the MetaTrader5 module for this adapter's read path."""

    def __init__(self, settings: Mt5Settings) -> None:
        self._settings = settings
        self._mt5: Any = None  # the MetaTrader5 module (lazy)
        self._initialized = False

    # ------------------------------------------------------------------ init

    def _module(self) -> Any:
        """Import MetaTrader5 lazily; raise if the package is unavailable."""
        if self._mt5 is not None:
            return self._mt5
        try:
            import MetaTrader5 as mt5  # noqa: N813, E402, F401 - lazy import
        except ImportError as exc:
            raise VenueUnavailableError(
                "MetaTrader5 package is not installed; cannot connect to a terminal."
            ) from exc
        self._mt5 = mt5
        return mt5

    def connect(self) -> None:
        """Initialize the terminal connection with retry/backoff.

        Never touches a live account implicitly: mode is validated upstream by
        :meth:`Mt5Settings.ensure_demo_or_paper` before any write; connect
        itself is read-only.
        """
        mt5 = self._module()
        last_err: Exception | None = None
        for attempt in range(1, _INIT_RETRIES + 1):
            try:
                ok = mt5.initialize(
                    path=self._settings.terminal_path or None,
                    login=self._settings.login,
                    password=self._settings.password,
                    server=self._settings.server,
                )
            except Exception as exc:  # module raises on bad args
                last_err = exc
                ok = False
            if ok:
                self._initialized = True
                log.info(
                    "mt5_initialized server=%s login=%d mode=%s",
                    self._settings.server,
                    self._settings.login,
                    self._settings.mode,
                )
                return
            last_err = last_err or VenueUnavailableError(
                f"mt5.initialize failed (attempt {attempt})"
            )
            if attempt < _INIT_RETRIES:
                time.sleep(_INIT_RETRY_DELAY_S * attempt)
        raise InitializeError(f"MT5 terminal init failed: {last_err}")

    def disconnect(self) -> None:
        if self._initialized:
            self._module().shutdown()
            self._initialized = False
            log.info("mt5_disconnected")

    @property
    def connected(self) -> bool:
        if not self._initialized:
            return False
        try:
            return bool(self._module().terminal_info() is not None)
        except Exception:
            return False

    # ------------------------------------------------------------- read path

    def _last_error(self) -> str:
        try:
            err = self._module().last_error()
        except Exception:
            return "unknown"
        if not err:
            return ""
        code, detail = err if isinstance(err, (tuple, list)) else (0, str(err))
        return f"mt5_error={code} {detail}"

    def account_info(self) -> Any:
        info = self._module().account_info()
        if info is None:
            raise DisconnectedError(f"account_info() returned None. {self._last_error()}")
        return info

    def symbols_all(self) -> list[str]:
        syms = self._module().symbols_get()
        if syms is None:
            raise DisconnectedError(f"symbols_get() returned None. {self._last_error()}")
        return [s.name for s in syms if s is not None]

    def symbol_info(self, symbol: str) -> Any:
        info = self._module().symbol_info(symbol)
        if info is None:
            raise SymbolUnavailableError(f"symbol {symbol!r} not found. {self._last_error()}")
        return info

    def tick(self, symbol: str) -> Any:
        tick = self._module().symbol_info_tick(symbol)
        if tick is None:
            raise SymbolUnavailableError(f"no tick for {symbol!r}. {self._last_error()}")
        return tick

    def positions(self) -> list[Any]:
        pos = self._module().positions_get()
        if pos is None:
            raise DisconnectedError(f"positions_get() returned None. {self._last_error()}")
        return list(pos)

    def orders(self) -> list[Any]:
        orders = self._module().orders_get()
        if orders is None:
            raise DisconnectedError(f"orders_get() returned None. {self._last_error()}")
        return list(orders)

    def history(self, *, from_ms: int, to_ms: int) -> list[Any]:
        deals = self._module().history_deals_get(from_ms, to_ms)
        if deals is None:
            raise DisconnectedError(f"history_deals_get() returned None. {self._last_error()}")
        return list(deals)

    # ------------------------------------------------------------ write path
    # Phase 3 (DEMO ONLY). These methods drive the venue; mode guards are
    # enforced upstream by Mt5Settings / Mt5ExecutionAdapter before any call.

    def order_check(self, request: dict[str, Any]) -> Any:
        """Venue-side validation of a request (no state change)."""
        return self._module().order_check(request)

    def order_send(self, request: dict[str, Any]) -> Any:
        """Submit a trade request (single submission — caller handles retry)."""
        return self._module().order_send(request)

    def position_modify(self, *, ticket: int, sl: float, tp: float, symbol: str) -> Any:
        """Modify SL/TP of an open position."""
        return self._module().position_modify(ticket=ticket, symbol=symbol, sl=sl, tp=tp)

    def deals_by_comment(self, comment: str) -> list[Any]:
        """Fetch deals matching a comment (idempotency lookup §4.3.2)."""
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - 24 * 3600 * 1000
        deals = self._module().history_deals_get(from_ms, now_ms)
        if deals is None:
            raise DisconnectedError(f"history_deals_get() returned None. {self._last_error()}")
        return [d for d in deals if str(getattr(d, "comment", "") or "") == comment]
