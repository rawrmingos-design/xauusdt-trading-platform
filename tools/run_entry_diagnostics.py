#!/usr/bin/env python3
"""
Entry signal diagnostics for confluence strategy (PROJECT-BACKTEST-010).
"""

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def run_backtest(cfg: ConfluenceConfig, candles: list[Candle]):
    strategy = ConfluenceStrategy(cfg)
    bt_config = BacktestConfig(initial_balance=10000.0, fee_rate=0.0005, slippage_bps=2.0)
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)
    return engine.run()


def bucketize_score(trades, bucket_size=5):
    """Group trades by score bucket."""
    groups = defaultdict(list)
    for t in trades:
        bucket = int(t.context_score // bucket_size) * bucket_size
        groups[bucket].append(t)
    return groups


def bucketize(trades, field, bucket_fn):
    """Group trades by arbitrary field and bucket function."""
    groups = defaultdict(list)
    for t in trades:
        key = bucket_fn(getattr(t, field))
        groups[key].append(t)
    return groups


def compute_stats(group):
    wins = [t for t in group if t.pnl > 0]
    losses = [t for t in group if t.pnl <= 0]
    total_pnl = sum(t.pnl for t in group)
    avg_pnl = total_pnl / len(group) if group else 0.0
    win_rate = len(wins) / len(group) if group else 0.0

    # Compute realized R for full exits only
    full_exits = [t for t in group if not t.is_partial]
    avg_r = 0.0
    if full_exits:
        rs = [(t.gross_pnl / (t.quantity * t.sl_distance)) for t in full_exits if t.sl_distance > 0]
        avg_r = sum(rs) / len(rs) if rs else 0.0

    return {
        "trades": len(group),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": total_pnl,
        "avg_pnl": avg_pnl,
        "win_rate": win_rate,
        "expectancy": avg_pnl,
        "avg_realized_r": avg_r,
    }


async def load_candles() -> list[Candle]:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    candle_orms = []
    async for session in get_session():
        repo = CandleRepository(session)
        results = await repo.query_by_range("XAU-USDT-SWAP", "15m", limit=100000)
        candle_orms = list(results)
        await session.close()
        break

    return [
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


async def main():
    candles = await load_candles()
    if not candles:
        print("ERROR: No candles found")
        return

    start_dt = datetime(2026, 4, 16, tzinfo=UTC)
    end_dt = datetime(2026, 7, 14, 23, 59, 59, tzinfo=UTC)
    base_candles = [c for c in candles if start_dt <= c.open_time <= end_dt]
    print(f"Loaded {len(base_candles)} candles.")

    v1_cfg = ConfluenceConfig(version="v1_diag", ema_fast_period=50, ema_slow_period=200, risk_reward_ratio=2.0, sl_atr_multiplier=2.0, min_score=65.0, improved_exit=True)
    v2_cfg = ConfluenceConfig(version="v2_diag", ema_fast_period=50, ema_slow_period=200, adx_min=25.0, adx_rising=True, ema_slope_alignment=True, risk_reward_ratio=2.5, sl_atr_multiplier=2.5, min_score=65.0, improved_exit=True)

    v1_res = run_backtest(v1_cfg, base_candles)
    v2_res = run_backtest(v2_cfg, base_candles)

    def analyze(name, trades):
        report = {}

        # Score bucket
        score_groups = bucketize_score(trades)
        report["by_score"] = {k: compute_stats(v) for k, v in score_groups.items()}

        # ADX bucket
        adx_groups = bucketize(trades, "context_adx", lambda x: int(x // 5) * 5)
        report["by_adx"] = {k: compute_stats(v) for k, v in adx_groups.items()}

        # EMA Conflict
        conflict_groups = bucketize(trades, "context_conflict", lambda x: str(x))
        report["by_ema_conflict"] = {k: compute_stats(v) for k, v in conflict_groups.items()}

        # Long vs Short
        side_groups = bucketize(trades, "side", lambda x: str(x))
        report["by_side"] = {k: compute_stats(v) for k, v in side_groups.items()}

        # Swing Recency
        recency_groups = bucketize(trades, "context_swing_recency", lambda x: int(x // 10) * 10)
        report["by_recency"] = {k: compute_stats(v) for k, v in recency_groups.items()}

        # Structure
        struct_groups = bucketize(trades, "context_structure", lambda x: str(x))
        report["by_structure"] = {k: compute_stats(v) for k, v in struct_groups.items()}
        return report

    v1_diag = analyze("V1", v1_res.trades)
    v2_diag = analyze("V2", v2_res.trades)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    with open(report_dir / f"diagnostics_BACKTEST-010_{ts}.json", "w") as f:
        json.dump({"v1": v1_diag, "v2": v2_diag}, f, indent=2, default=str)

    # Print top entries for quick review
    print("\n=== V1 Score Buckets ===")
    for k in sorted(v1_diag["by_score"]):
        s = v1_diag["by_score"][k]
        print(f"Score {k}-{k+4}: {s['trades']} trades, PnL ${s['net_pnl']:.2f}, WR {s['win_rate']*100:.1f}%")

    print("\n=== V2 Score Buckets ===")
    for k in sorted(v2_diag["by_score"]):
        s = v2_diag["by_score"][k]
        print(f"Score {k}-{k+4}: {s['trades']} trades, PnL ${s['net_pnl']:.2f}, WR {s['win_rate']*100:.1f}%")

    print(f"\nSaved diagnostics to {report_dir}")

if __name__ == "__main__":
    asyncio.run(main())
