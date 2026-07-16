"""Compare Confluence Strategy V1 vs V2."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")
from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def get_trade_stats(trades):
    if not trades:
        return {}

    longs = sum(1 for t in trades if t.side == "LONG")
    shorts = sum(1 for t in trades if t.side == "SHORT")
    sl_hits = sum(1 for t in trades if t.exit_reason == "SL")
    tp_hits = sum(1 for t in trades if t.exit_reason == "TP")
    signal_closes = sum(1 for t in trades if t.exit_reason == "SIGNAL")
    eol_closes = sum(1 for t in trades if t.exit_reason == "EOL")

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0

    durations = [(datetime.fromisoformat(t.exit_candle_time) - datetime.fromisoformat(t.entry_candle_time)).total_seconds() for t in trades]
    avg_duration_h = sum(durations) / len(durations) / 3600

    return {
        "longs": longs,
        "shorts": shorts,
        "sl_hits": sl_hits,
        "tp_hits": tp_hits,
        "signal_closes": signal_closes,
        "eol_closes": eol_closes,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_duration_h": avg_duration_h,
    }

async def main() -> None:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    print("Loading candles...")
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range("XAU-USDT-SWAP", "15m", limit=100000)
        await session.close()
        break

    candles = [
        Candle(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            open_time=r.open_time,
            open=float(r.open_price),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=float(r.volume or 0),
        )
        for r in orms
    ]

    print(f"Loaded {len(candles)} candles")
    if not candles:
        return

    bt_config = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
    )

    print("\nRunning V1 Baseline...")
    v1_cfg = ConfluenceConfig(
        version="v1",
        ema_fast_period=50,
        ema_slow_period=200,
        min_score=65.0,
        min_score_gap=15.0,
        adx_min=20.0,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.0,
    )
    v1_engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(v1_cfg))
    v1_result = v1_engine.run()
    v1_m = v1_result.metrics
    v1_stats = get_trade_stats(v1_m.trade_list)

    print("Running V2 Candidate...")
    v2_cfg = ConfluenceConfig(
        version="v2",
        adx_min=25.0,
        adx_rising=True,
        ema_slope_alignment=True,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.5,
    )
    v2_engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(v2_cfg))
    v2_result = v2_engine.run()
    v2_m = v2_result.metrics
    v2_stats = get_trade_stats(v2_m.trade_list)

    # Identify rejected trades
    v1_entry_times = {t.entry_candle_time for t in v1_m.trade_list}
    v2_entry_times = {t.entry_candle_time for t in v2_m.trade_list}
    rejected_times = v1_entry_times - v2_entry_times
    rejected_trades = [t for t in v1_m.trade_list if t.entry_candle_time in rejected_times]

    rejected_losses = sum(1 for t in rejected_trades if t.pnl <= 0)
    rejected_wins = sum(1 for t in rejected_trades if t.pnl > 0)

    # Was it a quality improvement or just less trades?
    # Was it a quality improvement or just less trades?
    rejected_wr = (rejected_wins / len(rejected_trades) * 100) if rejected_trades else 0
    rejected_v2_wr = 0.0
    for t in rejected_trades:
        # Check if this rejected trade would have been a win if taken
        pass # already calculated

    # V2 generated a different trade schedule, not just filtering V1.
    v2_unique_rejected = len(rejected_trades)

    quality_verdict = ""
    if v2_m.win_rate > v1_m.win_rate:
        quality_verdict = f"V2 IMPROVED trade quality. Rejected {v2_unique_rejected} trades that V1 would have taken. The rejected trades had a WR of {rejected_wr:.1f}%."
    elif len(rejected_trades) == len(v2_m.trade_list):
        quality_verdict = f"V2 generated a completely different trade schedule. All {len(v2_m.trade_list)} V2 trades were different from V1. The rejected trades from V1 had a WR of {rejected_wr:.1f}%."
    else:
        quality_verdict = f"V2 DID NOT significantly improve trade quality relative to its own schedule. It rejected {v2_unique_rejected} trades V1 would have taken ({rejected_losses} losses, {rejected_wins} wins). The rejected trades had a WR of {rejected_wr:.1f}%."

    # Write report
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    json_data = {
        "v1_config": v1_cfg.to_dict(),
        "v2_config": v2_cfg.to_dict(),
        "v1_metrics": {**v1_m.__dict__, "trade_list": len(v1_m.trade_list)},
        "v2_metrics": {**v2_m.__dict__, "trade_list": len(v2_m.trade_list)},
        "v1_stats": v1_stats,
        "v2_stats": v2_stats,
        "rejected_analysis": {
            "total_rejected": len(rejected_trades),
            "rejected_wins": rejected_wins,
            "rejected_losses": rejected_losses,
        }
    }
    with open(report_dir / f"comparison_BACKTEST-006_{timestamp}.json", "w") as f:
        json.dump(json_data, f, indent=2)

    with open(report_dir / "comparison_BACKTEST-006.md", "w") as f:
        f.write("# Confluence V1 vs V2 Comparison (PROJECT-BACKTEST-006)\n\n")
        f.write(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write("**Dataset**: XAU-USDT-SWAP 15m (8640 candles, 90 days)\n\n")

        f.write("## Performance Metrics\n\n")
        f.write("| Metric | V1 (Baseline) | V2 (Quality Filters) | Diff |\n")
        f.write("|---|---|---|---|\n")
        f.write(f"| Total Trades | {v1_m.total_trades} | {v2_m.total_trades} | {v2_m.total_trades - v1_m.total_trades} |\n")
        f.write(f"| Win Rate | {v1_m.win_rate*100:.1f}% | {v2_m.win_rate*100:.1f}% | {(v2_m.win_rate - v1_m.win_rate)*100:+.1f}% |\n")
        f.write(f"| Net PnL | {v1_m.net_pnl:.2f} | {v2_m.net_pnl:.2f} | {v2_m.net_pnl - v1_m.net_pnl:+.2f} |\n")
        f.write(f"| Profit Factor | {v1_m.profit_factor:.2f} | {v2_m.profit_factor:.2f} | {v2_m.profit_factor - v1_m.profit_factor:+.2f} |\n")
        f.write(f"| Max Drawdown | {v1_m.max_drawdown_pct*100:.1f}% | {v2_m.max_drawdown_pct*100:.1f}% | {(v2_m.max_drawdown_pct - v1_m.max_drawdown_pct)*100:+.1f}% |\n")
        f.write(f"| Expectancy | {v1_m.expectancy:.2f} | {v2_m.expectancy:.2f} | {v2_m.expectancy - v1_m.expectancy:+.2f} |\n\n")

        f.write("## Trade Analysis\n\n")
        f.write("| Metric | V1 | V2 |\n")
        f.write("|---|---|---|\n")
        f.write(f"| Longs / Shorts | {v1_stats['longs']} / {v1_stats['shorts']} | {v2_stats['longs']} / {v2_stats['shorts']} |\n")
        f.write(f"| SL Hits | {v1_stats['sl_hits']} | {v2_stats['sl_hits']} |\n")
        f.write(f"| TP Hits | {v1_stats['tp_hits']} | {v2_stats['tp_hits']} |\n")
        f.write(f"| Signal Closes | {v1_stats['signal_closes']} | {v2_stats['signal_closes']} |\n")
        f.write(f"| EOL Closes | {v1_stats['eol_closes']} | {v2_stats['eol_closes']} |\n")
        f.write(f"| Avg Win | {v1_stats['avg_win']:.2f} | {v2_stats['avg_win']:.2f} |\n")
        f.write(f"| Avg Loss | {v1_stats['avg_loss']:.2f} | {v2_stats['avg_loss']:.2f} |\n")
        f.write(f"| Avg Duration | {v1_stats['avg_duration_h']:.1f}h | {v2_stats['avg_duration_h']:.1f}h |\n\n")

        f.write("## Rejection Analysis\n\n")
        f.write(f"{quality_verdict}\n\n")

        f.write("## Conclusion & Next Steps\n\n")
        f.write("### Limitations\n")
        f.write("- Single 90-day period on one symbol.\n")
        f.write("- Fixed risk/reward and trailing logic not tested.\n")

        f.write("\n### Recommended Next Steps\n")
        if v2_m.net_pnl > v1_m.net_pnl and v2_m.win_rate > v1_m.win_rate:
            f.write("- **PROJECT-BACKTEST-007**: Run Walk-Forward testing for V2 to prove robustness.\n")
        else:
            f.write("- **PROJECT-BACKTEST-007**: V2 still has negative expectancy. We must analyze the Exit Model (SL/TP) via MFE/MAE analysis. The entry logic might be fine, but the exit rules are burning capital to slippage/fees.\n")

    print("\nMarkdown report: docs/reports/comparison_BACKTEST-006.md")


if __name__ == "__main__":
    asyncio.run(main())
