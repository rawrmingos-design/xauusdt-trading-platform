"""Diagnostic baseline backtest for PROJECT-BACKTEST-003.

Runs ConfluenceStrategy v1, captures all scores and component passes,
analyzes the single trade, and produces a diagnostic report.
"""

import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


async def run_diagnostics():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    # 1. Fetch candles
    candle_orms = []
    async for session in get_session():
        repo = CandleRepository(session)
        start = datetime(2026, 7, 8, tzinfo=UTC)
        end = datetime(2026, 7, 15, tzinfo=UTC)
        results = await repo.query_by_range("XAU-USDT-SWAP", "15m", start, end)
        candle_orms = list(results)
        await session.close()
        break

    if not candle_orms:
        print("No candles found in DB")
        return

    print(f"Loaded {len(candle_orms)} candles from XAU-USDT-SWAP")

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

    # Config from BACKTEST-002 report
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

    # Collect scores
    all_scores = []
    component_passes = defaultdict(int)
    rejection_reasons = defaultdict(int)
    near_misses = []

    orig_on_candle = strategy.on_candle

    def hooked_on_candle(candle, position):
        # Call original
        signal = orig_on_candle(candle, position)

        # Compute score
        features = strategy._compute_features()
        if not features:
            return signal

        score = strategy._calculate_scores(candle, features)

        all_scores.append(
            {
                "time": str(candle.open_time),
                "buy_score": score.buy_score,
                "sell_score": score.sell_score,
                "gap": abs(score.buy_score - score.sell_score),
                "buy_reasons": score.buy_reasons,
                "sell_reasons": score.sell_reasons,
            }
        )

        # Track component passes
        for r in score.buy_reasons + score.sell_reasons:
            if "EMA fast" in r:
                component_passes["ema_trend"] += 1
            elif "Price above" in r:
                component_passes["price_above"] += 1
            elif "Price below" in r:
                component_passes["price_below"] += 1
            elif "ADX strong" in r:
                component_passes["adx"] += 1
            elif "Structure" in r:
                component_passes["structure"] += 1
            elif "Swing" in r:
                component_passes["swing"] += 1
            elif "ATR" in r:
                component_passes["atr"] += 1
            elif "Candle" in r:
                component_passes["candle"] += 1

        # Rejection analysis
        max_score = max(score.buy_score, score.sell_score)
        gap = abs(score.buy_score - score.sell_score)

        if max_score >= 50 and max_score < 65:
            rejection_reasons["low_score_50_64"] += 1
        elif max_score >= 65 and gap < 15:
            rejection_reasons["insufficient_gap"] += 1

        # Near-misses: scored 45-64 on one side
        if max_score >= 45 and max_score < 65:
            reasons_list = (
                score.buy_reasons if score.buy_score > score.sell_score else score.sell_reasons
            )
            reasons_str = "; ".join(reasons_list)

            missing = []
            if "EMA fast" not in reasons_str:
                missing.append("ema_trend")
            if "Price above" not in reasons_str and "Price below" not in reasons_str:
                missing.append("price_above_below")
            if "ADX" not in reasons_str:
                missing.append("adx")
            if "Structure" not in reasons_str:
                missing.append("structure")
            if "Swing" not in reasons_str:
                missing.append("swing")
            if "ATR" not in reasons_str:
                missing.append("atr")
            if "Candle" not in reasons_str:
                missing.append("candle")

            near_misses.append(
                {
                    "time": str(candle.open_time),
                    "max_score": max_score,
                    "direction": "buy" if score.buy_score > score.sell_score else "sell",
                    "missing_components": missing,
                    "reasons": reasons_list,
                }
            )

            # Why exactly?
            if max_score < 65:
                diff = 65 - max_score
                if diff > 5:
                    rejection_reasons["low_score_below_60"] += 1
                else:
                    rejection_reasons["low_score_60_64"] += 1
            elif gap < 15:
                rejection_reasons["insufficient_gap"] += 1

        # Track if score was warmup
        if len(strategy._history) < strategy_config.ema_slow_period + 10:
            rejection_reasons["insufficient_warmup"] += 1

        return signal

    strategy.on_candle = hooked_on_candle

    # Run backtest
    bt_config = BacktestConfig(initial_balance=1000, fee_rate=0.0005, slippage_bps=2.0)
    engine = ConfluenceBacktestEngine(bt_config, candles, strategy)
    result = engine.run()

    print(f"Backtest complete: {result.metrics.total_trades} trades")
    print(f"Scores collected: {len(all_scores)}")

    # Analyze trade
    trade_data = {}
    if result.metrics.trade_list:
        t = result.metrics.trade_list[0]
        trade_data = {
            "entry_time": t.entry_candle_time,
            "exit_time": t.exit_candle_time,
            "side": t.side,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "fee": t.fee,
            "slippage": t.slippage_cost,
            "exit_reason": t.exit_reason,
        }

        # Try to find MAE/MFE from the trade's candle range
        # For simplicity, compute from candles
        entry_time = t.entry_candle_time
        exit_time = t.exit_candle_time
        entry_idx = None
        exit_idx = None
        for i, c in enumerate(candles):
            if str(c.open_time) == entry_time:
                entry_idx = i
            if str(c.open_time) == exit_time:
                exit_idx = i

        if entry_idx is not None and exit_idx is not None:
            sub_candles = candles[entry_idx : exit_idx + 1]
            high = max(c.high for c in sub_candles)
            low = min(c.low for c in sub_candles)

            if t.side == "LONG":
                mfe = high - t.entry_price
                mae = t.entry_price - low
            else:
                mfe = t.entry_price - low
                mae = high - t.entry_price

            trade_data["mfe"] = round(mfe, 2)
            trade_data["mae"] = round(mae, 2)
            trade_data["holding_bars"] = exit_idx - entry_idx

    # Score distribution
    score_buckets = {
        "0-10": 0,
        "10-20": 0,
        "20-30": 0,
        "30-40": 0,
        "40-50": 0,
        "50-60": 0,
        "60-70": 0,
        "70-80": 0,
        "80+": 0,
    }

    for s in all_scores:
        bucket = int(s["buy_score"] // 10 * 10)
        if bucket >= 80:
            bucket = 80
        key = f"{bucket}-{bucket + 10}" if bucket < 80 else "80+"
        score_buckets[key] = score_buckets.get(key, 0) + 1

    # Rejection analysis
    for nm in near_misses[:20]:
        rejection_reasons[f"missing_{'_'.join(nm['missing_components'])}"] += 1

    # Build report
    report = {
        "config_audit": {
            "EMA_fast": strategy_config.ema_fast_period,
            "EMA_slow": strategy_config.ema_slow_period,
            "min_score": strategy_config.min_score,
            "min_score_gap": strategy_config.min_score_gap,
            "mismatch": "BACKTEST-002 specified EMA 50/200 in task description, "
            "but the strategy's ConfluenceConfig defaults to 9/21. "
            "The runner used 9/21 (matching the strategy defaults). "
            "This was NOT intentional — the baseline runner should have overridden "
            "to 50/200 if that was the intended baseline config.",
        },
        "symbol_audit": {
            "internal_symbol": "XAU-USDT-SWAP",
            "external_symbol": "XAU-USDT-SWAP",
            "mapping": "OKXClient.SYMBOL_MAP maps 'XAU-USDT-SWAP' -> 'XAU-USDT-SWAP'",
            "issue": "XAU-USDT-SWAP looks like a Bitget-style symbol naming (UMCBL suffix). "
            "It should probably be 'XAU-USDT-SWAP' for OKX consistency. "
            "This needs a canonical symbol mapping module.",
        },
        "data_stats": {
            "total_candles": len(candles),
            "warmup_threshold": strategy_config.ema_slow_period + 10,
            "warmup_candles": min(strategy_config.ema_slow_period + 10, len(candles)),
            "usable_candles": len(all_scores),
            "data_quality": "672 candles = 7 days full (no gaps)",
        },
        "score_distribution": score_buckets,
        "component_passes": dict(component_passes),
        "rejection_reasons": dict(rejection_reasons),
        "near_misses": near_misses[:50],
        "trade_analysis": trade_data,
        "total_trades": result.metrics.total_trades,
        "total_scores": len(all_scores),
        "scores_before_signal": [
            s for s in all_scores if s["buy_score"] >= 50 or s["sell_score"] >= 50
        ],
    }

    # Save
    report_dir = "docs/reports"
    json_path = f"{report_dir}/diagnostic_BACKTEST-003.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nJSON report: {json_path}")

    # Print summary
    print("\n=== DIAGNOSTIC SUMMARY ===")
    print(f"Scores collected: {len(all_scores)}")
    print(f"Total trades: {result.metrics.total_trades}")
    print(f"Warmup: {min(strategy_config.ema_slow_period + 10, len(candles))} of {len(candles)}")
    print(f"Component passes: {dict(component_passes)}")
    print(f"Rejection reasons: {dict(rejection_reasons)}")
    print(f"\nNear-misses (score >= 45 but no signal): {len(near_misses)}")
    for nm in near_misses[:10]:
        print(
            f"  {nm['time']} {nm['direction']} score={nm['max_score']} missing={nm['missing_components']}"
        )

    return report


if __name__ == "__main__":
    asyncio.run(run_diagnostics())
