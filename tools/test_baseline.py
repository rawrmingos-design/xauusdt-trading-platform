"""Quick baseline test."""

import asyncio
import sys

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from datetime import UTC, datetime

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


async def main():
    # Create test candles (simple uptrend)
    candles = [
        Candle(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            open_time=datetime(2026, 7, 8, tzinfo=UTC)
            + __import__("datetime", fromlist=["timedelta"]).timedelta(minutes=i * 15),
            open=100.0 + i * 0.5,
            high=101.0 + i * 0.5,
            low=99.5 + i * 0.4,
            close=100.5 + i * 0.6,
            volume=100.0,
            quote_volume=10000.0,
        )
        for i in range(100)
    ]

    print(f"Created {len(candles)} test candles")

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

    # Run
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)
    result = engine.run()

    m = result.metrics
    print("\n=== Test Backtest Result ===")
    print(f"Candles: {len(candles)}")
    print(f"Trades: {m.total_trades}")
    print(f"Win rate: {m.win_rate * 100:.1f}%")
    print(f"Net PnL: {m.net_pnl:.2f}")
    print(f"Final balance: {m.final_balance:.2f}")
    print(f"Profit factor: {m.profit_factor:.2f}")
    print(f"Max drawdown: {m.max_drawdown:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
