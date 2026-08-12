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
from xauusdt.execution.mt5.config import AccountMode, ExecutionEnvironment, Mt5Settings
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
        self._env: ExecutionEnvironment | None = None
        # Mode guard: a live account may never be touched implicitly.
        self._settings.ensure_demo_or_paper("read")

    # ------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        self._client.connect()
        # Verify configured mode against broker-reported account mode.
        acc = self._client.account_info()
        env = ExecutionEnvironment(
            configured_mode=self._settings.mode,
            actual_account_mode=AccountMode.from_int(int(getattr(acc, "trade_mode", 0))),
        )
        env.allow_execution()
        self._env = env
        log.info(
            "mt5_adapter_connected mode=%s actual=%s",
            self._settings.mode,
            env.actual_name,
        )

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
        """Resolve the symbol to trade against the venue, fail-safe.

        Explicit symbol (``MT5_SYMBOL``):
          - query the venue for that exact symbol
          - verify it exists and is usable (trade mode != NO)
          - use it; otherwise raise SymbolUnavailableError

        Auto-discovery (empty symbol):
          - inspect all venue symbols
          - apply deterministic matching rules for XAUUSD/gold candidates
          - require EXACTLY ONE acceptable candidate
          - zero candidates -> fail; multiple candidates -> fail closed
          - never pick randomly / never pick the first match
        """
        if symbol:
            info = self._client.symbol_info(symbol)
            if info is None:
                raise SymbolUnavailableError(f"symbol {symbol!r} not found on venue")
            if str(getattr(info, "trade_mode", "FULL")) == "NO":
                raise SymbolUnavailableError(
                    f"symbol {symbol!r} exists but trading is disabled (trade_mode=NO)"
                )
            self._resolved_symbol = symbol
            return symbol

        candidates = self._discover_gold_candidates(self._client.symbols_all())
        if len(candidates) != 1:
            raise SymbolUnavailableError(
                f"gold symbol discovery failed: found {len(candidates)} candidates "
                f"{sorted(candidates)!r}; expected exactly one. "
                "Set MT5_SYMBOL explicitly to disambiguate."
            )
        self._resolved_symbol = candidates[0]
        log.info("mt5_symbol_resolved symbol=%s", candidates[0])
        return candidates[0]

    @staticmethod
    def _discover_gold_candidates(available: list[str]) -> list[str]:
        """Deterministic XAUUSD/gold candidate matching.

        Rules (applied in order, case-insensitive, exact on the base):
          1. Exact names: XAUUSD (plain), and explicit common variants that
             share the exact XAUUSD prefix (XAUUSDm, XAUUSDc, XAUUSD.pro...)
             are candidates.
          2. Any symbol whose base is exactly ``XAUUSD`` (prefix match on the
             first 6 chars) is a candidate.
          3. ``GOLD`` (exact, case-insensitive) is a candidate.
        Symbols are sorted for determinism before returning.
        """
        lower = {s.lower(): s for s in available}
        candidates: list[str] = []
        # exact gold base symbols
        for name in ("xauusd", "gold"):
            if name in lower:
                candidates.append(lower[name])
        # XAUUSD-prefixed variants (XAUUSDm, XAUUSDc, XAUUSD.pro, ...)
        for sym in available:
            if sym.lower().startswith("xauusd") and sym.lower() not in candidates:
                candidates.append(sym)
        # dedupe preserving order, sorted for determinism
        seen: set[str] = set()
        ordered: list[str] = []
        for c in sorted(candidates, key=str.lower):
            if c.lower() not in seen:
                seen.add(c.lower())
                ordered.append(c)
        return ordered

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
