"""PROJECT-FORWARD-OOS-001 — frozen evaluation engine.

Anti-peeking guarantee: the evaluate() entry refuses to compute any strategy
performance metric before CHECKPOINT_60D UTC. There is NO force/bypass/env/
alternate-date escape. The status path exposes operational/data-quality info
only. All cutoffs are UTC-aware wall-clock.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.forward_oos.manifest import (
    CHECKPOINT_60D,
)
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config


class DataQualityError(Exception):
    """Raised when the forward dataset fails audit before evaluation."""


@dataclass
class EvalLock:
    """Result of the pre-checkpoint gate."""

    now: datetime
    locked_until: float
    unlocked: bool


@dataclass
class ParityReport:
    """Paper-runtime vs offline-replay parity on the frozen window."""

    signal_count_paper: int
    signal_count_replay: int
    entry_count_paper: int
    entry_count_replay: int
    exit_count_paper: int
    exit_count_replay: int
    signal_parity_pct: float
    entry_parity_pct: float
    exit_parity_pct: float


@dataclass
class MetricsReport:
    """Strategy performance metrics — ONLY legal at/after a checkpoint."""

    trade_count: int
    long_count: int
    short_count: int
    expectancy_r: float
    profit_factor: float
    win_rate_pct: float
    net_pnl: float
    max_drawdown_pct: float
    avg_realized_r: float
    exit_reasons: dict[str, int]
    break_even_count: int


def utc_now() -> datetime:
    """UTC-aware wall clock. Single seam so tests can pin time."""
    return datetime.now(UTC)


def current_checkpoint() -> datetime | None:
    """Highest checkpoint the wall-clock has reached; None if locked."""
    now = utc_now()
    if now >= CHECKPOINT_60D:
        return CHECKPOINT_60D
    return None


def evaluate_gate() -> EvalLock:
    """Anti-peek gate. Refuses (exit 1) before 2026-09-13, no bypass."""
    now = utc_now()
    cp = current_checkpoint()
    if cp is None:
        print(
            "\nEVALUATION LOCKED\n"
            f"  now      : {now.isoformat()}\n"
            f"  unlocked : {CHECKPOINT_60D.isoformat()} (60d checkpoint)\n"
            "  No strategy-performance metrics are available before the\n"
            "  checkpoint (PROJECT-FORWARD-OOS-001 anti-peeking control).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return EvalLock(now=now, locked_until=CHECKPOINT_60D.timestamp(), unlocked=True)


def checkpoint_end(checkpoint_label: str) -> datetime | None:
    """UTC end bound for a checkpoint window."""
    label = checkpoint_label.lower()
    if label == "60d":
        return CHECKPOINT_60D
    if label == "90d":
        # 90d checkpoint is stronger_90d 2026-10-13; window [start, 90d)
        from xauusdt.forward_oos.manifest import CHECKPOINT_90D

        return CHECKPOINT_90D
    raise ValueError(f"Unknown checkpoint: {checkpoint_label!r} (use 60d or 90d)")


def replay_offline(candles: list[Any]) -> Any:
    """Replay v3_candidate offline on the frozen forward candles.

    Uses ConfluenceBacktestEngine with v3_candidate config and the same
    fee/slippage defaults as the paper harness so parity is meaningful.
    Returns BacktestResult.
    """
    if not candles:
        raise DataQualityError("No candles to replay")
    cfg = BacktestConfig(initial_balance=10_000.0, fee_rate=0.0006, slippage_bps=5.0)
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    engine = ConfluenceBacktestEngine(cfg, candles, strategy)
    return engine.run()


def build_parity(
    paper_signals: int,
    replay_signals: int,
    paper_entries: int,
    replay_entries: int,
    paper_exits: int,
    replay_exits: int,
) -> ParityReport:
    """Compare paper persisted vs offline replay counts."""

    def pct(a: int, b: int) -> float:
        if b == 0:
            return 0.0
        return round(100.0 * a / b, 2)

    return ParityReport(
        signal_count_paper=paper_signals,
        signal_count_replay=replay_signals,
        entry_count_paper=paper_entries,
        entry_count_replay=replay_entries,
        exit_count_paper=paper_exits,
        exit_count_replay=replay_exits,
        signal_parity_pct=pct(paper_signals, replay_signals),
        entry_parity_pct=pct(paper_entries, replay_entries),
        exit_parity_pct=pct(paper_exits, replay_exits),
    )


def compute_metrics(result: Any) -> MetricsReport:
    """Compute performance metrics from a BacktestResult.

    Called ONLY after the gate has verified the checkpoint is unlocked.
    Grouped by entry_candle_time so multi-leg partial exits aggregate to one
    parent trade before averaging R (compounding-decay pitfall).
    """
    trades = _group_parent_trades(result.trades)
    if not trades:
        return MetricsReport(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {}, 0)

    long_trades = [t for t in trades if t["side"] == "LONG"]
    short_trades = [t for t in trades if t["side"] == "SHORT"]
    realized_r = _realized_r(trades)
    wins = [t for t in trades if t["parent_pnl"] > 0]
    losses = [t for t in trades if t["parent_pnl"] <= 0]
    gross_win = sum(t["parent_pnl"] for t in wins)
    gross_loss = abs(sum(t["parent_pnl"] for t in losses))
    pf = round(gross_win / gross_loss, 4) if gross_loss > 0 else (0.0 if gross_win == 0 else 999.0)
    net = round(sum(t["parent_pnl"] for t in trades), 2)
    max_dd = round(result.max_drawdown_pct, 4)

    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1

    return MetricsReport(
        trade_count=len(trades),
        long_count=len(long_trades),
        short_count=len(short_trades),
        expectancy_r=round(sum(realized_r) / len(realized_r), 4) if realized_r else 0.0,
        profit_factor=pf,
        win_rate_pct=round(100.0 * len(wins) / len(trades), 2),
        net_pnl=net,
        max_drawdown_pct=max_dd,
        avg_realized_r=round(sum(realized_r) / len(realized_r), 4) if realized_r else 0.0,
        exit_reasons=reasons,
        break_even_count=sum(1 for t in trades if t.get("is_break_even")),
    )


def _group_parent_trades(trades: list[Any]) -> list[dict[str, Any]]:
    """Aggregate multi-leg partial exits into one parent trade per entry."""
    groups: dict[str, list[Any]] = {}
    for t in trades:
        key = t.entry_candle_time
        groups.setdefault(key, []).append(t)
    out = []
    for key, grp in groups.items():
        parent_pnl = sum(t.pnl for t in grp)
        parent_fee = sum(t.fee for t in grp)
        head = grp[0]
        out.append(
            {
                "entry_candle_time": key,
                "side": head.side,
                "parent_pnl": parent_pnl,
                "parent_fee": parent_fee,
                "entry_price": head.entry_price,
                "quantity": head.quantity,
                "sl_distance": head.sl_distance,
                "exit_reason": grp[-1].exit_reason,
                "is_break_even": any(getattr(t, "is_break_even", False) for t in grp),
            }
        )
    return out


def _realized_r(trades: list[dict[str, Any]]) -> list[float]:
    """Realized R = parent_pnl / risk_per_parent.

    Risk per parent = entry_price * quantity * sl_distance (the trade's stop
    distance in price units, from BacktestTrade.sl_distance). This is the
    gross_pnl/risk convention — dollar PnL alone is misleading under
    compounding, matching the research stack's evaluation rule.
    """
    out: list[float] = []
    for t in trades:
        risk = t["entry_price"] * t["quantity"] * t.get("sl_distance", 0.0)
        if risk <= 1e-12:
            out.append(0.0)
            continue
        out.append(t["parent_pnl"] / risk)
    return out


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False, default=str)
