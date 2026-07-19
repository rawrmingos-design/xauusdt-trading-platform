import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy, make_v3_config


def calculate_r_multiple(trade: BacktestTrade) -> float:
    """Calculate R-multiple for a trade."""
    if trade.sl_distance <= 0:
        return 0.0
    return trade.gross_pnl / (trade.quantity * trade.sl_distance)


def analyze_trades(trades: list[BacktestTrade], metrics) -> dict:
    if not trades:
        return {}

    longs = [t for t in trades if t.side == "LONG"]
    shorts = [t for t in trades if t.side == "SHORT"]

    # Calculate expectancy via R-multiples (avoid compounding distortion)
    r_multiples = [calculate_r_multiple(t) for t in trades]
    expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    long_r = [calculate_r_multiple(t) for t in longs]
    short_r = [calculate_r_multiple(t) for t in shorts]

    long_expectancy = sum(long_r) / len(long_r) if long_r else 0.0
    short_expectancy = sum(short_r) / len(short_r) if short_r else 0.0

    return {
        "trade_count": len(trades),
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
        "net_pnl": metrics.net_pnl,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "expectancy_r": expectancy_r,
        "long_breakdown": {
            "count": len(longs),
            "win_rate": len([t for t in longs if t.pnl > 0]) / len(longs) if longs else 0.0,
            "expectancy_r": long_expectancy,
        },
        "short_breakdown": {
            "count": len(shorts),
            "win_rate": len([t for t in shorts if t.pnl > 0]) / len(shorts) if shorts else 0.0,
            "expectancy_r": short_expectancy,
        },
    }


def analyze_v3_discards(
    v2_trades: list[BacktestTrade], candles: list[Candle], v3_cfg: ConfluenceConfig
) -> dict:
    """
    Simulate what V3 would do on the exact entry candles that V2 took.
    This tells us exactly which winning and losing trades from V2 were prevented by V3 filters.
    """
    # Toxic zone: 75 - 84
    lo = v3_cfg.v3_toxic_score_min
    hi = v3_cfg.v3_toxic_score_max

    # ADX bounds
    min_adx = v3_cfg.v3_min_adx
    max_adx = v3_cfg.v3_max_adx

    toxic_discards = []
    adx_min_discards = []
    adx_max_discards = []

    for t in v2_trades:
        score = t.context_score
        adx = t.context_adx

        discarded = False
        if v3_cfg.v3_reject_toxic_score and lo <= score <= hi:
            toxic_discards.append(t)
            discarded = True

        if not discarded and adx < min_adx:
            adx_min_discards.append(t)
            discarded = True

        if not discarded and adx > max_adx:
            adx_max_discards.append(t)

    def _stats(t_list):
        return {
            "count": len(t_list),
            "winners": len([t for t in t_list if t.pnl > 0]),
            "losers": len([t for t in t_list if t.pnl <= 0]),
        }

    return {
        "toxic_zone": _stats(toxic_discards),
        "adx_min": _stats(adx_min_discards),
        "adx_max": _stats(adx_max_discards),
    }


async def main():
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

    print("Loading 90-day dataset...")
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
    print(f"Loaded {len(candles)} candles.")
    if not candles:
        return

    bt_config = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,  # 100% balance
    )

    # 1. Run V1 Baseline
    print("Running V1 Baseline...")
    # NOTE: To be a fair apples-to-apples comparison of entry logic, we use
    # the improved exit model consistently across variants unless we want to see old V1.
    # We will use improved_exit=True for all to isolate ENTRY filters.
    v1_cfg = ConfluenceConfig(
        version="v1_improved_exit",
        ema_fast_period=50,
        ema_slow_period=200,
        min_score=65.0,
        min_score_gap=15.0,
        adx_min=20.0,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.0,
        improved_exit=True,
    )
    v1_engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(v1_cfg))
    v1_res = v1_engine.run()
    v1_analysis = analyze_trades(v1_res.trades, v1_res.metrics)

    # 2. Run V2 Quality Filter Strategy
    print("Running V2 Strategy...")
    v2_cfg = ConfluenceConfig(
        version="v2_improved_exit",
        adx_min=25.0,
        adx_rising=True,
        ema_slope_alignment=True,
        sl_atr_multiplier=1.5,
        risk_reward_ratio=2.5,
        improved_exit=True,
    )
    v2_engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(v2_cfg))
    v2_res = v2_engine.run()
    v2_analysis = analyze_trades(v2_res.trades, v2_res.metrics)

    # 3. Run V3 Experimental Strategy
    print("Running V3 Experimental Strategy...")
    v3_cfg = make_v3_config()
    # Align base params with v2 to isolate the new v3 filters
    v3_cfg.adx_rising = True
    v3_cfg.ema_slope_alignment = True
    v3_cfg.sl_atr_multiplier = 1.5
    v3_cfg.risk_reward_ratio = 2.5

    v3_engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(v3_cfg))
    v3_res = v3_engine.run()
    v3_analysis = analyze_trades(v3_res.trades, v3_res.metrics)

    # Calculate V3 Discards from V2 baseline
    discards = analyze_v3_discards(v2_res.trades, candles, v3_cfg)

    # Prepare JSON Output
    report_data = {
        "metadata": {
            "dataset": "XAU-USDT-SWAP 15m (90-day)",
            "candle_count": len(candles),
            "start_time": candles[0].open_time.isoformat() if candles else None,
            "end_time": candles[-1].open_time.isoformat() if candles else None,
            "backtest_config": {
                "initial_balance": bt_config.initial_balance,
                "fee_rate": bt_config.fee_rate,
                "slippage_bps": bt_config.slippage_bps,
            },
        },
        "v1": {
            "config": v1_cfg.to_dict(),
            "metrics": v1_analysis,
        },
        "v2": {
            "config": v2_cfg.to_dict(),
            "metrics": v2_analysis,
        },
        "v3": {
            "config": v3_cfg.to_dict(),
            "metrics": v3_analysis,
            "discard_analysis_from_v2": discards,
        },
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"comparison_BACKTEST-011_{ts}.json"

    with open(json_path, "w") as f:
        json.dump(report_data, f, indent=2)

    # Generate Markdown Output
    md_path = report_dir / "comparison_BACKTEST-011.md"
    md_content = f"""# PROJECT-BACKTEST-011: V1 vs V2 vs V3 Comparison

**Objective**: Determine whether v3 entry-quality filters (from BACKTEST-010) improve expectancy, risk profile, and trade quality compared to v1/v2, rather than just reducing trade counts.

## Test Conditions
- **Symbol**: XAU-USDT-SWAP
- **Granularity**: 15m
- **Data Range**: {report_data["metadata"]["start_time"]} to {report_data["metadata"]["end_time"]} ({len(candles)} candles)
- **Fee**: {bt_config.fee_rate * 100}%
- **Slippage**: {bt_config.slippage_bps} bps
- **Exit Logic**: 'improved_exit=True' applied uniformly across all variants to strictly isolate entry-filter performance.

## High-Level Metrics

| Metric | V1 (Baseline) | V2 (Strict Trend) | V3 (Quality Filters) |
|--------|---------------|-------------------|----------------------|
| **Trade Count** | {v1_analysis.get("trade_count", 0)} | {v2_analysis.get("trade_count", 0)} | {v3_analysis.get("trade_count", 0)} |
| **Win Rate** | {v1_analysis.get("win_rate", 0) * 100:.1f}% | {v2_analysis.get("win_rate", 0) * 100:.1f}% | {v3_analysis.get("win_rate", 0) * 100:.1f}% |
| **Profit Factor** | {v1_analysis.get("profit_factor", 0):.2f} | {v2_analysis.get("profit_factor", 0):.2f} | {v3_analysis.get("profit_factor", 0):.2f} |
| **Expectancy (R)** | {v1_analysis.get("expectancy_r", 0):.2f}R | {v2_analysis.get("expectancy_r", 0):.2f}R | {v3_analysis.get("expectancy_r", 0):.2f}R |
| **Net PnL** | ${v1_analysis.get("net_pnl", 0):.2f} | ${v2_analysis.get("net_pnl", 0):.2f} | ${v3_analysis.get("net_pnl", 0):.2f} |
| **Max Drawdown** | {v1_analysis.get("max_drawdown_pct", 0) * 100:.1f}% | {v2_analysis.get("max_drawdown_pct", 0) * 100:.1f}% | {v3_analysis.get("max_drawdown_pct", 0) * 100:.1f}% |

## Long / Short Breakdown

| Direction | V1 | V2 | V3 |
|-----------|----|----|----|
| **LONG WR** | {v1_analysis["long_breakdown"]["win_rate"] * 100:.1f}% ({v1_analysis["long_breakdown"]["count"]}) | {v2_analysis["long_breakdown"]["win_rate"] * 100:.1f}% ({v2_analysis["long_breakdown"]["count"]}) | {v3_analysis["long_breakdown"]["win_rate"] * 100:.1f}% ({v3_analysis["long_breakdown"]["count"]}) |
| **LONG Exp(R)** | {v1_analysis["long_breakdown"]["expectancy_r"]:.2f}R | {v2_analysis["long_breakdown"]["expectancy_r"]:.2f}R | {v3_analysis["long_breakdown"]["expectancy_r"]:.2f}R |
| **SHORT WR** | {v1_analysis["short_breakdown"]["win_rate"] * 100:.1f}% ({v1_analysis["short_breakdown"]["count"]}) | {v2_analysis["short_breakdown"]["win_rate"] * 100:.1f}% ({v2_analysis["short_breakdown"]["count"]}) | {v3_analysis["short_breakdown"]["win_rate"] * 100:.1f}% ({v3_analysis["short_breakdown"]["count"]}) |
| **SHORT Exp(R)** | {v1_analysis["short_breakdown"]["expectancy_r"]:.2f}R | {v2_analysis["short_breakdown"]["expectancy_r"]:.2f}R | {v3_analysis["short_breakdown"]["expectancy_r"]:.2f}R |

## V3 Filter Discards (Applied over V2 trades)

*What would V3 have rejected if it evaluated V2's exact entries?*

| Filter | Total Discarded | Winners Saved (Bad) | Losers Saved (Good) | Net Edge |
|--------|-----------------|---------------------|---------------------|----------|
| **Toxic Zone [75-84]** | {discards["toxic_zone"]["count"]} | {discards["toxic_zone"]["winners"]} | {discards["toxic_zone"]["losers"]} | {"Positive" if discards["toxic_zone"]["losers"] > discards["toxic_zone"]["winners"] else "Negative/Neutral"} |
| **ADX < 15** | {discards["adx_min"]["count"]} | {discards["adx_min"]["winners"]} | {discards["adx_min"]["losers"]} | {"Positive" if discards["adx_min"]["losers"] > discards["adx_min"]["winners"] else "Negative/Neutral"} |
| **ADX > 40** | {discards["adx_max"]["count"]} | {discards["adx_max"]["winners"]} | {discards["adx_max"]["losers"]} | {"Positive" if discards["adx_max"]["losers"] > discards["adx_max"]["winners"] else "Negative/Neutral"} |

## Analysis & Conclusion

### Did V3 improve Expectancy?
{"**YES**. Expectancy improved from V2." if v3_analysis.get("expectancy_r", 0) > v2_analysis.get("expectancy_r", 0) else "**NO**. Expectancy degraded or stagnated compared to V2. V3 merely reduced exposure."}

### Did V3 improve Drawdown?
{"**YES**. Max Drawdown was reduced." if v3_analysis.get("max_drawdown_pct", 100) < v2_analysis.get("max_drawdown_pct", 100) else "**NO**. Drawdown remained the same or worsened."}

### Filter Efficacy
{"The toxic zone filter successfully removed more losers than winners." if discards["toxic_zone"]["losers"] > discards["toxic_zone"]["winners"] else "The toxic zone filter removed too many winners and must be retuned or discarded."}

### Known Limitations
- V3 long-bias penalty was kept at 0 by default; LONG entries may still exhibit negative drag.
- Comparison covers a single 90-day period.

### Recommended Next Steps
- (To be determined by Tech Lead based on above metrics)

---
*Generated by `tools/run_backtest_011.py`*
"""

    with open(md_path, "w") as f:
        f.write(md_content)

    print(f"Report generated: {md_path}")
    print(f"JSON data saved: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
