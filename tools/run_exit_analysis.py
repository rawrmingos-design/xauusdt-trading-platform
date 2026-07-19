"""Exit Model Analysis for BACKTEST-007.

Analyzes SL/TP behavior, MFE, MAE, and R-multiples for
ConfluenceStrategy v1 and v2.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "/home/devistopup13/xauusdt-platform/src")
from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy

V1_CONFIGS = [
    ConfluenceConfig(version="v1_baseline", risk_reward_ratio=rr, sl_atr_multiplier=atr)
    for rr in [1.0, 1.5, 2.0, 2.5]
    for atr in [1.0, 1.5, 2.0, 2.5]
]

V2_CONFIGS = [
    ConfluenceConfig(
        version="v2_candidate",
        adx_rising=True,
        ema_slope_alignment=True,
        adx_min=25.0,
        risk_reward_ratio=rr,
        sl_atr_multiplier=atr,
    )
    for rr in [1.0, 1.5, 2.0, 2.5]
    for atr in [1.0, 1.5, 2.0, 2.5]
]


def _run(cfg: ConfluenceConfig, candles: list[Candle]) -> list[BacktestTrade]:
    bt_config = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
    )
    engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(cfg))
    return engine.run().trades


def _analyze(trades: list[BacktestTrade]) -> dict:
    if not trades:
        return {}

    wins = [t for t in trades if t.pnl > 0]
    sl_hits = [t for t in trades if t.exit_reason == "SL"]
    tp_hits = [t for t in trades if t.exit_reason == "TP"]
    sig_closes = [t for t in trades if t.exit_reason == "SIGNAL"]

    def _r_mult(trade: BacktestTrade) -> float:
        if trade.sl_distance == 0:
            return 0.0
        return trade.pnl / trade.sl_distance

    r_multiples = [_r_mult(t) for t in trades]
    mfe_multiples = [t.max_mfe / t.sl_distance if t.sl_distance > 0 else 0.0 for t in trades]
    mae_multiples = [t.max_mae / t.sl_distance if t.sl_distance > 0 else 0.0 for t in trades]

    reached_1r = sum(1 for r in r_multiples if r >= 1.0)
    reached_2r = sum(1 for r in r_multiples if r >= 2.0)
    stopped_before_1r = sum(1 for r in r_multiples if 0 < r < 1.0)

    def _percent_in_range(lo: float, hi: float, data: list[float]) -> float:
        return (sum(1 for v in data if lo <= v < hi) / len(data)) * 100

    return {
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "net_pnl": sum(t.pnl for t in trades),
        "avg_mfe_r": sum(mfe_multiples) / len(mfe_multiples),
        "avg_mae_r": sum(mae_multiples) / len(mae_multiples),
        "max_mfe_r": max(mfe_multiples) if mfe_multiples else 0,
        "max_mae_r": max(mae_multiples) if mae_multiples else 0,
        "mfe_dist": {
            "0.0_to_0.5r": _percent_in_range(0, 0.5, mfe_multiples),
            "0.5r_to_1r": _percent_in_range(0.5, 1.0, mfe_multiples),
            "1r_to_1.5r": _percent_in_range(1.0, 1.5, mfe_multiples),
            "1.5r_to_2r": _percent_in_range(1.5, 2.0, mfe_multiples),
            "2r_to_2.5r": _percent_in_range(2.0, 2.5, mfe_multiples),
            "above_2.5r": _percent_in_range(2.5, 999, mfe_multiples),
        },
        "mae_dist": {
            "0.0_to_0.5r": _percent_in_range(0, 0.5, mae_multiples),
            "0.5r_to_1r": _percent_in_range(0.5, 1.0, mae_multiples),
            "1.0r_to_1.5r": _percent_in_range(1.0, 1.5, mae_multiples),
            "above_1.5r": _percent_in_range(1.5, 999, mae_multiples),
        },
        "sl_hit_rate": len(sl_hits) / len(trades),
        "tp_hit_rate": len(tp_hits) / len(trades),
        "sig_close_rate": len(sig_closes) / len(trades),
        "stopped_before_1r_pct": stopped_before_1r / len(trades) * 100,
        "reached_1r_pct": reached_1r / len(trades) * 100,
        "reached_2r_pct": reached_2r / len(trades) * 100,
    }


async def main() -> None:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)

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

    results_v1 = {}
    results_v2 = {}

    print("\n--- V1 EXIT VARIANTS ---")
    for cfg in V1_CONFIGS:
        trades = _run(cfg, candles)
        analysis = _analyze(trades)
        key = f"RR={cfg.risk_reward_ratio}/ATR={cfg.sl_atr_multiplier}"
        results_v1[key] = {**analysis, "config": cfg.to_dict()}
        print(
            f"  {key}: {analysis.get('total_trades', 0)} trades, "
            f"WR={analysis.get('win_rate', 0) * 100:.1f}%, "
            f"SL%={analysis.get('sl_hit_rate', 0) * 100:.1f}%, "
            f"TP%={analysis.get('tp_hit_rate', 0) * 100:.1f}%, "
            f"MFE_avg={analysis.get('avg_mfe_r', 0):.2f}R, "
            f"PnL={analysis.get('net_pnl', 0):.2f}"
        )

    print("\n--- V2 EXIT VARIANTS ---")
    for cfg in V2_CONFIGS:
        trades = _run(cfg, candles)
        analysis = _analyze(trades)
        key = f"RR={cfg.risk_reward_ratio}/ATR={cfg.sl_atr_multiplier}"
        results_v2[key] = {**analysis, "config": cfg.to_dict()}
        print(
            f"  {key}: {analysis.get('total_trades', 0)} trades, "
            f"WR={analysis.get('win_rate', 0) * 100:.1f}%, "
            f"SL%={analysis.get('sl_hit_rate', 0) * 100:.1f}%, "
            f"TP%={analysis.get('tp_hit_rate', 0) * 100:.1f}%, "
            f"MFE_avg={analysis.get('avg_mfe_r', 0):.2f}R, "
            f"PnL={analysis.get('net_pnl', 0):.2f}"
        )

    # Save JSON
    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    with open(report_dir / f"exit_analysis_BACKTEST-007_{timestamp}.json", "w") as f:
        json.dump({"v1": results_v1, "v2": results_v2}, f, indent=2, default=str)

    # Markdown
    _generate_md(results_v1, results_v2, report_dir, timestamp)

    print("\nMarkdown report: docs/reports/exit_analysis_BACKTEST-007.md")


def _generate_md(v1: dict, v2: dict, report_dir: Path, ts: str) -> None:
    with open(report_dir / "exit_analysis_BACKTEST-007.md", "w") as f:
        f.write("# Exit Model Analysis (PROJECT-BACKTEST-007)\n\n")
        f.write(f"**Date**: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write("**Dataset**: XAU-USDT-SWAP 15m (90 days)\n\n")

        f.write("## SUPERSEDED NOTICE\n\n")
        f.write(
            "Pre-PR #25 backtest reports (including BACKTEST-004 and BACKTEST-006 baseline) **do not** reflect real exit behavior because ATR-based SL/TP were not active. All exit analysis here uses the corrected engine from PR #25.\n\n"
        )

        f.write("## V1 Baseline Comparison (ATR Stop vs RR)\n\n")
        f.write(
            "| Exit Config | Trades | Win Rate | SL% | TP% | Signal% | Avg MFE | Net PnL | Max MFE | Avg MAE | Stopped <1R | Reached 1R | Reached 2R |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for key, data in v1.items():
            f.write(
                f"| {key} | {data['total_trades']} | {data['win_rate'] * 100:.1f}% | "
                f"{data['sl_hit_rate'] * 100:.1f}% | {data['tp_hit_rate'] * 100:.1f}% | "
                f"{data['sig_close_rate'] * 100:.1f}% | {data['avg_mfe_r']:.2f}R | "
                f"{data['net_pnl']:.2f} | {data['max_mfe_r']:.2f}R | "
                f"{data['avg_mae_r']:.2f}R | {data['stopped_before_1r_pct']:.1f}% | "
                f"{data['reached_1r_pct']:.1f}% | {data['reached_2r_pct']:.1f}% |\n"
            )

        f.write("\n## V2 Candidate Comparison (ATR Stop vs RR)\n\n")
        f.write(
            "| Exit Config | Trades | Win Rate | SL% | TP% | Signal% | Avg MFE | Net PnL | Max MFE | Avg MAE | Stopped <1R | Reached 1R | Reached 2R |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for key, data in v2.items():
            f.write(
                f"| {key} | {data['total_trades']} | {data['win_rate'] * 100:.1f}% | "
                f"{data['sl_hit_rate'] * 100:.1f}% | {data['tp_hit_rate'] * 100:.1f}% | "
                f"{data['sig_close_rate'] * 100:.1f}% | {data['avg_mfe_r']:.2f}R | "
                f"{data['net_pnl']:.2f} | {data['max_mfe_r']:.2f}R | "
                f"{data['avg_mae_r']:.2f}R | {data['stopped_before_1r_pct']:.1f}% | "
                f"{data['reached_1r_pct']:.1f}% | {data['reached_2r_pct']:.1f}% |\n"
            )

        # MFE Distribution Tables
        f.write("\n## MFE Distribution (V1 Baseline — 1.0ATR SL)\n\n")
        for rr in [1.0, 1.5, 2.0, 2.5]:
            key = f"RR={rr}/ATR=1.0"
            if key in v1:
                f.write(f"\n### RR {rr}x\n")
                f.write("| Range | % of Trades |\n|---|---|\n")
                for rng, pct in v1[key]["mfe_dist"].items():
                    f.write(f"| {rng}R | {pct:.1f}% |\n")

        f.write("\n## MFE Distribution (V2 Candidate — 1.0ATR SL)\n\n")
        for rr in [1.0, 1.5, 2.0, 2.5]:
            key = f"RR={rr}/ATR=1.0"
            if key in v2:
                f.write(f"\n### RR {rr}x\n")
                f.write("| Range | % of Trades |\n|---|---|\n")
                for rng, pct in v2[key]["mfe_dist"].items():
                    f.write(f"| {rng}R | {pct:.1f}% |\n")

        # Best V1 and V2
        best_v1 = max(v1.items(), key=lambda x: x[1].get("net_pnl", -99999))
        best_v2 = max(v2.items(), key=lambda x: x[1].get("net_pnl", -99999))

        f.write("\n## Best Exit Configuration\n\n")
        f.write(f"- **V1 Best PnL**: `{best_v1[0]}` (Net PnL {best_v1[1]['net_pnl']:.2f})\n")
        f.write(f"- **V2 Best PnL**: `{best_v2[0]}` (Net PnL {best_v2[1]['net_pnl']:.2f})\n\n")

        # Analysis
        f.write("## Trade Quality Analysis\n\n")
        f.write("### Key Metrics Explained\n")
        f.write(
            "- **Avg MFE**: Average Maximum Favorable Excursion in R. If Avg MFE > RR, most trades went deep into profit before hitting SL.\n"
        )
        f.write(
            "- **Max MFE**: Best case R reached. If Max MFE >> RR, the strategy often had winners that didn't make it to TP.\n"
        )
        f.write(
            "- **Stopped Before 1R %**: % of trades that reversed and hit SL before making 1R. High % = SL too tight.\n"
        )
        f.write(
            "- **Reached 2R %**: % of trades that made it past 2R target. Low % = TP is too far.\n"
        )

        # Recommendation
        f.write("\n## Recommended Exit Model Changes\n\n")
        f.write("Based on BACKTEST-007 data:\n")
        if best_v1[1].get("avg_mfe_r", 0) > best_v1[0].split("/")[0].split("=")[1]:
            f.write(
                "- **SL too tight**: Avg MFE is significantly higher than RR target. Many trades reverse early.\n"
            )
        if best_v1[1].get("stopped_before_1r_pct", 0) > 60:
            f.write(
                f"- **TP too far**: {best_v1[1]['stopped_before_1r_pct']:.0f}% of trades were stopped before reaching 1R.\n"
            )
        f.write(
            "- **Recommended Next**: `PROJECT-STRATEGY-003` — Implement improved exit model (e.g. partial TP at 1.5R, ATR stop at 2.0x, break-even after 1R).\n"
        )

        f.write("\n## Known Limitations\n\n")
        f.write("- Single 90-day period on one symbol.\n")
        f.write("- MFE/MAE computed on candle close, not intra-candle wick extreme.\n")
        f.write("- No slippage/fee impact modeled in MFE calculation.\n")
        f.write("- Fixed position sizing; real trading may vary.\n")


if __name__ == "__main__":
    asyncio.run(main())
