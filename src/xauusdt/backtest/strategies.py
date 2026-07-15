"""Strategy protocol and example implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from xauusdt.backtest.models import BacktestPosition, Signal
from xauusdt.exchange.models import Candle

log = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base for backtest strategies."""

    @abstractmethod
    def on_candle(self, candle: Candle, position: BacktestPosition | None) -> Signal:
        """Called on each candle before execution.

        Args:
            candle: Current candle (open time is the candle's open).
            position: Currently open position, or None.

        Returns:
            Signal to execute for this candle.
        """


class AlwaysHold(BaseStrategy):
    """Do nothing strategy — baseline for zero-trade metrics."""

    def on_candle(self, candle: Candle, position: BacktestPosition | None) -> Signal:
        return Signal.HOLD


class SimpleMACrossover(BaseStrategy):
    """Simple moving average crossover strategy.

    When short MA crosses above long MA → BUY.
    When short MA crosses below long MA → SELL.
    Uses closing prices only (no lookahead bias — uses previous candle to determine crossover).
    """

    def __init__(self, short_window: int = 5, long_window: int = 20) -> None:
        self._short_window = short_window
        self._long_window = long_window
        self._prices: list[float] = []

    def on_candle(self, candle: Candle, position: BacktestPosition | None) -> Signal:
        self._prices.append(candle.close)
        if len(self._prices) < self._long_window + 1:
            return Signal.HOLD

        # Calculate SMAs from previous window (no lookahead)
        # Use prices[0:-1] for previous close, prices[1:] for current
        prev_prices = self._prices[:-1]
        curr_prices = self._prices

        prev_short = sum(prev_prices[-self._short_window :]) / self._short_window
        prev_long = sum(prev_prices[-self._long_window :]) / self._long_window
        curr_short = sum(curr_prices[-self._short_window :]) / self._short_window
        curr_long = sum(curr_prices[-self._long_window :]) / self._long_window

        # Crossover: short MA crossed above long MA
        if prev_short <= prev_long and curr_short > curr_long:
            return Signal.BUY
        # Crossunder: short MA crossed below long MA
        if prev_short >= prev_long and curr_short < curr_long:
            return Signal.SELL

        return Signal.HOLD


class SimpleSLTPStrategy(BaseStrategy):
    """Basic long-only strategy with fixed SL/TP.

    Buys on every 5th candle (deterministic),
    relies on engine SL/TP to close position.
    """

    def __init__(self, candle_interval: int = 5) -> None:
        self._interval = candle_interval
        self._counter = 0

    def on_candle(self, candle: Candle, position: BacktestPosition | None) -> Signal:
        self._counter += 1
        if position is None and self._counter % self._interval == 0:
            return Signal.BUY
        return Signal.HOLD
