"""Simulated execution models for PROJECT-PAPER-001 shadow/paper harness.

Mirrors the deterministic execution semantics of the backtest engine
(ATR-based SL, partial TP at 1R, break-even after partial, final TP/SL)
so replay-parity tests can compare paper execution against the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from xauusdt.backtest.models import Side


class SimMode(Enum):
    """Harness operation mode."""

    SHADOW = "shadow"  # record signals only, no simulated positions
    PAPER = "paper"  # full simulated position lifecycle


class SimExitReason(Enum):
    """Reason a simulated position was closed."""

    PARTIAL_TP = "PARTIAL_TP"
    TP = "TP"
    SL = "SL"
    SIGNAL = "SIGNAL"
    EOL = "EOL"


@dataclass
class PaperConfig:
    """Execution parameters shared by shadow and paper modes."""

    mode: SimMode = SimMode.PAPER
    initial_balance: float = 10_000.0
    fee_rate: float = 0.0005  # taker fee, matches backtest
    slippage_bps: float = 2.0  # deterministic slippage, matches backtest
    max_position_size_pct: float = 1.0  # fraction of balance per position
    max_open_positions: int = 1  # one XAU-USDT-SWAP position at a time
    stale_candle_seconds: int = 900  # 15m candle + tolerance
    max_candle_gap_seconds: int = 1800  # two consecutive 15m candles

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "initial_balance": self.initial_balance,
            "fee_rate": self.fee_rate,
            "slippage_bps": self.slippage_bps,
            "max_position_size_pct": self.max_position_size_pct,
            "max_open_positions": self.max_open_positions,
            "stale_candle_seconds": self.stale_candle_seconds,
            "max_candle_gap_seconds": self.max_candle_gap_seconds,
        }


@dataclass
class SimSignal:
    """A signal recorded by the harness (shadow mode records these only)."""

    run_id: str
    candle_time: datetime
    signal_type: str  # BUY | SELL | HOLD
    side: Side
    executed: bool  # True when the signal caused an order action (paper mode)
    buy_score: float
    sell_score: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    partial_tp_price: float
    context_adx: float
    context_ema_trend: str
    context_structure: str
    context_conflict: bool
    strategy_version: str
    config_hash: str
    commit_sha: str
    rejected: bool = False
    rejection_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "candle_time": self.candle_time.isoformat(),
            "signal_type": self.signal_type,
            "side": self.side.value,
            "executed": self.executed,
            "buy_score": self.buy_score,
            "sell_score": self.sell_score,
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "partial_tp_price": self.partial_tp_price,
            "context_adx": self.context_adx,
            "context_ema_trend": self.context_ema_trend,
            "context_structure": self.context_structure,
            "context_conflict": self.context_conflict,
            "strategy_version": self.strategy_version,
            "config_hash": self.config_hash,
            "commit_sha": self.commit_sha,
            "rejected": self.rejected,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass
class SimOrder:
    """A simulated order (entry or exit) with deterministic fills."""

    run_id: str
    candle_time: datetime
    symbol: str
    side: Side  # entry side
    order_type: str  # ENTRY | EXIT | PARTIAL_EXIT
    price: float  # fill price (slippage applied)
    raw_price: float  # market price before slippage
    quantity: float
    fee: float
    status: str = "FILLED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "candle_time": self.candle_time.isoformat(),
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type,
            "price": self.price,
            "raw_price": self.raw_price,
            "quantity": self.quantity,
            "fee": self.fee,
            "status": self.status,
        }


@dataclass
class SimPosition:
    """Open simulated position."""

    run_id: str
    entry_candle_time: datetime
    entry_price: float
    side: Side
    quantity: float  # remaining quantity
    stop_loss_price: float
    take_profit_price: float
    partial_tp_price: float | None  # None when improved_exit disabled
    partial_tp_ratio: float
    is_partial_closed: bool = False
    max_mfe_price: float = 0.0
    max_mae_price: float = 0.0

    def update_excursions(self, candle_high: float, candle_low: float) -> None:
        if self.side == Side.LONG:
            self.max_mfe_price = max(self.max_mfe_price, candle_high)
            self.max_mae_price = min(self.max_mae_price or candle_low, candle_low)
        else:
            self.max_mfe_price = max(self.max_mfe_price, candle_low)
            self.max_mae_price = min(self.max_mae_price or candle_high, candle_high)

    def is_sl_hit(self, candle_high: float, candle_low: float) -> bool:
        if self.side == Side.LONG:
            return candle_low <= self.stop_loss_price
        return candle_high >= self.stop_loss_price

    def is_partial_tp_hit(self, candle_high: float, candle_low: float) -> bool:
        if self.is_partial_closed or self.partial_tp_price is None:
            return False
        if self.side == Side.LONG:
            return candle_high >= self.partial_tp_price
        return candle_low <= self.partial_tp_price

    def is_tp_hit(self, candle_high: float, candle_low: float) -> bool:
        if self.side == Side.LONG:
            return candle_high >= self.take_profit_price
        return candle_low <= self.take_profit_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entry_candle_time": self.entry_candle_time.isoformat(),
            "entry_price": self.entry_price,
            "side": self.side.value,
            "quantity": self.quantity,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "partial_tp_price": self.partial_tp_price,
            "partial_tp_ratio": self.partial_tp_ratio,
            "is_partial_closed": self.is_partial_closed,
            "max_mfe_price": self.max_mfe_price,
            "max_mae_price": self.max_mae_price,
        }


@dataclass
class SimExit:
    """Record of a closed simulated position leg."""

    run_id: str
    entry_candle_time: datetime
    exit_candle_time: datetime
    side: Side
    exit_reason: SimExitReason
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    fee: float
    is_partial: bool
    slippage_cost: float
    sl_distance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "entry_candle_time": self.entry_candle_time.isoformat(),
            "exit_candle_time": self.exit_candle_time.isoformat(),
            "side": self.side.value,
            "exit_reason": self.exit_reason.value,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "pnl": self.pnl,
            "fee": self.fee,
            "is_partial": self.is_partial,
            "slippage_cost": self.slippage_cost,
            "sl_distance": self.sl_distance,
        }


@dataclass
class PaperRunResult:
    """Aggregate result of one harness run."""

    run_id: str
    mode: SimMode
    strategy_version: str
    config_hash: str
    commit_sha: str
    candles_processed: int
    signals: list[SimSignal]
    orders: list[SimOrder]
    exits: list[SimExit]
    final_balance: float
    initial_balance: float
    max_drawdown: float
    max_drawdown_pct: float

    @property
    def total_pnl(self) -> float:
        return self.final_balance - self.initial_balance

    @property
    def trade_count(self) -> int:
        return len(self.exits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "strategy_version": self.strategy_version,
            "config_hash": self.config_hash,
            "commit_sha": self.commit_sha,
            "candles_processed": self.candles_processed,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "total_pnl": self.total_pnl,
            "trade_count": self.trade_count,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "signals": [s.to_dict() for s in self.signals],
            "orders": [o.to_dict() for o in self.orders],
            "exits": [e.to_dict() for e in self.exits],
        }
