"""Backtest domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from xauusdt.exchange.models import Candle

# ── Enums ──────────────────────────────────────────────────────────


class Signal(Enum):
    """Signal emitted by strategy on_candle()."""

    HOLD = "HOLD"
    BUY = "BUY"
    SELL = "SELL"


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(Enum):
    MARKET = "MARKET"
    SL = "STOP_LOSS"
    TP = "TAKE_PROFIT"


# ── Config ─────────────────────────────────────────────────────────


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    initial_balance: float = 10000.0
    fee_rate: float = 0.0006  # 0.06% maker/taker average
    slippage_bps: float = 5.0  # 5 basis points = 0.05%
    stop_loss_pct: float = 0.0  # 0 = disabled, e.g. 0.01 = 1%
    take_profit_pct: float = 0.0  # 0 = disabled
    max_position_size_pct: float = 1.0  # % of balance to use per trade

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ── Trade & Position ───────────────────────────────────────────────


@dataclass
class BacktestTrade:
    """A single completed trade."""

    entry_candle_time: str  # ISO 8601 UTC
    entry_price: float
    exit_candle_time: str
    exit_price: float
    side: str  # "LONG" or "SHORT"
    quantity: float
    pnl: float
    pnl_pct: float
    fee: float
    slippage_cost: float
    exit_reason: str  # "SL", "TP", "SIGNAL", "EOL"

    @property
    def gross_pnl(self) -> float:
        """PnL before fees and slippage."""
        if self.side == "LONG":
            return (self.exit_price - self.entry_price) * self.quantity
        return (self.entry_price - self.exit_price) * self.quantity

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestPosition:
    """Currently open position."""

    side: Side
    entry_candle_time: str
    entry_price: float
    quantity: float

    # SL/TP triggers (computed from entry_price)
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    def is_sl_hit(self, candle: Candle) -> bool:
        """Check if SL was hit within candle high/low range."""
        if self.stop_loss_price is None:
            return False
        if self.side == Side.LONG:
            return candle.low <= self.stop_loss_price
        return candle.high >= self.stop_loss_price

    def is_tp_hit(self, candle: Candle) -> bool:
        """Check if TP was hit within candle high/low range."""
        if self.take_profit_price is None:
            return False
        if self.side == Side.LONG:
            return candle.high >= self.take_profit_price
        return candle.low <= self.take_profit_price


# ── Result & Metrics ───────────────────────────────────────────────


@dataclass
class BacktestMetrics:
    """Summary metrics for a backtest run."""

    initial_balance: float
    final_balance: float
    net_pnl: float
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float  # ratio
    profit_factor: float  # gross_profit / gross_loss
    max_drawdown: float  # max peak-to-trough
    max_drawdown_pct: float
    expectancy: float  # avg pnl per trade
    sharpe_ratio: float = 0.0  # placeholder
    trade_list: list[BacktestTrade] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "net_pnl": round(self.net_pnl, 2),
            "total_trades": self.total_trades,
            "win_trades": self.win_trades,
            "loss_trades": self.loss_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "expectancy": round(self.expectancy, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
        }


@dataclass
class BacktestResult:
    """Full result of a backtest run."""

    config: BacktestConfig
    candles_count: int
    metrics: BacktestMetrics
    trades: list[BacktestTrade]
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "candles_count": self.candles_count,
            "metrics": self.metrics.to_dict(),
            "trades": [t.__dict__ for t in self.trades],
            "errors": self.errors,
        }
