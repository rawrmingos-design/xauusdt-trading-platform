"""Backtest engine that uses ConfluenceStrategy."""

from __future__ import annotations

from xauusdt.backtest.engine import BacktestEngine
from xauusdt.backtest.models import Signal
from xauusdt.exchange.models import Candle as CandleModel
from xauusdt.strategy.confluence import ConfluenceStrategy


class ConfluenceBacktestEngine(BacktestEngine):
    """Backtest engine that uses ConfluenceStrategy for signals."""

    def __init__(
        self,
        config: object,  # BacktestConfig
        candles: list[CandleModel],
        strategy: ConfluenceStrategy,
    ) -> None:
        super().__init__(config, candles)  # type: ignore[arg-type]
        self._strategy = strategy

    def _on_candle(self, candle: CandleModel, index: int) -> Signal:
        """Override to use ConfluenceStrategy signals."""
        return self._strategy.on_candle(candle, self._position)
