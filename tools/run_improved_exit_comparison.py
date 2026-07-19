#!/usr/bin/env python3
"""
Run comparison: Old Exit Model vs Improved Exit Model (PROJECT-BACKTEST-008).
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestResult
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def run_backtest(cfg: ConfluenceConfig, candles: list[Candle]) -> BacktestResult:
    strategy = ConfluenceStrategy(cfg)
    bt_config = BacktestConfig(initial_balance=10000.0, fee_rate=0.0005, slippage_bps=2.0)
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)
    return engine.run()


def summarize_trades(trades) -> dict:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]

    full_sl = [t for t in trades if t.exit_reason == "SL"]
    partial_tp = [t for t in trades if t.exit_reason == "PARTIAL_TP"]
    break_even = [
        t for t in trades if getattr(t, "is_break_even", False) or t.exit_reason == "BREAK_EVEN"
    ]
    final_tp = [t for t in trades if t.exit_reason == "TP"]
    signal = [t for t in trades if t.exit_reason == "SIGNAL"]
    eol = [t for t in trades if t.exit_reason == "EOL"]

    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0

    full_exits = [t for t in trades if not t.is_partial]
    avg_r = (
        sum(
            (t.gross_pnl / (t.quantity * t.sl_distance))
            for t in full_exits
            if getattr(t, "sl_distance", 0.0) > 0
        )
        / len(full_exits)
        if full_exits
        else 0.0
    )

    return {
        "trades_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "full_sl": len(full_sl),
        "partial_tp": len(partial_tp),
        "break_even": len(break_even),
        "final_tp": len(final_tp),
        "signal": len(signal),
        "eol": len(eol),
        "avg_realized_r": avg_r,
    }


async def main():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    candle_orms = []
    async for session in get_session():
        repo = CandleRepository(session)
        results = await repo.query_by_range("XAU-USDT-SWAP", "15m", limit=100000)
        candle_orms = list(results)
        await session.close()
        break

    if not candle_orms:
        print("ERROR: No candles found")
        return

    candles = [
        Candle(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            open_time=r.open_time,
            open=r.open_price,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            quote_volume=r.quote_volume,
        )
        for r in candle_orms
    ]

    # Filter to identical 90-day dataset used in previous backtests
    start_time = datetime(2026, 4, 16, tzinfo=UTC)
    end_time = datetime(2026, 7, 14, 23, 59, 59, tzinfo=UTC)
    candles = [c for c in candles if start_time <= c.open_time <= end_time]
    print(f"Loaded {len(candles)} candles.")

    configs = {
        "V1_Old": ConfluenceConfig(
            version="v1_old",
            ema_fast_period=50,
            ema_slow_period=200,
            risk_reward_ratio=2.0,
            sl_atr_multiplier=1.5,
            improved_exit=False,
            min_score=65.0,
        ),
        "V1_Improved": ConfluenceConfig(
            version="v1_new",
            ema_fast_period=50,
            ema_slow_period=200,
            risk_reward_ratio=2.0,
            sl_atr_multiplier=2.0,
            improved_exit=True,
            min_score=65.0,
        ),
        "V2_Old": ConfluenceConfig(
            version="v2_old",
            adx_min=25.0,
            adx_rising=True,
            ema_slope_alignment=True,
            risk_reward_ratio=2.5,
            sl_atr_multiplier=1.5,
            improved_exit=False,
            min_score=65.0,
        ),
        "V2_Improved": ConfluenceConfig(
            version="v2_new",
            adx_min=25.0,
            adx_rising=True,
            ema_slope_alignment=True,
            risk_reward_ratio=2.5,
            sl_atr_multiplier=2.5,
            improved_exit=True,
            min_score=65.0,
        ),
    }

    results = {}
    summaries = {}
    for name, cfg in configs.items():
        print(f"Running {name}...")
        res = run_backtest(cfg, candles)
        results[name] = res.to_dict()
        summaries[name] = {"metrics": res.metrics.to_dict(), "exits": summarize_trades(res.trades)}
        print(f"  PnL: {res.metrics.net_pnl:.2f}, WR: {res.metrics.win_rate * 100:.1f}%")

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    # Save JSON
    with open(report_dir / f"comparison_BACKTEST-008_{ts}.json", "w") as f:
        json.dump(
            {"configs": {k: v.__dict__ for k, v in configs.items()}, "summaries": summaries},
            f,
            indent=2,
            default=str,
        )

    # Save Markdown
    md_lines = [
        "# Exit Model Comparison (BACKTEST-008)",
        "",
        "Comparing the Old Exit Model against the Improved Exit Model with Partial TP and Break-Even.",
        "",
        "## Summary Metrics",
        "",
        "| Variant | Trades | Win Rate | Net PnL | Profit Factor | Max DD | Expectancy | Avg Realized R | Avg Win | Avg Loss |",
        "|---------|--------|----------|---------|---------------|--------|------------|----------------|---------|----------|",
    ]

    for name, s in summaries.items():
        m = s["metrics"]
        e = s["exits"]
        md_lines.append(
            f"| {name} | {m['total_trades']} | {m['win_rate'] * 100:.1f}% | ${m['net_pnl']:,.2f} | {m['profit_factor']:.2f} | {m['max_drawdown_pct']:.2f}% | ${m['expectancy']:.2f} | {e['avg_realized_r']:.2f}R | ${e['avg_win']:.2f} | ${e['avg_loss']:.2f} |"
        )

    md_lines.extend(
        [
            "",
            "## Exit Breakdown",
            "",
            "| Variant | Full SL | Partial TP | Break-Even SL | Final TP | Signal/EOL |",
            "|---------|---------|------------|---------------|----------|------------|",
        ]
    )

    for name, s in summaries.items():
        e = s["exits"]
        md_lines.append(
            f"| {name} | {e['full_sl']} | {e['partial_tp']} | {e['break_even']} | {e['final_tp']} | {e['signal'] + e['eol']} |"
        )

    with open(report_dir / f"comparison_BACKTEST-008_{ts}.md", "w") as f:
        f.write("\\n".join(md_lines) + "\\n")

    print(f"\\nSaved reports to {report_dir}")


if __name__ == "__main__":
    asyncio.run(main())
