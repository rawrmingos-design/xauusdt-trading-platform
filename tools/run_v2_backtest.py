"""Run Strategy V2 Backtest."""

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


async def main() -> None:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    print("Loading candles...")
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range(
            "XAU-USDT-SWAP",
            "15m",
            limit=100000,
        )
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

    start_dt = min(c.open_time for c in candles)
    end_dt = max(c.open_time for c in candles)
    prices = [c.close for c in candles]
    print(f"Date range: {start_dt.date()} to {end_dt.date()}")
    print(f"Price range: {min(prices):.2f} - {max(prices):.2f}\n")

    print("=== CONFIGURING V2 ===")
    strategy_config = ConfluenceConfig(
        ema_fast_period=50,
        ema_slow_period=200,
        min_score=65.0,
        min_score_gap=15.0,
        # V2 Quality Filters
        adx_min=25.0,
        adx_rising=True,
        ema_slope_alignment=True,
        # Risk (using 2.5x RR as recommended)
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.5,
        version="v2",
    )

    print(
        f"Filters: ADX Rising={strategy_config.adx_rising}, EMA Slope={strategy_config.ema_slope_alignment}"
    )
    print(f"Thresholds: ADX Min={strategy_config.adx_min}, Min Score={strategy_config.min_score}")
    print(f"Risk: RR={strategy_config.risk_reward_ratio}x\n")

    bt_config = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
    )

    strategy = ConfluenceStrategy(strategy_config)
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)

    print("Running backtest...")
    result = engine.run()
    m = result.metrics

    print("\n=== V2 REPORT ===")
    print(f"Trades: {m.total_trades}")
    print(f"Win rate: {m.win_rate * 100:.1f}%")
    print(f"Net PnL: {m.net_pnl:.2f}")
    print(f"Final balance: {m.final_balance:.2f}")
    print(f"Profit factor: {m.profit_factor:.2f}")
    print(f"Max drawdown: {m.max_drawdown:.2f} ({m.max_drawdown_pct * 100:.1f}%)")
    print(f"Expectancy: {m.expectancy:.2f}")

    # Analyze entry types
    longs = sum(1 for t in m.trade_list if t.side == "LONG")
    shorts = sum(1 for t in m.trade_list if t.side == "SHORT")
    print(f"Longs/Shorts: {longs} / {shorts}")

    # Generate JSON
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_file = report_dir / f"strategy_v2_baseline_{timestamp}.json"

    report_data = {
        "strategy": "ConfluenceStrategy",
        "version": strategy_config.version,
        "config": strategy_config.to_dict(),
        "metrics": {
            "total_trades": m.total_trades,
            "win_rate": m.win_rate,
            "net_pnl": m.net_pnl,
            "profit_factor": m.profit_factor,
            "max_drawdown": m.max_drawdown,
            "max_drawdown_pct": m.max_drawdown_pct,
            "expectancy": m.expectancy,
            "longs": longs,
            "shorts": shorts,
        },
    }
    with open(json_file, "w") as f:
        json.dump(report_data, f, indent=2)

    # Markdown
    md_file = report_dir / f"strategy_v2_baseline_{timestamp}.md"
    with open(md_file, "w") as f:
        f.write("# Strategy V2 Baseline Backtest Report\n\n")
        f.write(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write("## Data\n\n")
        f.write("- **Symbol**: XAU-USDT-SWAP\n")
        f.write("- **Granularity**: 15m\n")
        f.write(f"- **Range**: {start_dt.date()} to {end_dt.date()}\n")
        f.write(f"- **Candle count**: {len(candles)}\n\n")

        f.write("## V2 Configuration\n\n")
        f.write(f"- ADX Rising Filter: {strategy_config.adx_rising}\n")
        f.write(f"- EMA Slope Alignment: {strategy_config.ema_slope_alignment}\n")
        f.write(f"- ADX Threshold: {strategy_config.adx_min}\n")
        f.write(f"- Risk/Reward Ratio: {strategy_config.risk_reward_ratio}\n")
        f.write(f"- Min Score: {strategy_config.min_score}\n\n")

        f.write("## Results\n\n")
        f.write("| Metric | Value |\n|---|---|\n")
        f.write(f"| Net PnL | {m.net_pnl:.2f} |\n")
        f.write(f"| Total Trades | {m.total_trades} |\n")
        f.write(f"| Win Rate | {m.win_rate * 100:.1f}% |\n")
        f.write(f"| Profit Factor | {m.profit_factor:.2f} |\n")
        f.write(f"| Max Drawdown | {m.max_drawdown:.2f} ({m.max_drawdown_pct * 100:.1f}%) |\n")
        f.write(f"| Expectancy | {m.expectancy:.2f} |\n\n")

    print(f"\nMarkdown report: {md_file}")


if __name__ == "__main__":
    asyncio.run(main())
