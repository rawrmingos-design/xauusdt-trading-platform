"""Run baseline confluence backtest on OKX XAU-USDT-SWAP."""

import sys
import asyncio

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from datetime import UTC, datetime
from pathlib import Path

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.storage.database import init_db
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


async def main():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    # Load candles
    from xauusdt.storage.database import get_session

    candle_orms = []
    async for session in get_session():
        repo = CandleRepository(session)

        start = datetime(2026, 7, 8, tzinfo=UTC)
        end = datetime(2026, 7, 15, tzinfo=UTC)
        results = await repo.query_by_range("XAUUSDT_UMCBL", "15m", start, end)
        candle_orms = list(results)
        await session.close()
        break

    if not candle_orms:
        print("ERROR: No candles found")
        return

    print(f"Loaded {len(candle_orms)} candles")

    # Convert to Candle model
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

    start_dt = candle_orms[0].open_time
    end_dt = candle_orms[-1].open_time

    print(f"Date range: {start_dt.date()} to {end_dt.date()}")
    print(f"Price range: {candle_orms[0].open_price:.2f} - {candle_orms[-1].close:.2f}")

    # Config
    bt_config = BacktestConfig(initial_balance=1000, fee_rate=0.0005, slippage_bps=2.0)

    strategy_config = ConfluenceConfig(
        min_score=65.0,
        min_score_gap=15.0,
        ema_fast_period=9,
        ema_slow_period=21,
        adx_min=20.0,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.0,
    )

    strategy = ConfluenceStrategy(strategy_config)

    # Run backtest
    print("\nRunning backtest...")
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)
    result = engine.run()

    m = result.metrics
    print(f"\n=== BASELINE REPORT ===")
    print(f"Candles: {len(candles)}")
    print(f"Trades: {m.total_trades}")
    print(f"Win trades: {m.win_trades}")
    print(f"Loss trades: {m.loss_trades}")
    print(f"Win rate: {m.win_rate * 100:.1f}%")
    print(f"Net PnL: {m.net_pnl:.2f}")
    print(f"Final balance: {m.final_balance:.2f}")
    print(f"Profit factor: {m.profit_factor:.2f}")
    print(f"Max drawdown: {m.max_drawdown:.2f} ({m.max_drawdown_pct * 100:.1f}%)")
    print(f"Expectancy: {m.expectancy:.2f}")
    
    # Save report files
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    # JSON
    import json
    report_data = {
        "data": {
            "symbol": "XAU-USDT-SWAP",
            "granularity": "15m",
            "start": str(start_dt),
            "end": str(end_dt),
            "candle_count": len(candles),
        },
        "config": {
            "backtest": bt_config.to_dict(),
            "strategy": strategy_config.to_dict(),
        },
        "metrics": {
            "net_pnl": m.net_pnl,
            "final_balance": m.final_balance,
            "total_trades": m.total_trades,
            "win_trades": m.win_trades,
            "loss_trades": m.loss_trades,
            "win_rate": m.win_rate,
            "profit_factor": m.profit_factor,
            "max_drawdown": m.max_drawdown,
            "max_drawdown_pct": m.max_drawdown_pct,
            "expectancy": m.expectancy,
        },
    }
    
    json_file = report_dir / f"baseline_{timestamp}.json"
    with open(json_file, "w") as f:
        json.dump(report_data, f, indent=2)
    print(f"\nJSON report: {json_file}")
    
    # Markdown
    md_file = report_dir / f"baseline_{timestamp}.md"
    with open(md_file, "w") as f:
        f.write(f"# Baseline Confluence Backtest Report\n\n")
        f.write(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write("## Data\n\n")
        f.write(f"- **Symbol**: XAU-USDT-SWAP\n")
        f.write(f"- **Granularity**: 15m\n")
        f.write(f"- **Range**: {start_dt.date()} to {end_dt.date()}\n")
        f.write(f"- **Candle count**: {len(candles)}\n\n")
        f.write("## Configuration\n\n")
        f.write(f"- Initial balance: {bt_config.initial_balance}\n")
        f.write(f"- Fee rate: {bt_config.fee_rate * 100:.2f}%\n")
        f.write(f"- Slippage: {bt_config.slippage_bps} bps\n")
        f.write(f"- Min score: {strategy_config.min_score}\n")
        f.write(f"- Min score gap: {strategy_config.min_score_gap}\n")
        f.write(f"- EMA: {strategy_config.ema_fast_period}/{strategy_config.ema_slow_period}\n")
        f.write(f"- ADX threshold: {strategy_config.adx_min}\n")
        f.write(f"- ATR SL multiplier: {strategy_config.sl_atr_multiplier}\n")
        f.write(f"- Risk/Reward: {strategy_config.risk_reward_ratio}\n\n")
        f.write("## Results\n\n")
        f.write(f"| Metric | Value |\n|---|---|\n")
        f.write(f"| Net PnL | {m.net_pnl:.2f} |\n")
        f.write(f"| Final Balance | {m.final_balance:.2f} |\n")
        f.write(f"| Total Trades | {m.total_trades} |\n")
        f.write(f"| Win Rate | {m.win_rate * 100:.1f}% |\n")
        f.write(f"| Profit Factor | {m.profit_factor:.2f} |\n")
        f.write(f"| Max Drawdown | {m.max_drawdown:.2f} ({m.max_drawdown_pct * 100:.1f}%) |\n")
        f.write(f"| Expectancy | {m.expectancy:.2f} |\n\n")
        f.write("## Known Limitations\n\n")
        f.write("- 7-day data range\n- 15m granularity only\n- Confluence v1 (not optimized)\n- No parameter optimization\n")
    print(f"Markdown report: {md_file}")


if __name__ == "__main__":
    asyncio.run(main())
