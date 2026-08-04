"""Risk engine models for PROJECT-RISK-001.

Deterministic, persistent, restart-safe risk controls for the shadow/paper
harness. Placement: between strategy signal generation and simulated order
creation. Strategy scoring and exit logic are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RiskRejectionCode(Enum):
    """Structured rejection codes for risk-blocked entries."""

    KILL_SWITCH_ACTIVE = "risk_kill_switch_active"
    DAILY_LOSS_LIMIT = "risk_daily_loss_limit_reached"
    WEEKLY_LOSS_LIMIT = "risk_weekly_loss_limit_reached"
    CONSECUTIVE_LOSS_COOLDOWN = "risk_consecutive_loss_cooldown"
    MAX_OPEN_POSITIONS = "risk_max_open_positions"
    INVALID_ENTRY_PRICE = "risk_invalid_entry_price"
    INVALID_STOP_PRICE = "risk_invalid_stop_price"
    INVALID_STOP_DISTANCE = "risk_invalid_stop_distance"
    QUANTITY_BELOW_MINIMUM = "risk_quantity_below_minimum"
    STALE_CANDLE = "risk_stale_candle"
    CANDLE_GAP = "risk_candle_gap"
    DUPLICATE_CANDLE = "risk_duplicate_candle"


@dataclass(frozen=True)
class RiskConfig:
    """Baseline conservative risk configuration for paper research."""

    risk_per_trade_pct: float = 0.25  # % of equity risked per trade
    max_daily_realized_loss_pct: float = 1.0  # net realized loss / equity
    max_weekly_realized_loss_pct: float = 2.5
    max_open_positions: int = 1
    max_consecutive_losing_trades: int = 3
    min_quantity: float = 0.001  # minimum contract quantity
    max_quantity: float = 1_000_000.0  # sanity cap
    fee_rate: float = 0.0005  # taker fee, matches backtest/paper
    slippage_bps: float = 2.0  # deterministic slippage, matches paper
    include_estimated_costs: bool = True  # fees + slippage in sizing
    manual_kill_switch: bool = True
    # cooldown in seconds after the consecutive-loss threshold is hit;
    # deterministic, time-based (UTC). 0 = disabled.
    cooldown_seconds: int = 3600

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_daily_realized_loss_pct": self.max_daily_realized_loss_pct,
            "max_weekly_realized_loss_pct": self.max_weekly_realized_loss_pct,
            "max_open_positions": self.max_open_positions,
            "max_consecutive_losing_trades": self.max_consecutive_losing_trades,
            "min_quantity": self.min_quantity,
            "max_quantity": self.max_quantity,
            "fee_rate": self.fee_rate,
            "slippage_bps": self.slippage_bps,
            "include_estimated_costs": self.include_estimated_costs,
            "manual_kill_switch": self.manual_kill_switch,
            "cooldown_seconds": self.cooldown_seconds,
        }


@dataclass
class KillSwitchState:
    """Persistent manual kill switch."""

    enabled: bool = False
    reason: str = ""
    timestamp: str | None = None
    actor: str = "cli"
    source: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "source": self.source,
        }


@dataclass
class RiskDecision:
    """Outcome of a single risk evaluation for one signal."""

    run_id: str
    candle_time: datetime
    signal_type: str  # BUY | SELL | HOLD
    approved: bool
    rejection_codes: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    # sizing (populated when approved)
    quantity: float = 0.0
    risk_budget: float = 0.0
    risk_per_unit: float = 0.0
    estimated_entry_fee: float = 0.0
    estimated_exit_fee: float = 0.0
    estimated_entry_slippage: float = 0.0
    estimated_exit_slippage: float = 0.0
    entry_price: float = 0.0
    stop_price: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "candle_time": self.candle_time.isoformat(),
            "signal_type": self.signal_type,
            "approved": self.approved,
            "rejection_codes": list(self.rejection_codes),
            "rejection_reasons": list(self.rejection_reasons),
            "quantity": self.quantity,
            "risk_budget": self.risk_budget,
            "risk_per_unit": self.risk_per_unit,
            "estimated_entry_fee": self.estimated_entry_fee,
            "estimated_exit_fee": self.estimated_exit_fee,
            "estimated_entry_slippage": self.estimated_entry_slippage,
            "estimated_exit_slippage": self.estimated_exit_slippage,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
        }


@dataclass
class RiskStateSnapshot:
    """Point-in-time risk counters for a run."""

    run_id: str
    equity: float
    daily_realized_loss: float
    daily_limit: float
    weekly_realized_loss: float
    weekly_limit: float
    consecutive_losses: int
    cooldown_until: str | None
    cooldown_active: bool
    kill_switch: KillSwitchState
    open_positions: int
    max_open_positions: int
    last_rejection_codes: list[str]
    last_rejection_time: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "equity": self.equity,
            "daily_realized_loss": self.daily_realized_loss,
            "daily_limit": self.daily_limit,
            "weekly_realized_loss": self.weekly_realized_loss,
            "weekly_limit": self.weekly_limit,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_until": self.cooldown_until,
            "cooldown_active": self.cooldown_active,
            "kill_switch": self.kill_switch.to_dict(),
            "open_positions": self.open_positions,
            "max_open_positions": self.max_open_positions,
            "last_rejection_codes": list(self.last_rejection_codes),
            "last_rejection_time": self.last_rejection_time,
        }
