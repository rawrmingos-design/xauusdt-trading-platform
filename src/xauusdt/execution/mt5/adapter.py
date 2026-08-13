"""MT5 execution adapter (PROJECT-MT5-002, Phase 3 — DEMO ONLY).

Implements the full :class:`ExecutionAdapter` contract over the thin
:class:`Mt5Client`, adding the Phase 3 write path behind the same interface:

  - submit()            — demo order submission (persist-before-send,
                          idempotency, bounded retry, SL/TP mandatory)
  - modify_sl_tp()      — SL/TP update of a position with our magic
  - close_position()    — full close (partial close not supported)
  - reconcile()         — intent store vs venue truth (deals > positions > orders)
  - startup_reconcile() — crash recovery: resolve intents + adopt positions
                          (strict triple-match: account + magic namespace + symbol)

There is NO live path. ``Mt5Settings.ensure_demo_or_paper`` + the runtime
``ExecutionEnvironment.allow_execution`` gate every write.
"""

from __future__ import annotations

import logging

from xauusdt.execution.errors import ModeGuardError, SymbolUnavailableError
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
from xauusdt.execution.mt5.reconcile import Mt5Reconciler, ReconcileReport
from xauusdt.execution.mt5.store import Mt5IntentStore
from xauusdt.execution.mt5.writer import Mt5Writer
from xauusdt.execution.orders import ExecutionResult, OrderIntent

log = logging.getLogger(__name__)


class Mt5ExecutionAdapter(AbstractExecutionAdapter):
    """Demo-only MT5 adapter with the full read + write path."""

    def __init__(self, settings: Mt5Settings, store: Mt5IntentStore | None = None) -> None:
        self._settings = settings
        self._client = Mt5Client(settings)
        self._resolved_symbol: str | None = None
        self._env: ExecutionEnvironment | None = None
        self._store = store or Mt5IntentStore(":memory:")
        self._reconciler = Mt5Reconciler(self._client)
        self._writer = Mt5Writer(
            client=self._client,
            store=self._store,
            settings=settings,
            mode_guard=self._write_guard,
        )
        # Mode guard: a live account may never be touched implicitly.
        self._settings.ensure_demo_or_paper("read")

    # ------------------------------------------------------------- lifecycle

    def connect(self) -> None:
        self._client.connect()
        acc = self._client.account_info()
        env = ExecutionEnvironment(
            configured_mode=self._settings.mode,
            actual_account_mode=AccountMode.from_int(int(getattr(acc, "trade_mode", 0))),
        )
        env.allow_execution()
        self._env = env
        # Reconcile namespace = magic range from the store.
        base, span = self._store.magic_namespace()
        if span > 0:
            self._reconciler.set_namespace(base, span)
        log.info(
            "mt5_adapter_connected mode=%s actual=%s",
            self._settings.mode,
            env.actual_name,
        )

    def disconnect(self) -> None:
        self._client.disconnect()
        self._store.close()
        log.info("mt5_adapter_disconnected")

    @property
    def connected(self) -> bool:
        return self._client.connected

    def _write_guard(self, operation: str) -> None:
        """Demo-only guard for every write path entry (§0)."""
        self._settings.ensure_demo_or_paper(operation)
        try:
            if self._env is None:
                raise ModeGuardError(f"{operation} refused: not connected")
            self._env.allow_execution()
        except ModeGuardError:
            raise
        except Exception as exc:
            raise ModeGuardError(
                f"{operation} refused: environment not verified demo ({exc})"
            ) from exc

    # ------------------------------------------------------------- read path

    def account_info(self) -> AccountSnapshot:
        return account_to_domain(self._client.account_info())

    def resolve_symbol(self, symbol: str) -> str:
        """Resolve the symbol to trade against the venue, fail-safe."""
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
        """Deterministic XAUUSD/gold candidate matching (fail-closed)."""
        lower = {s.lower(): s for s in available}
        candidates: list[str] = []
        for name in ("xauusd", "gold"):
            if name in lower:
                candidates.append(lower[name])
        for sym in available:
            if sym.lower().startswith("xauusd") and sym.lower() not in candidates:
                candidates.append(sym)
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
        return [order_to_domain(o) for o in self._client.orders()]

    def history(self, limit: int = 100) -> list[OrderState]:
        return []

    # ------------------------------------------------------------- write path

    def check_order(self, intent: OrderIntent) -> ExecutionResult:
        return self._writer.check_order(intent)

    def place_order(self, intent: OrderIntent) -> ExecutionResult:
        return self._writer.submit(intent)

    def modify_position(
        self, position_id: str, *, sl: float | None = None, tp: float | None = None
    ) -> ExecutionResult:
        return self._writer.modify_sl_tp(position_id, sl=sl, tp=tp)

    def close_position(self, position_id: str) -> ExecutionResult:
        return self._writer.close_position(position_id)

    # ------------------------------------------------------------- reconcile

    def reconcile(self) -> list[str]:
        """Reconcile active intents vs venue truth; return mismatch ids.

        Implements the interface contract (list of mismatched ids). The full
        report (resolved/unresolved/positions) is available via
        ``reconcile_report()``.
        """
        report = self.reconcile_report()
        return report.mismatches + list(report.unresolved)

    def reconcile_report(self) -> ReconcileReport:
        """Full reconcile pass: resolve active intents against venue truth."""
        intents = self._store.intents_in_states(
            [OrderState.SUBMITTING, OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED]
        )
        report = self._reconciler.reconcile(intents)
        self._apply_report(report)
        return report

    def startup_reconcile(self) -> ReconcileReport:
        """Crash recovery (§8.2): resolve intents + adopt open positions.

        Runs BEFORE any new intent is submitted. Adopts only positions that
        pass strict triple-match (account + magic namespace + symbol).
        """
        intents = self._store.intents_in_states(
            [
                OrderState.SUBMITTING,
                OrderState.SUBMITTED,
                OrderState.PARTIALLY_FILLED,
                OrderState.UNKNOWN_OUTCOME,
            ]
        )
        report = self._reconciler.reconcile(intents)
        self._apply_report(report)

        # Adopt open positions: strict triple-match (§8.2 step 4).
        adopted = self._adopt_positions()
        report.open_positions.extend(adopted)
        log.info(
            "mt5_startup_reconcile resolved=%d unresolved=%d adopted=%d",
            len(report.resolved),
            len(report.unresolved),
            len(adopted),
        )
        return report

    def _adopt_positions(self) -> list[str]:
        """Adopt open positions with our account + magic namespace + symbol."""
        adopted: list[str] = []
        try:
            acc = self._client.account_info()
            my_login = int(getattr(acc, "login", 0))
        except Exception:
            log.exception("adopt_positions_account_failed")
            return adopted
        base, span = self._store.magic_namespace()
        if span <= 0:
            return adopted
        allowed_symbols = {self._settings.symbol} if self._settings.symbol else set()
        for p in self._client.positions() or []:
            # 1. account identity
            if int(getattr(p, "login", 0) or 0) != my_login:
                continue
            # 2. magic namespace
            magic = int(getattr(p, "magic", 0) or 0)
            if not (base <= magic < base + span):
                continue
            # 3. symbol / environment
            sym = str(getattr(p, "symbol", "") or "")
            if allowed_symbols and sym not in allowed_symbols:
                continue
            adopted.append(str(getattr(p, "ticket", 0)))
            log.info(
                "mt5_adopted_position ticket=%s symbol=%s magic=%d",
                getattr(p, "ticket", 0),
                sym,
                magic,
            )
        return adopted

    def _apply_report(self, report: ReconcileReport) -> None:
        for rid, state in report.resolved.items():
            self._store.update_state(rid, state)
        for rid in report.unresolved:
            self._store.update_state(rid, OrderState.FAILED_UNKNOWN)
