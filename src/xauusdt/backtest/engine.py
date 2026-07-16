"""Backtest engine core.

Iterates candles chronologically, calls strategy.on_candle(),
simulates order execution, manages position lifecycle.
"""

from __future__ import annotations

import logging

from xauusdt.backtest.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestPosition,
    BacktestResult,
    BacktestTrade,
    Side,
    Signal,
)
from xauusdt.exchange.models import Candle

log = logging.getLogger(__name__)


class BacktestEngine:
    """Candle-by-candle backtest simulation engine."""

    def __init__(
        self,
        config: BacktestConfig,
        candles: list[Candle],
    ) -> None:
        self._config = config
        # Sort ascending by open_time (chronological)
        self._candles = sorted(candles, key=lambda c: c.open_time)
        self._balance = config.initial_balance
        self._position: BacktestPosition | None = None
        self._trades: list[BacktestTrade] = []
        self._equity_curve: list[float] = []
        self._peak_equity = config.initial_balance
        self._errors: list[str] = []

    def run(self) -> BacktestResult:
        """Run the backtest simulation."""
        if not self._candles:
            self._errors.append("No candles provided")
            return self._finalize()

        log.info("Backtest: %d candles, balance=%.2f", len(self._candles), self._balance)

        # First pass: precompute SL/TP for strategy if provided
        for i, candle in enumerate(self._candles):
            # Strategy callback is called during iteration
            pass

        # Main iteration
        for i, candle in enumerate(self._candles):
            try:
                self._process_candle(candle, i)
            except Exception:
                self._errors.append(
                    f"Error at candle {i} ({candle.open_time.isoformat()}): {self.__class__}"
                )
                log.exception("Engine error at candle %d", i)

        # Close any open position at end of data
        if self._position is not None:
            self._close_position(self._candles[-1], "EOL")

        return self._finalize()

    def _process_candle(self, candle: Candle, index: int) -> None:
        """Process a single candle: check SL/TP → strategy signal → execute."""
        # Update MFE/MAE excursions first for this candle
        if self._position is not None:
            self._position._update_excursions(candle)

        # 1. Check SL/TP first (conservative: SL before TP if both hit)
        if self._position is not None:
            # Check SL
            if self._position.is_sl_hit(candle):
                self._close_position(candle, "SL")
                return

            # Check Partial TP (PROJECT-STRATEGY-003)
            if self._position.is_partial_tp_hit(candle):
                self._close_position(candle, "PARTIAL_TP", partial=True)

            # Check Full TP (might be hit in the same candle as Partial TP!)
            if self._position is not None and self._position.is_tp_hit(candle):
                self._close_position(candle, "TP")
                return

        # 2. Call strategy
        signal = self._on_candle(candle, index)

        # 3. Execute signal
        if signal == Signal.BUY and self._position is None:
            self._open_position(candle, Side.LONG)
        elif signal == Signal.SELL and self._position is None:
            self._open_position(candle, Side.SHORT)
        elif (
            signal == Signal.SELL
            and self._position is not None
            and self._position.side == Side.LONG
        ):
            # Close long → open short (or just close long)
            self._close_position(candle, "SIGNAL")
        elif (
            signal == Signal.BUY
            and self._position is not None
            and self._position.side == Side.SHORT
        ):
            # Close short → open long
            self._close_position(candle, "SIGNAL")

        # Record equity
        self._equity_curve.append(self._current_equity(candle))

    def _on_candle(self, candle: Candle, index: int) -> Signal:
        """Called by engine — overridden by strategy injection."""
        raise NotImplementedError("Subclass must implement on_candle()")

    def _open_position(self, candle: Candle, side: Side) -> None:
        """Open a new position."""
        # Calculate quantity: use max_position_size_pct of balance
        position_value = self._balance * self._config.max_position_size_pct
        # For perpetuals, quantity = position_value / price (simplified, not using leverage)
        quantity = position_value / candle.close

        # Compute entry price with slippage
        entry_price = self._apply_slippage(candle.close, side)
        # Apply fee
        fee = entry_price * quantity * self._config.fee_rate

        self._balance -= fee

        # Compute SL/TP prices
        sl_price: float | None = None
        tp_price: float | None = None
        if self._config.stop_loss_pct > 0:
            if side == Side.LONG:
                sl_price = entry_price * (1 - self._config.stop_loss_pct)
            else:
                sl_price = entry_price * (1 + self._config.stop_loss_pct)
        if self._config.take_profit_pct > 0:
            if side == Side.LONG:
                tp_price = entry_price * (1 + self._config.take_profit_pct)
            else:
                tp_price = entry_price * (1 - self._config.take_profit_pct)

        self._position = BacktestPosition(
            side=side,
            entry_candle_time=candle.open_time.isoformat(),
            entry_price=entry_price,
            quantity=quantity,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
        )

        log.debug(
            "OPEN %s qty=%.2f @ %.2f SL=%.2f TP=%.2f",
            side.value,
            quantity,
            entry_price,
            sl_price,
            tp_price,
        )

    def _close_position(self, candle: Candle, reason: str, partial: bool = False) -> None:
        """Close current position (or partially close it) and record trade."""
        if self._position is None:
            return

        exit_price = self._apply_slippage(candle.close, self._position.side)

        # If partial close, we only close 50% of the position
        close_quantity = self._position.quantity
        if partial:
            close_quantity = self._position.quantity / 2.0

        fee = exit_price * close_quantity * self._config.fee_rate
        self._balance -= fee

        # Calculate PnL
        pnl: float
        if self._position.side == Side.LONG:
            pnl = (exit_price - self._position.entry_price) * close_quantity
        else:
            pnl = (self._position.entry_price - exit_price) * close_quantity

        pnl_pct = (
            pnl / self._position.entry_price / close_quantity * 100
            if self._position.entry_price > 0
            else 0
        )
        slippage_cost = abs(exit_price - candle.close) * close_quantity

        # Exit Model Diagnostics (BACKTEST-007)
        max_mfe = self._position.max_mfe_price
        max_mae = self._position.max_mae_price
        sl_dist = abs(self._position.entry_price - (self._position.stop_loss_price or self._position.entry_price))
        if sl_dist > 0:
            max_r = max_mfe / sl_dist
        else:
            max_r = 0.0

        max_mfe_pct = (max_mfe / self._position.entry_price) * 100 if self._position.entry_price > 0 else 0.0
        max_mae_pct = (max_mae / self._position.entry_price) * 100 if self._position.entry_price > 0 else 0.0
        atr_at_entry = getattr(candle, "_atr_at_entry", 0.0)

        # Exit Model Diagnostics (BACKTEST-007)
        max_mfe = self._position.max_mfe_price
        max_mae = self._position.max_mae_price
        sl_dist = abs(self._position.entry_price - (self._position.stop_loss_price or self._position.entry_price))
        if sl_dist > 0:
            max_r = max_mfe / sl_dist
        else:
            max_r = 0.0

        max_mfe_pct = (max_mfe / self._position.entry_price) * 100 if self._position.entry_price > 0 else 0.0
        max_mae_pct = (max_mae / self._position.entry_price) * 100 if self._position.entry_price > 0 else 0.0
        atr_at_entry = getattr(candle, "_atr_at_entry", 0.0)

        trade = BacktestTrade(
            entry_candle_time=self._position.entry_candle_time,
            entry_price=self._position.entry_price,
            exit_candle_time=candle.open_time.isoformat(),
            exit_price=exit_price,
            side=self._position.side.value,
            quantity=close_quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            fee=fee,
            slippage_cost=slippage_cost,
            exit_reason=reason,
            is_partial=partial,
            max_mfe=max_mfe,
            max_mfe_pct=max_mfe_pct,
            max_mae=max_mae,
            max_mae_pct=max_mae_pct,
            max_r=max_r,
            atr_at_entry=atr_at_entry,
            sl_distance=sl_dist,
        )
        self._trades.append(trade)
        self._balance += pnl  # Add PnL to balance

        if partial:
            # Mark as partially closed, reduce quantity
            self._position.is_partial_closed = True
            self._position.quantity -= close_quantity
            # Move SL to break-even
            self._position.stop_loss_price = self._position.entry_price
        else:
            self._position = None

        log.debug(
            "CLOSE %s reason=%s @ %.2f pnl=%.2f (%.2f%%)",
            trade.side,
            reason,
            exit_price,
            trade.pnl,
            trade.pnl_pct,
        )

    def _apply_slippage(self, price: float, side: Side) -> float:
        """Apply deterministic slippage."""
        bps = self._config.slippage_bps / 10000  # basis points to ratio
        if side == Side.LONG:
            return price * (1 + bps)  # Buy: worse price
        return price * (1 - bps)  # Sell: worse price

    def _current_equity(self, candle: Candle) -> float:
        """Current equity = balance + unrealized PnL."""
        equity = self._balance
        if self._position is not None:
            if self._position.side == Side.LONG:
                unrealized = (candle.close - self._position.entry_price) * self._position.quantity
            else:
                unrealized = (self._position.entry_price - candle.close) * self._position.quantity
            equity += unrealized
        return equity

    def _finalize(self) -> BacktestResult:
        """Compute metrics and return result."""
        self._peak_equity = max(self._equity_curve) if self._equity_curve else self._balance

        # Compute drawdown
        max_dd = 0.0
        max_dd_pct = 0.0
        peak = self._balance
        for eq in self._equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd / peak if peak > 0 else 0

        # Compute trade stats
        total_trades = len(self._trades)
        win_trades = sum(1 for t in self._trades if t.pnl > 0)
        loss_trades = sum(1 for t in self._trades if t.pnl <= 0)
        win_rate = win_trades / total_trades if total_trades > 0 else 0.0

        # Profit factor
        gross_profit = sum(t.gross_pnl for t in self._trades if t.gross_pnl > 0)
        gross_loss = abs(sum(t.gross_pnl for t in self._trades if t.gross_pnl < 0))
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0
            else float("inf")
            if gross_profit > 0
            else 0.0
        )

        # Expectancy
        net_pnl = self._balance - self._config.initial_balance
        expectancy = net_pnl / total_trades if total_trades > 0 else 0.0

        metrics = BacktestMetrics(
            initial_balance=self._config.initial_balance,
            final_balance=self._balance,
            net_pnl=net_pnl,
            total_trades=total_trades,
            win_trades=win_trades,
            loss_trades=loss_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            expectancy=expectancy,
            trade_list=self._trades,
        )

        return BacktestResult(
            config=self._config,
            candles_count=len(self._candles),
            metrics=metrics,
            trades=self._trades,
            errors=self._errors,
        )
