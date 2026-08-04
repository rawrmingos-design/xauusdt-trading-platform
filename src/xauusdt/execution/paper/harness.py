"""Deterministic shadow/paper execution harness (PROJECT-PAPER-001).

Mirrors the backtest engine's execution semantics candle-by-candle:
  priority  SL -> partial TP -> TP -> strategy signal
  partial close 50% at 1R, move SL to entry (break-even),
  then final TP or SL on the remaining quantity.

Guards:
  - stale candle: block new entries when the candle is older than expected
  - candle gap:   block new entries when consecutive candles have a gap
  - single open position per symbol (no duplicate over-restart)

Continuous mode: state (open position + equity counters) is persisted and
restored across process restarts. Idempotency relies on candle timestamps: a
candle already fully processed (recorded to the positions/runs table) is
skipped on restart.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, cast

from xauusdt.backtest.models import BacktestPosition, Side, Signal
from xauusdt.exchange.models import Candle
from xauusdt.execution.paper.models import (
    PaperConfig,
    PaperRunResult,
    SimExit,
    SimExitReason,
    SimMode,
    SimOrder,
    SimPosition,
    SimSignal,
)
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy

log = logging.getLogger(__name__)

DEFAULT_SYMBOL = "XAU-USDT-SWAP"
DEFAULT_GRANULARITY = "15m"


def config_hash(cfg: ConfluenceConfig) -> str:
    """Deterministic sha256 of the full serialized config."""
    blob = json.dumps(cfg.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def commit_sha() -> str:
    """Return the current git commit SHA (best effort)."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _apply_slippage(price: float, side: Side, bps: float) -> float:
    ratio = bps / 10_000.0
    if side == Side.LONG:
        return price * (1 + ratio)  # buy worst
    return price * (1 - ratio)  # sell worst


class PaperHarness:
    """Candle-driven simulated execution harness for shadow/paper modes."""

    def __init__(
        self,
        strategy: ConfluenceStrategy,
        paper_cfg: PaperConfig | None = None,
    ) -> None:
        self._strategy = strategy
        self._cfg = paper_cfg or PaperConfig()
        self._position: SimPosition | None = None
        self._balance = self._cfg.initial_balance
        self._peak_balance = self._cfg.initial_balance
        self._max_drawdown = 0.0
        self._max_drawdown_pct = 0.0
        self._last_candle_time: datetime | None = None
        self._last_candle: Candle | None = None
        self._entry_blocked = False
        self._signals: list[SimSignal] = []
        self._orders: list[SimOrder] = []
        self._exits: list[SimExit] = []
        self._candles_processed = 0
        self._run_id = ""
        self._commit = "unknown"
        self._config_hash = ""

    # ------------------------------------------------------------------ runs

    def reset(self, run_id: str, commit: str) -> None:
        """Reset in-memory state for a new run."""
        self._position = None
        self._balance = self._cfg.initial_balance
        self._peak_balance = self._cfg.initial_balance
        self._max_drawdown = 0.0
        self._max_drawdown_pct = 0.0
        self._last_candle_time = None
        self._last_candle = None
        self._entry_blocked = False
        self._signals = []
        self._orders = []
        self._exits = []
        self._candles_processed = 0
        self._run_id = run_id
        self._commit = commit
        self._config_hash = config_hash(self._strategy._config)

    def run(self, candles: list[Candle]) -> PaperRunResult:
        """Execute all candles in chronological order. Returns aggregate result."""
        if not self._run_id:
            raise RuntimeError("call reset(run_id, commit) before run()")
        for c in sorted(candles, key=lambda x: x.open_time):
            self._process_candle(c)
        # close any open position at end of data (EOL), like the backtest engine
        if self._position is not None and self._last_candle is not None:
            self._close_position(self._last_candle, SimExitReason.EOL)
        return self._finish()

    def _process_candle(self, candle: Candle) -> None:
        """Process one candle: guards -> exits -> signal -> entry."""
        if self._candles_processed == 0:
            self._entry_blocked = False
        else:
            self._check_continuity(candle)
        self._last_candle_time = candle.open_time
        self._last_candle = candle

        # update excursions + exits (mirror backtest priority: SL -> partial -> TP)
        if self._position is not None:
            self._position.update_excursions(candle.high, candle.low)
            if self._position.is_sl_hit(candle.high, candle.low):
                self._close_position(candle, SimExitReason.SL)
                self._candles_processed += 1
                self._apply_equity(candle)
                return
            if self._position.is_partial_tp_hit(candle.high, candle.low):
                self._close_position(candle, SimExitReason.PARTIAL_TP, partial=True)
            if self._position is not None and self._position.is_tp_hit(candle.high, candle.low):
                self._close_position(candle, SimExitReason.TP)
                self._candles_processed += 1
                self._apply_equity(candle)
                return

        # strategy signal
        signal = self._strategy_signal(candle)

        # shadow mode: record signal without opening a position
        if self._cfg.mode == SimMode.SHADOW:
            self._record_signal(candle, signal, executed=False)
            self._candles_processed += 1
            self._apply_equity(candle)
            return

        # continuity guard: no new entries on stale/gapped candles
        if self._entry_blocked and self._position is None:
            self._record_signal(
                candle,
                signal,
                executed=False,
                extra_reasons=["continuity_guard"],
            )
            self._candles_processed += 1
            self._apply_equity(candle)
            return

        # paper mode: open/close per signal.
        # `executed` means the signal caused an actual order action (open or close).
        if signal == Signal.BUY and self._position is None:
            opened = self._open_position(candle, Side.LONG)
            self._record_signal(candle, signal, executed=opened)
        elif signal == Signal.SELL and self._position is None:
            opened = self._open_position(candle, Side.SHORT)
            self._record_signal(candle, signal, executed=opened)
        elif (
            signal == Signal.SELL
            and self._position is not None
            and self._position.side == Side.LONG
        ):
            self._close_position(candle, SimExitReason.SIGNAL)
            opened = self._open_position(candle, Side.SHORT)
            self._record_signal(candle, signal, executed=opened)
        elif (
            signal == Signal.BUY
            and self._position is not None
            and self._position.side == Side.SHORT
        ):
            self._close_position(candle, SimExitReason.SIGNAL)
            opened = self._open_position(candle, Side.LONG)
            self._record_signal(candle, signal, executed=opened)
        else:
            self._record_signal(candle, signal, executed=False)

        self._candles_processed += 1
        self._apply_equity(candle)

    def _strategy_signal(self, candle: Candle) -> Signal:
        """Call strategy.on_candle; SimPosition quacks like BacktestPosition."""
        pos = cast(BacktestPosition | None, self._position)
        return self._strategy.on_candle(candle, pos)

    # ------------------------------------------- continuous-mode interface

    def process_candle_continuous(self, candle: Candle) -> None:
        """Process one candle in continuous mode (no EOL close, no result build)."""
        self._process_candle(candle)

    def drain_batch(self) -> PaperRunResult | None:
        """Return records accumulated since the last drain (continuous mode).

        Returns a lightweight PaperRunResult containing only new signals,
        orders and exits; clears the internal lists so the next batch starts
        empty. Returns None when nothing was recorded.
        """
        if not (self._signals or self._orders or self._exits):
            return None
        result = PaperRunResult(
            run_id=self._run_id,
            mode=self._cfg.mode,
            strategy_version=self._strategy._config.version,
            config_hash=self._config_hash,
            commit_sha=self._commit,
            candles_processed=self._candles_processed,
            signals=list(self._signals),
            orders=list(self._orders),
            exits=list(self._exits),
            final_balance=self._balance,
            initial_balance=self._cfg.initial_balance,
            max_drawdown=self._max_drawdown,
            max_drawdown_pct=self._max_drawdown_pct,
        )
        self._signals = []
        self._orders = []
        self._exits = []
        return result

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def peak_balance(self) -> float:
        return self._peak_balance

    @property
    def max_drawdown(self) -> float:
        return self._max_drawdown

    @property
    def max_drawdown_pct(self) -> float:
        return self._max_drawdown_pct

    @property
    def candles_processed(self) -> int:
        return self._candles_processed

    @property
    def strategy_version(self) -> str:
        return self._strategy._config.version

    @property
    def config_hash(self) -> str:
        return self._config_hash

    @property
    def open_position(self) -> SimPosition | None:
        return self._position

    # ------------------------------------------------------------- positions

    def _open_position(self, candle: Candle, side: Side) -> bool:
        """Open a position. Returns True when a new position was opened."""
        if self._position is not None:
            return False
        position_value = self._balance * self._cfg.max_position_size_pct
        quantity = position_value / candle.close if candle.close > 0 else 0.0
        entry_price = _apply_slippage(candle.close, side, self._cfg.slippage_bps)
        fee = entry_price * quantity * self._cfg.fee_rate
        self._balance -= fee

        sl_price, tp_price, partial_tp = self._compute_prices(candle, side, entry_price)

        self._position = SimPosition(
            run_id=self._run_id,
            entry_candle_time=candle.open_time,
            entry_price=entry_price,
            side=side,
            quantity=quantity,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            partial_tp_price=partial_tp,
            partial_tp_ratio=self._strategy._config.partial_tp_ratio,
        )
        self._orders.append(
            SimOrder(
                run_id=self._run_id,
                candle_time=candle.open_time,
                symbol=candle.symbol,
                side=side,
                order_type="ENTRY",
                price=entry_price,
                raw_price=candle.close,
                quantity=quantity,
                fee=fee,
            )
        )
        log.debug(
            "OPEN %s qty=%.4f @ %.2f SL=%.2f TP=%.2f",
            side.value,
            quantity,
            entry_price,
            sl_price,
            tp_price,
        )
        return True

    def _compute_prices(
        self, candle: Candle, side: Side, entry_price: float
    ) -> tuple[float, float, float | None]:
        """ATR-based SL/TP mirrors ConfluenceBacktestEngine._open_position."""
        cfg = self._strategy._config
        sl_distance = 0.0
        features = self._strategy._compute_features()
        if features and features.atr_14.valid and features.atr_14.atr_value > 0:
            sl_distance = features.atr_14.atr_value * cfg.sl_atr_multiplier
            if side == Side.LONG:
                sl = entry_price - sl_distance
                tp = entry_price + sl_distance * cfg.risk_reward_ratio
            else:
                sl = entry_price + sl_distance
                tp = entry_price - sl_distance * cfg.risk_reward_ratio
        else:
            if side == Side.LONG:
                sl = entry_price * (1 - 0.02)
                tp = entry_price * (1 + 0.02 * cfg.risk_reward_ratio)
            else:
                sl = entry_price * (1 + 0.02)
                tp = entry_price * (1 - 0.02 * cfg.risk_reward_ratio)
        partial: float | None = None
        if cfg.improved_exit:
            dist = abs(entry_price - sl)
            if side == Side.LONG:
                partial = entry_price + dist
            else:
                partial = entry_price - dist
        return sl, tp, partial

    def _close_position(self, candle: Candle, reason: SimExitReason, partial: bool = False) -> None:
        if self._position is None:
            return
        pos = self._position
        exit_price = _apply_slippage(candle.close, pos.side, self._cfg.slippage_bps)
        close_qty = pos.quantity / 2.0 if partial else pos.quantity
        fee = exit_price * close_qty * self._cfg.fee_rate
        self._balance -= fee

        if pos.side == Side.LONG:
            pnl = (exit_price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - exit_price) * close_qty
        self._balance += pnl

        slippage_cost = abs(exit_price - candle.close) * close_qty
        sl_dist = abs(pos.entry_price - (pos.stop_loss_price or pos.entry_price))

        self._exits.append(
            SimExit(
                run_id=self._run_id,
                entry_candle_time=pos.entry_candle_time,
                exit_candle_time=candle.open_time,
                side=pos.side,
                exit_reason=reason,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=close_qty,
                pnl=pnl,
                fee=fee,
                is_partial=partial,
                slippage_cost=slippage_cost,
                sl_distance=sl_dist,
            )
        )
        self._orders.append(
            SimOrder(
                run_id=self._run_id,
                candle_time=candle.open_time,
                symbol=candle.symbol,
                side=pos.side,
                order_type="PARTIAL_EXIT" if partial else "EXIT",
                price=exit_price,
                raw_price=candle.close,
                quantity=close_qty,
                fee=fee,
            )
        )

        if partial:
            pos.is_partial_closed = True
            pos.quantity -= close_qty
            pos.stop_loss_price = pos.entry_price  # break-even
        else:
            self._position = None
        log.debug(
            "CLOSE %s reason=%s pnl=%.2f partial=%s",
            pos.side.value,
            reason.value,
            pnl,
            partial,
        )

    # ------------------------------------------------------------- signals

    def _record_signal(
        self,
        candle: Candle,
        signal: Signal,
        executed: bool,
        extra_reasons: list[str] | None = None,
    ) -> None:
        cfg = self._strategy._config
        score = self._strategy.get_last_score()
        regime = None
        try:
            regime = self._strategy.get_last_regime()
        except Exception:
            regime = None
        # BUY/SELL are actionable signals; `executed` marks whether the signal
        # caused an order action (paper mode). A HOLD is "rejected" only when the
        # strategy actively rejected an entry candidate (v3 filters populate
        # rejection reasons). Plain HOLD (no reasons) is a non-candidate.
        reasons = list(self._strategy.get_last_rejection_reasons())
        if extra_reasons:
            reasons.extend(extra_reasons)
        rejected = signal == Signal.HOLD and bool(reasons)
        self._signals.append(
            SimSignal(
                run_id=self._run_id,
                candle_time=candle.open_time,
                signal_type=signal.value,
                side=self._signal_side(signal),
                executed=executed,
                buy_score=score.buy_score,
                sell_score=score.sell_score,
                entry_price=candle.close,
                stop_loss_price=0.0,
                take_profit_price=0.0,
                partial_tp_price=0.0,
                context_adx=0.0,
                context_ema_trend="",
                context_structure=regime.label.value if regime else "",
                context_conflict=False,
                strategy_version=cfg.version,
                config_hash=self._config_hash,
                commit_sha=self._commit,
                rejected=rejected,
                rejection_reasons=reasons,
            )
        )

    @staticmethod
    def _signal_side(signal: Signal) -> Side:
        if signal == Signal.BUY:
            return Side.LONG
        if signal == Signal.SELL:
            return Side.SHORT
        return Side.LONG  # HOLD recorded as LONG/no-op for completeness

    # ----------------------------------------------------------------- guards

    def _check_continuity(self, candle: Candle) -> None:
        """Block new entries on stale or gapped candles (existing position honored)."""
        assert self._last_candle_time is not None
        delta = candle.open_time - self._last_candle_time
        stale = delta > timedelta(seconds=self._cfg.stale_candle_seconds)
        gap = delta > timedelta(seconds=self._cfg.max_candle_gap_seconds)
        self._entry_blocked = stale or gap
        if self._entry_blocked:
            log.warning(
                "Continuity guard on %s: delta=%s stale=%s gap=%s",
                candle.open_time.isoformat(),
                delta,
                stale,
                gap,
            )

    # ------------------------------------------------------------- equity

    def _apply_equity(self, candle: Candle) -> None:
        equity = self._balance
        if self._position is not None:
            if self._position.side == Side.LONG:
                equity += (candle.close - self._position.entry_price) * self._position.quantity
            else:
                equity += (self._position.entry_price - candle.close) * self._position.quantity
        if equity > self._peak_balance:
            self._peak_balance = equity
        dd = self._peak_balance - equity
        if dd > self._max_drawdown:
            self._max_drawdown = dd
            if self._peak_balance > 0:
                self._max_drawdown_pct = dd / self._peak_balance

    # --------------------------------------------------------------- final

    def _finish(self) -> PaperRunResult:
        return PaperRunResult(
            run_id=self._run_id,
            mode=self._cfg.mode,
            strategy_version=self._strategy._config.version,
            config_hash=self._config_hash,
            commit_sha=self._commit,
            candles_processed=self._candles_processed,
            signals=self._signals,
            orders=self._orders,
            exits=self._exits,
            final_balance=self._balance,
            initial_balance=self._cfg.initial_balance,
            max_drawdown=self._max_drawdown,
            max_drawdown_pct=self._max_drawdown_pct,
        )

    # ---------------------------------------------------- continuous state

    def harness_state(self, symbol: str = "XAU-USDT-SWAP") -> dict[str, Any]:
        """Serialize live state (equity counters + open position if any).

        Always returns a dict: equity counters are always persisted; position
        fields are present when a position is open (side=null otherwise).
        """
        base: dict[str, Any] = {
            "run_id": self._run_id,
            "symbol": symbol,
            "balance_snapshot": self._balance,
            "peak_balance_snapshot": self._peak_balance,
            "max_drawdown_snapshot": self._max_drawdown,
            "candles_processed": self._candles_processed,
            "last_processed_candle": (
                self._last_candle.open_time.isoformat() if self._last_candle else None
            ),
        }
        if self._position is None:
            base["side"] = None
            return base
        base.update(
            {
                "entry_candle_time": self._position.entry_candle_time.isoformat(),
                "entry_price": self._position.entry_price,
                "side": self._position.side.value,
                "quantity": self._position.quantity,
                "stop_loss_price": self._position.stop_loss_price,
                "take_profit_price": self._position.take_profit_price,
                "partial_tp_price": (
                    self._position.partial_tp_price
                    if self._position.partial_tp_price is not None
                    else None
                ),
                "partial_tp_ratio": self._position.partial_tp_ratio,
                "is_partial_closed": self._position.is_partial_closed,
                "max_mfe_price": self._position.max_mfe_price,
                "max_mae_price": self._position.max_mae_price,
            }
        )
        return base

    def restore_state(self, state: dict[str, Any] | None) -> None:
        """Restore open position + equity counters from a persisted snapshot."""
        if not state:
            return
        self._balance = float(state.get("balance_snapshot", self._balance))
        self._peak_balance = float(state.get("peak_balance_snapshot", self._peak_balance))
        self._max_drawdown = float(state.get("max_drawdown_snapshot", self._max_drawdown))
        self._candles_processed = int(state.get("candles_processed", 0))
        if not state.get("side"):
            return
        from datetime import datetime as _dt

        self._position = SimPosition(
            run_id=self._run_id,
            entry_candle_time=_dt.fromisoformat(state["entry_candle_time"]),
            entry_price=float(state["entry_price"]),
            side=Side(state["side"]),
            quantity=float(state["quantity"]),
            stop_loss_price=float(state["stop_loss_price"]),
            take_profit_price=float(state["take_profit_price"]),
            partial_tp_price=(
                float(state["partial_tp_price"]) if state.get("partial_tp_price") else None
            ),
            partial_tp_ratio=float(state["partial_tp_ratio"]),
            is_partial_closed=bool(state.get("is_partial_closed", 0)),
            max_mfe_price=float(state.get("max_mfe_price", 0.0)),
            max_mae_price=float(state.get("max_mae_price", 0.0)),
        )
        if state.get("last_processed_candle"):
            self._last_candle_time = _dt.fromisoformat(state["last_processed_candle"])
