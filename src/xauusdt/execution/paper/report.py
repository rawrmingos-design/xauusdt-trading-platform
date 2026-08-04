"""Daily JSON + human-readable report generation for paper/shadow runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.execution.paper.models import PaperRunResult


def build_daily_report(result: PaperRunResult) -> dict[str, Any]:
    """Build a JSON-serializable daily summary from a run result."""
    exits = result.exits
    wins = [e for e in exits if e.pnl > 0]
    losses = [e for e in exits if e.pnl <= 0]
    gross_profit = sum(e.pnl for e in wins)
    gross_loss = abs(sum(e.pnl for e in losses))
    win_rate = len(wins) / len(exits) if exits else 0.0
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    )
    long_exits = [e for e in exits if e.side.value == "LONG"]
    short_exits = [e for e in exits if e.side.value == "SHORT"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": result.run_id,
        "mode": result.mode.value,
        "strategy_version": result.strategy_version,
        "config_hash": result.config_hash,
        "commit_sha": result.commit_sha,
        "candles_processed": result.candles_processed,
        "initial_balance": result.initial_balance,
        "final_balance": result.final_balance,
        "total_pnl": result.total_pnl,
        "trade_count": len(exits),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown": result.max_drawdown,
        "max_drawdown_pct": round(result.max_drawdown_pct, 4),
        "long_trades": len(long_exits),
        "long_pnl": round(sum(e.pnl for e in long_exits), 2),
        "short_trades": len(short_exits),
        "short_pnl": round(sum(e.pnl for e in short_exits), 2),
        "signals_count": len(result.signals),
        "executed_count": sum(1 for s in result.signals if s.executed),
        "rejected_count": sum(1 for s in result.signals if s.rejected),
        "exit_reasons": _exit_reason_counts(exits),
    }


def _exit_reason_counts(exits: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in exits:
        counts[e.exit_reason.value] = counts.get(e.exit_reason.value, 0) + 1
    return counts


def write_daily_report(
    result: PaperRunResult,
    out_dir: str | Path,
    prefix: str = "paper_daily",
) -> Path:
    """Write JSON + human-readable markdown report. Returns JSON path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = build_daily_report(result)
    day = datetime.now(UTC).date().isoformat()
    json_path = out / f"{prefix}_{result.run_id}_{day}.json"
    md_path = out / f"{prefix}_{result.run_id}_{day}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(_render_md(report))
    return json_path


def _render_md(report: dict[str, Any]) -> str:
    lines = [
        f"# Paper Report {report['run_id']} ({report['mode']})",
        "",
        f"- Strategy: `{report['strategy_version']}` config_hash=`{report['config_hash']}` commit=`{report['commit_sha']}`",
        f"- Candles processed: {report['candles_processed']}",
        f"- Trades: {report['trade_count']} (win rate {report['win_rate']:.1%})",
        f"- PnL: ${report['total_pnl']:,.2f} (final balance ${report['final_balance']:,.2f})",
        f"- Profit factor: {report['profit_factor']:.3f}",
        f"- Max drawdown: ${report['max_drawdown']:,.2f} ({report['max_drawdown_pct']:.2%})",
        f"- LONG: {report['long_trades']} trades ${report['long_pnl']:,.2f} | SHORT: {report['short_trades']} trades ${report['short_pnl']:,.2f}",
        f"- Signals: {report['signals_count']} (executed {report['executed_count']}, rejected {report['rejected_count']})",
        f"- Exit reasons: {report['exit_reasons']}",
        "",
        "> Reference strategy `v3_candidate` has NO confirmed historical OOS edge.",
        "> This is an execution-path harness report, NOT a profitability claim.",
        "",
    ]
    return "\n".join(lines)
