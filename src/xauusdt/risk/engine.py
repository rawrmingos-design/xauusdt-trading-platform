"""Deterministic risk and position sizing engine (PROJECT-RISK-001).

Placed between strategy signal generation and simulated order creation.
Evaluates each entry signal against configured limits, computes fixed-
fractional quantity from the user-specified formula, records every decision,
and persists counters for restart safety.

Sizing formula (exact, as specified):
    risk_budget        = current_equity * risk_per_trade_pct
    estimated_costs    = entry_fee + exit_fee + entry_slippage + exit_slippage
    risk_per_unit      = abs(entry - stop) + estimated_costs_per_unit
    quantity           = floor(risk_budget / risk_per_unit)

Quantity is floored (never rounded up) so worst-case stop loss stays within
the risk budget including estimated fees and slippage.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from xauusdt.risk.models import (
    KillSwitchState,
    RiskConfig,
    RiskDecision,
    RiskRejectionCode,
    RiskStateSnapshot,
)
from xauusdt.risk.store import RiskStore


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _day_key(t: datetime) -> str:
    return t.astimezone(UTC).date().isoformat()


def _week_key(t: datetime) -> str:
    """ISO week (Monday-based), used for weekly realized-loss periods."""
    d = t.astimezone(UTC).date()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _floor_quantity(q: float) -> float:
    """Floor to a deterministic precision (never round up risk)."""
    if not math.isfinite(q) or q <= 0:
        return 0.0
    # floor at 6 decimals (contract precision)
    return math.floor(q * 1_000_000) / 1_000_000


class RiskEngine:
    """Stateless core logic + persistent counters via RiskStore."""

    def __init__(
        self,
        store: RiskStore,
        run_id: str,
        config: RiskConfig | None = None,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._cfg = config or RiskConfig()

    # ------------------------------------------------------------ helpers

    def _period_tracking(self, now: datetime) -> tuple[str, str]:
        """Return (day_key, week_key) for the current UTC period."""
        return _day_key(now), _week_key(now)

    def _reset_periods(self, state: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Reset realized-loss counters when a new UTC day/week started."""
        day_key, week_key = self._period_tracking(now)
        if state.get("_day_key") is None:
            state["_day_key"] = state.get("day_key") or day_key
        if state.get("_week_key") is None:
            state["_week_key"] = state.get("week_key") or week_key
        if state["_day_key"] != day_key:
            state["daily_realized_loss"] = 0.0
            state["_day_key"] = day_key
        if state["_week_key"] != week_key:
            state["weekly_realized_loss"] = 0.0
            state["_week_key"] = week_key
        return state

    # ------------------------------------------------------------- sizing

    def compute_quantity(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        side: str,
        fee_rate: float | None = None,
        slippage_bps: float | None = None,
    ) -> tuple[float, float, float, dict[str, float]]:
        """Compute quantity from the mandated formula.

        Returns (quantity, risk_budget, risk_per_unit, cost_breakdown).
        """
        fee_rate = fee_rate if fee_rate is not None else self._cfg.fee_rate
        slippage_bps = slippage_bps if slippage_bps is not None else self._cfg.slippage_bps
        risk_budget = equity * (self._cfg.risk_per_trade_pct / 100.0)
        # unit risk: distance to stop
        unit_risk = abs(entry_price - stop_price)
        if unit_risk <= 0 or not math.isfinite(unit_risk):
            return 0.0, risk_budget, 0.0, {}
        slippage_ratio = slippage_bps / 10_000.0
        est_entry_fee = entry_price * fee_rate
        est_exit_fee = entry_price * fee_rate
        est_entry_slip = entry_price * slippage_ratio
        est_exit_slip = entry_price * slippage_ratio
        costs = (
            est_entry_fee + est_exit_fee + est_entry_slip + est_exit_slip
            if self._cfg.include_estimated_costs
            else 0.0
        )
        risk_per_unit = unit_risk + costs
        quantity = _floor_quantity(risk_budget / risk_per_unit)
        breakdown = {
            "estimated_entry_fee": est_entry_fee,
            "estimated_exit_fee": est_exit_fee,
            "estimated_entry_slippage": est_entry_slip,
            "estimated_exit_slippage": est_exit_slip,
            "estimated_costs_total": costs,
            "unit_risk": unit_risk,
        }
        return quantity, risk_budget, risk_per_unit, breakdown

    # ---------------------------------------------------------- evaluation

    def evaluate_entry(
        self,
        candle_time: datetime,
        signal_type: str,
        entry_price: float,
        stop_price: float,
        side: str,
        equity: float,
        open_positions: int,
        now: datetime | None = None,
    ) -> RiskDecision:
        """Evaluate one entry signal; returns approved/rejected decision."""
        now = now or _utc_now()
        decision = RiskDecision(
            run_id=self._run_id,
            candle_time=candle_time,
            signal_type=signal_type,
            approved=False,
            entry_price=entry_price,
            stop_price=stop_price,
        )
        codes: list[str] = []
        reasons: list[str] = []

        # kill switch
        ks = self._store.load_kill_switch(self._run_id)
        if ks.enabled:
            codes.append(RiskRejectionCode.KILL_SWITCH_ACTIVE.value)
            reasons.append(f"kill switch active: {ks.reason or 'no reason'}")

        # position constraint
        if open_positions >= self._cfg.max_open_positions:
            codes.append(RiskRejectionCode.MAX_OPEN_POSITIONS.value)
            reasons.append(f"open positions {open_positions} >= max {self._cfg.max_open_positions}")

        # invalid prices
        if not math.isfinite(entry_price) or entry_price <= 0:
            codes.append(RiskRejectionCode.INVALID_ENTRY_PRICE.value)
            reasons.append(f"invalid entry price: {entry_price}")
        if not math.isfinite(stop_price) or stop_price <= 0:
            codes.append(RiskRejectionCode.INVALID_STOP_PRICE.value)
            reasons.append(f"invalid stop price: {stop_price}")
        unit_risk = abs(entry_price - stop_price) if entry_price > 0 and stop_price > 0 else 0.0
        if unit_risk <= 0:
            codes.append(RiskRejectionCode.INVALID_STOP_DISTANCE.value)
            reasons.append(f"invalid stop distance: entry={entry_price} stop={stop_price}")

        # realized loss limits
        state = self._store.load_state(self._run_id, default_equity=equity)
        state = self._reset_periods(state, now)
        daily_limit = equity * (self._cfg.max_daily_realized_loss_pct / 100.0)
        weekly_limit = equity * (self._cfg.max_weekly_realized_loss_pct / 100.0)
        if state["daily_realized_loss"] >= daily_limit and daily_limit > 0:
            codes.append(RiskRejectionCode.DAILY_LOSS_LIMIT.value)
            reasons.append(
                f"daily realized loss {state['daily_realized_loss']:.2f} >= limit {daily_limit:.2f}"
            )
        if state["weekly_realized_loss"] >= weekly_limit and weekly_limit > 0:
            codes.append(RiskRejectionCode.WEEKLY_LOSS_LIMIT.value)
            reasons.append(
                f"weekly realized loss {state['weekly_realized_loss']:.2f} >= limit {weekly_limit:.2f}"
            )

        # cooldown
        cooldown_until = state.get("cooldown_until")
        if cooldown_until:
            try:
                cu = datetime.fromisoformat(cooldown_until)
                if now < cu:
                    codes.append(RiskRejectionCode.CONSECUTIVE_LOSS_COOLDOWN.value)
                    reasons.append(f"cooldown until {cu.isoformat()}")
            except ValueError:
                pass

        if codes:
            decision.approved = False
            decision.rejection_codes = codes
            decision.rejection_reasons = reasons
            self._store.save_decision(decision)
            self._store.save_costate(
                self._run_id,
                equity,
                state["daily_realized_loss"],
                state["weekly_realized_loss"],
                state["consecutive_losses"],
                state.get("cooldown_until"),
                codes,
                now.isoformat(),
                day_key=state.get("_day_key"),
                week_key=state.get("_week_key"),
            )
            return decision

        # sizing
        quantity, risk_budget, risk_per_unit, breakdown = self.compute_quantity(
            equity, entry_price, stop_price, side
        )
        if quantity <= 0 or not math.isfinite(quantity):
            codes.append(RiskRejectionCode.QUANTITY_BELOW_MINIMUM.value)
            reasons.append(
                f"quantity {quantity} <= 0 or non-finite (budget {risk_budget:.2f}, rpu {risk_per_unit:.6f})"
            )
        elif quantity < self._cfg.min_quantity:
            codes.append(RiskRejectionCode.QUANTITY_BELOW_MINIMUM.value)
            reasons.append(f"quantity {quantity} below min {self._cfg.min_quantity}")
        elif quantity > self._cfg.max_quantity:
            codes.append(RiskRejectionCode.QUANTITY_BELOW_MINIMUM.value)
            reasons.append(f"quantity {quantity} above max {self._cfg.max_quantity}")

        if codes:
            decision.approved = False
            decision.rejection_codes = codes
            decision.rejection_reasons = reasons
            decision.quantity = quantity
            decision.risk_budget = risk_budget
            decision.risk_per_unit = risk_per_unit
            self._store.save_decision(decision)
            self._store.save_costate(
                self._run_id,
                equity,
                state["daily_realized_loss"],
                state["weekly_realized_loss"],
                state["consecutive_losses"],
                state.get("cooldown_until"),
                codes,
                now.isoformat(),
                day_key=state.get("_day_key"),
                week_key=state.get("_week_key"),
            )
            return decision

        decision.approved = True
        decision.quantity = quantity
        decision.risk_budget = risk_budget
        decision.risk_per_unit = risk_per_unit
        decision.estimated_entry_fee = breakdown.get("estimated_entry_fee", 0.0)
        decision.estimated_exit_fee = breakdown.get("estimated_exit_fee", 0.0)
        decision.estimated_entry_slippage = breakdown.get("estimated_entry_slippage", 0.0)
        decision.estimated_exit_slippage = breakdown.get("estimated_exit_slippage", 0.0)
        self._store.save_decision(decision)
        return decision

    # --------------------------------------------------- parent trade close

    def record_realized_pnl(
        self,
        candle_time: datetime,
        entry_candle_time: datetime,
        realized_pnl: float,
        equity: float,
        parent_closed: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Account realized PnL into daily/weekly counters and loss streaks.

        `parent_closed=True` means the parent trade (all legs) is fully closed;
        only then is the consecutive-loss counter updated, using aggregate
        net realized PnL across all legs of the parent trade.
        """
        now = now or _utc_now()
        state = self._store.load_state(self._run_id, default_equity=equity)
        state = self._reset_periods(state, now)

        if parent_closed:
            # full parent closed: net realized PnL decides the streak
            if realized_pnl < 0:
                state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                if (
                    self._cfg.max_consecutive_losing_trades > 0
                    and state["consecutive_losses"] >= self._cfg.max_consecutive_losing_trades
                ):
                    if self._cfg.cooldown_seconds > 0:
                        state["cooldown_until"] = (
                            now + timedelta(seconds=self._cfg.cooldown_seconds)
                        ).isoformat()
                    else:
                        state["cooldown_until"] = None
            else:
                # profitable or break-even resets the streak
                state["consecutive_losses"] = 0
                state["cooldown_until"] = None

        # realized loss counters: only negative net PnL counts (partials too)
        if realized_pnl < 0:
            state["daily_realized_loss"] = state.get("daily_realized_loss", 0.0) + abs(realized_pnl)
            state["weekly_realized_loss"] = state.get("weekly_realized_loss", 0.0) + abs(
                realized_pnl
            )

        self._store.save_costate(
            self._run_id,
            equity,
            state["daily_realized_loss"],
            state["weekly_realized_loss"],
            state["consecutive_losses"],
            state.get("cooldown_until"),
            state.get("last_rejection_codes", []),
            state.get("last_rejection_time"),
            day_key=state.get("_day_key"),
            week_key=state.get("_week_key"),
        )
        return state

    # ---------------------------------------------------------- kill switch

    def enable_kill_switch(self, reason: str, actor: str = "cli") -> KillSwitchState:
        ks = KillSwitchState(
            enabled=True,
            reason=reason,
            timestamp=_utc_now().isoformat(),
            actor=actor,
            source="manual",
        )
        self._store.save_kill_switch(self._run_id, ks)
        return ks

    def disable_kill_switch(self, reason: str, actor: str = "cli") -> KillSwitchState:
        ks = KillSwitchState(
            enabled=False,
            reason=reason,
            timestamp=_utc_now().isoformat(),
            actor=actor,
            source="manual",
        )
        self._store.save_kill_switch(self._run_id, ks)
        return ks

    def kill_switch(self) -> KillSwitchState:
        return self._store.load_kill_switch(self._run_id)

    def reset_counters(self, reason: str = "manual reset", actor: str = "cli") -> None:
        """Explicitly reset permitted counters (CLI command only)."""
        state = self._store.load_state(self._run_id)
        self._store.save_costate(
            self._run_id,
            state.get("equity", 0.0),
            0.0,
            0.0,
            0,
            None,
            [],
            None,
        )

    # ------------------------------------------------------------- snapshot

    def snapshot(
        self,
        equity: float,
        open_positions: int,
        now: datetime | None = None,
    ) -> RiskStateSnapshot:
        now = now or _utc_now()
        daily_limit = equity * (self._cfg.max_daily_realized_loss_pct / 100.0)
        weekly_limit = equity * (self._cfg.max_weekly_realized_loss_pct / 100.0)
        return self._store.snapshot(
            self._run_id,
            equity,
            open_positions,
            self._cfg.max_open_positions,
            now,
            daily_limit=daily_limit,
            weekly_limit=weekly_limit,
        )
