"""Backtest engine that uses ConfluenceStrategy."""

from __future__ import annotations

from xauusdt.backtest.engine import BacktestEngine
from xauusdt.backtest.models import BacktestPosition, Signal, Side
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

    def _open_position(self, candle: CandleModel, side: Side) -> None:
        """Override to use ConfluenceStrategy dynamic ATR-based SL/TP."""
        # Calculate quantity: use max_position_size_pct of balance
        position_value = self._balance * self._config.max_position_size_pct
        quantity = position_value / candle.close

        # Compute entry price with slippage
        entry_price = self._apply_slippage(candle.close, side)

        # Apply fee
        fee = entry_price * quantity * self._config.fee_rate
        self._balance -= fee

        # Calculate dynamic SL/TP based on ConfluenceStrategy config
        cfg = self._strategy._config
        features = self._strategy._compute_features()

        sl_price: float | None = None
        tp_price: float | None = None

        if features and features.atr_14.valid and features.atr_14.atr_value > 0:
            atr_value = features.atr_14.atr_value
            sl_distance = atr_value * cfg.sl_atr_multiplier
            
            if side == Side.LONG:
                sl_price = entry_price - sl_distance
                tp_price = entry_price + (sl_distance * cfg.risk_reward_ratio)
            else:
                sl_price = entry_price + sl_distance
                tp_price = entry_price - (sl_distance * cfg.risk_reward_ratio)
        else:
            # Fallback to static SL if ATR is not valid (e.g., warmup)
            if side == Side.LONG:
                sl_price = entry_price * (1 - 0.02)
                tp_price = entry_price * (1 + 0.02 * cfg.risk_reward_ratio)
            else:
                sl_price = entry_price * (1 + 0.02)
                tp_price = entry_price * (1 - 0.02 * cfg.risk_reward_ratio)

        self._position = BacktestPosition(
            side=side,
            entry_candle_time=candle.open_time.isoformat(),
            entry_price=entry_price,
            quantity=quantity,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
        )
