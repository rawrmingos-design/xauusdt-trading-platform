#!/usr/bin/env python3
"""PROJECT-BACKTEST-013: Market regime analysis for v3_candidate.

Goal: explain why walk-forward Window 2 is negative for the V3 candidate
without re-optimizing parameters.

This is diagnostics only:
- fixed candidate config from STRATEGY-005 / BACKTEST-012
- same 90-day XAU-USDT-SWAP 15m stored candles
- no live API
- no production default changes
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from statistics import mean, median

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import ConfluenceStrategy, make_v3_candidate_config


def calc_r(trade: BacktestTrade) -> float:
    if trade.sl_distance <= 0 or trade.quantity <= 0:
        return 0.0
    return trade.gross_pnl / (trade.quantity * trade.sl_distance)


def group_by_entry(trades: list[BacktestTrade]) -> list[list[BacktestTrade]]:
    """Group multi-leg exits into one entry signal."""
    ordered = sorted(trades, key=lambda t: t.entry_candle_time)
    return [list(g) for _, g in groupby(ordered, key=lambda t: t.entry_candle_time)]


def entry_stats(trades: list[BacktestTrade]) -> dict:
    groups = group_by_entry(trades)
    if not groups:
        return {
            "entries": 0,
            "trade_legs": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "expectancy_r": 0.0,
            "long_entries": 0,
            "short_entries": 0,
            "long_r": 0.0,
            "short_r": 0.0,
            "avg_score": 0.0,
            "avg_adx": 0.0,
            "exit_reasons": {},
            "score_buckets": {},
            "adx_buckets": {},
            "structure_buckets": {},
            "ema_trend_buckets": {},
            "conflict_rate": 0.0,
        }

    entry_rs: list[float] = []
    long_rs: list[float] = []
    short_rs: list[float] = []
    scores: list[float] = []
    adxs: list[float] = []
    exit_counter: Counter[str] = Counter()
    score_buckets: dict[str, list[float]] = defaultdict(list)
    adx_buckets: dict[str, list[float]] = defaultdict(list)
    structure_buckets: dict[str, list[float]] = defaultdict(list)
    ema_buckets: dict[str, list[float]] = defaultdict(list)
    conflicts = 0

    for legs in groups:
        first = legs[0]
        total_r = sum(calc_r(t) for t in legs)
        entry_rs.append(total_r)
        scores.append(first.context_score)
        adxs.append(first.context_adx)
        if first.context_conflict:
            conflicts += 1

        side = first.side
        if side == "LONG":
            long_rs.append(total_r)
        else:
            short_rs.append(total_r)

        for t in legs:
            exit_counter[t.exit_reason] += 1

        score_key = _score_bucket(first.context_score)
        adx_key = _adx_bucket(first.context_adx)
        score_buckets[score_key].append(total_r)
        adx_buckets[adx_key].append(total_r)
        structure_buckets[first.context_structure or "UNKNOWN"].append(total_r)
        ema_buckets[first.context_ema_trend or "UNKNOWN"].append(total_r)

    def bucket_summary(buckets: dict[str, list[float]]) -> dict:
        out = {}
        for k, vals in sorted(buckets.items()):
            out[k] = {
                "entries": len(vals),
                "expectancy_r": mean(vals) if vals else 0.0,
                "win_rate": sum(1 for v in vals if v > 0) / len(vals) if vals else 0.0,
            }
        return out

    wins = sum(1 for r in entry_rs if r > 0)
    return {
        "entries": len(groups),
        "trade_legs": len(trades),
        "win_rate": wins / len(groups),
        "net_pnl": sum(t.pnl for t in trades),
        "expectancy_r": mean(entry_rs),
        "long_entries": len(long_rs),
        "short_entries": len(short_rs),
        "long_r": mean(long_rs) if long_rs else 0.0,
        "short_r": mean(short_rs) if short_rs else 0.0,
        "avg_score": mean(scores) if scores else 0.0,
        "avg_adx": mean(adxs) if adxs else 0.0,
        "median_adx": median(adxs) if adxs else 0.0,
        "exit_reasons": dict(exit_counter),
        "score_buckets": bucket_summary(score_buckets),
        "adx_buckets": bucket_summary(adx_buckets),
        "structure_buckets": bucket_summary(structure_buckets),
        "ema_trend_buckets": bucket_summary(ema_buckets),
        "conflict_rate": conflicts / len(groups),
    }


def _score_bucket(score: float) -> str:
    if score < 65:
        return "<65"
    if score < 70:
        return "65-69"
    if score < 75:
        return "70-74"
    if score < 80:
        return "75-79"
    if score < 85:
        return "80-84"
    if score < 90:
        return "85-89"
    return "90+"


def _adx_bucket(adx: float) -> str:
    if adx < 15:
        return "<15"
    if adx < 20:
        return "15-20"
    if adx < 25:
        return "20-25"
    if adx < 30:
        return "25-30"
    if adx < 35:
        return "30-35"
    if adx < 40:
        return "35-40"
    if adx < 45:
        return "40-45"
    return "45+"


def market_regime(candles: list[Candle]) -> dict:
    if not candles:
        return {}
    opens = [c.open for c in candles]
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    ranges = [h - lo for h, lo in zip(highs, lows, strict=True)]
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])

    start = opens[0]
    end = closes[-1]
    net_return = (end - start) / start if start else 0.0
    peak = closes[0]
    max_dd = 0.0
    for px in closes:
        peak = max(peak, px)
        dd = (peak - px) / peak if peak else 0.0
        max_dd = max(max_dd, dd)

    # simple trend fraction: close vs rolling mid of window
    up_bars = sum(1 for c in candles if c.close > c.open)
    down_bars = sum(1 for c in candles if c.close < c.open)

    # volatility of returns
    vol = 0.0
    if len(rets) > 1:
        m = mean(rets)
        vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

    return {
        "candles": len(candles),
        "start_time": candles[0].open_time.isoformat(),
        "end_time": candles[-1].open_time.isoformat(),
        "start_price": start,
        "end_price": end,
        "net_return_pct": net_return * 100.0,
        "high": max(highs),
        "low": min(lows),
        "range_pct": ((max(highs) - min(lows)) / start * 100.0) if start else 0.0,
        "avg_candle_range": mean(ranges) if ranges else 0.0,
        "median_candle_range": median(ranges) if ranges else 0.0,
        "return_vol": vol,
        "max_drawdown_pct": max_dd * 100.0,
        "up_bar_pct": up_bars / len(candles) * 100.0,
        "down_bar_pct": down_bars / len(candles) * 100.0,
        "direction_bias": "UP"
        if net_return > 0.01
        else ("DOWN" if net_return < -0.01 else "RANGE"),
    }


def run_window(name: str, candles: list[Candle], bt_config: BacktestConfig) -> dict:
    cfg = make_v3_candidate_config()
    # Align with BACKTEST-012 candidate baseline quality gates used in comparison runs.
    cfg.adx_rising = True
    cfg.ema_slope_alignment = True
    cfg.sl_atr_multiplier = 1.5
    cfg.risk_reward_ratio = 2.5

    engine = ConfluenceBacktestEngine(bt_config, candles, ConfluenceStrategy(cfg))
    result = engine.run()
    return {
        "window": name,
        "config": {
            "version": cfg.version,
            "v3_long_bias_penalty": cfg.v3_long_bias_penalty,
            "v3_max_adx": cfg.v3_max_adx,
            "v3_reject_toxic_score": cfg.v3_reject_toxic_score,
            "improved_exit": cfg.improved_exit,
            "adx_rising": cfg.adx_rising,
            "ema_slope_alignment": cfg.ema_slope_alignment,
            "sl_atr_multiplier": cfg.sl_atr_multiplier,
            "risk_reward_ratio": cfg.risk_reward_ratio,
        },
        "market": market_regime(candles),
        "performance": entry_stats(result.trades),
    }


def compare_windows(rows: list[dict]) -> dict:
    by_name = {r["window"]: r for r in rows}
    w1, w2, w3 = by_name["W1"], by_name["W2"], by_name["W3"]

    def delta(a: float, b: float) -> float:
        return a - b

    findings: list[str] = []

    # Market differences
    if w2["market"]["direction_bias"] != w1["market"]["direction_bias"]:
        findings.append(
            f"W2 market direction={w2['market']['direction_bias']} differs from W1={w1['market']['direction_bias']}"
        )
    if w2["market"]["return_vol"] > max(w1["market"]["return_vol"], w3["market"]["return_vol"]):
        findings.append("W2 has the highest return volatility of the three windows")
    if w2["market"]["avg_candle_range"] > max(
        w1["market"]["avg_candle_range"], w3["market"]["avg_candle_range"]
    ):
        findings.append("W2 has the highest average candle range (volatility expansion)")
    if abs(w2["market"]["net_return_pct"]) < min(
        abs(w1["market"]["net_return_pct"]), abs(w3["market"]["net_return_pct"])
    ):
        findings.append("W2 has the smallest net directional move (more range-bound)")

    # Strategy differences
    if w2["performance"]["long_r"] < min(w1["performance"]["long_r"], w3["performance"]["long_r"]):
        findings.append(
            f"W2 LONG expectancy is worst ({w2['performance']['long_r']:.3f}R) vs W1/W3"
        )
    if w2["performance"]["short_r"] < min(
        w1["performance"]["short_r"], w3["performance"]["short_r"]
    ):
        findings.append(
            f"W2 SHORT expectancy is worst ({w2['performance']['short_r']:.3f}R) vs W1/W3"
        )
    if w2["performance"]["conflict_rate"] > max(
        w1["performance"]["conflict_rate"], w3["performance"]["conflict_rate"]
    ):
        findings.append(
            f"W2 has highest EMA/structure conflict rate ({w2['performance']['conflict_rate'] * 100:.1f}%)"
        )
    if w2["performance"]["avg_adx"] < min(
        w1["performance"]["avg_adx"], w3["performance"]["avg_adx"]
    ):
        findings.append(
            f"W2 entries have lowest average ADX ({w2['performance']['avg_adx']:.1f}) — weaker trend environment"
        )
    if w2["performance"]["avg_adx"] > max(
        w1["performance"]["avg_adx"], w3["performance"]["avg_adx"]
    ):
        findings.append(
            f"W2 entries have highest average ADX ({w2['performance']['avg_adx']:.1f}) — late/extended trend risk"
        )

    # Classify primary failure mode
    long_drag = (
        w2["performance"]["long_r"] < 0
        and w2["performance"]["long_r"] < w2["performance"]["short_r"]
    )
    short_drag = (
        w2["performance"]["short_r"] < 0
        and w2["performance"]["short_r"] < w2["performance"]["long_r"]
    )
    both_drag = w2["performance"]["long_r"] < 0 and w2["performance"]["short_r"] < 0

    if both_drag:
        primary = "both_sides_negative"
    elif long_drag:
        primary = "long_side_regime_failure"
    elif short_drag:
        primary = "short_side_regime_failure"
    else:
        primary = "mixed_or_fee_drag"

    # Robustness label for candidate under regime split
    positive = sum(1 for w in (w1, w2, w3) if w["performance"]["expectancy_r"] > 0)
    if positive == 3:
        robustness = "robust"
    elif positive == 2:
        robustness = "mixed_regime_dependent"
    else:
        robustness = "fragile"

    return {
        "primary_failure_mode": primary,
        "robustness": robustness,
        "positive_windows": positive,
        "findings": findings,
        "w2_vs_w1": {
            "expectancy_r_delta": delta(
                w2["performance"]["expectancy_r"], w1["performance"]["expectancy_r"]
            ),
            "long_r_delta": delta(w2["performance"]["long_r"], w1["performance"]["long_r"]),
            "short_r_delta": delta(w2["performance"]["short_r"], w1["performance"]["short_r"]),
            "net_return_pct_delta": delta(
                w2["market"]["net_return_pct"], w1["market"]["net_return_pct"]
            ),
            "vol_delta": delta(w2["market"]["return_vol"], w1["market"]["return_vol"]),
            "avg_adx_delta": delta(w2["performance"]["avg_adx"], w1["performance"]["avg_adx"]),
        },
        "w2_vs_w3": {
            "expectancy_r_delta": delta(
                w2["performance"]["expectancy_r"], w3["performance"]["expectancy_r"]
            ),
            "long_r_delta": delta(w2["performance"]["long_r"], w3["performance"]["long_r"]),
            "short_r_delta": delta(w2["performance"]["short_r"], w3["performance"]["short_r"]),
            "net_return_pct_delta": delta(
                w2["market"]["net_return_pct"], w3["market"]["net_return_pct"]
            ),
            "vol_delta": delta(w2["market"]["return_vol"], w3["market"]["return_vol"]),
            "avg_adx_delta": delta(w2["performance"]["avg_adx"], w3["performance"]["avg_adx"]),
        },
    }


def write_markdown(path: Path, payload: dict) -> None:
    windows = payload["windows"]
    cmp = payload["comparison"]
    lines: list[str] = []
    lines.append("# PROJECT-BACKTEST-013: Market Regime Analysis for V3 Candidate\n")
    lines.append("## Objective\n")
    lines.append(
        "Explain why walk-forward Window 2 is negative for `v3_candidate` without re-optimizing parameters.\n"
    )
    lines.append("## Status\n")
    lines.append("**Diagnostics only. Not production-ready. No parameter promotion.**\n")
    lines.append("## Candidate Config (fixed)\n")
    cfg = windows[0]["config"]
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for k, v in cfg.items():
        lines.append(f"| `{k}` | `{v}` |")
    lines.append("")
    lines.append("## Window Market Regimes\n")
    lines.append(
        "| Window | Direction | Net Return | Range % | Avg Candle Range | Return Vol | Max DD % | Up Bars % |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for w in windows:
        if w["window"] == "FULL":
            continue
        m = w["market"]
        lines.append(
            f"| {w['window']} | {m['direction_bias']} | {m['net_return_pct']:.2f}% | "
            f"{m['range_pct']:.2f}% | {m['avg_candle_range']:.2f} | {m['return_vol']:.6f} | "
            f"{m['max_drawdown_pct']:.2f}% | {m['up_bar_pct']:.1f}% |"
        )
    lines.append("")
    lines.append("## Window Strategy Performance (`v3_candidate`)\n")
    lines.append(
        "| Window | Entries | Exp R | Long R | Short R | WR | Avg ADX | Conflict % | Net PnL |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for w in windows:
        p = w["performance"]
        lines.append(
            f"| {w['window']} | {p['entries']} | {p['expectancy_r']:.3f}R | "
            f"{p['long_r']:.3f}R | {p['short_r']:.3f}R | {p['win_rate'] * 100:.1f}% | "
            f"{p['avg_adx']:.1f} | {p['conflict_rate'] * 100:.1f}% | {p['net_pnl']:.2f} |"
        )
    lines.append("")
    lines.append("## Why W2 Failed\n")
    lines.append(f"**Primary failure mode:** `{cmp['primary_failure_mode']}`\n")
    lines.append(
        f"**Robustness label:** `{cmp['robustness']}` "
        f"({cmp['positive_windows']}/3 windows positive)\n"
    )
    lines.append("### Evidence findings\n")
    if cmp["findings"]:
        for f in cmp["findings"]:
            lines.append(f"- {f}")
    else:
        lines.append("- No dominant single market feature isolated; failure may be multifactorial.")
    lines.append("")
    lines.append("### W2 deltas vs W1 / W3\n")
    lines.append("| Metric | W2-W1 | W2-W3 |")
    lines.append("|---|---:|---:|")
    keys = [
        ("expectancy_r_delta", "Expectancy R"),
        ("long_r_delta", "Long R"),
        ("short_r_delta", "Short R"),
        ("net_return_pct_delta", "Net return %"),
        ("vol_delta", "Return vol"),
        ("avg_adx_delta", "Avg entry ADX"),
    ]
    for key, label in keys:
        lines.append(f"| {label} | {cmp['w2_vs_w1'][key]:.4f} | {cmp['w2_vs_w3'][key]:.4f} |")
    lines.append("")
    lines.append("## Entry Quality Breakdown by Window\n")
    for w in windows:
        if w["window"] == "FULL":
            continue
        p = w["performance"]
        lines.append(f"### {w['window']}\n")
        lines.append(f"- Exit reasons: `{p['exit_reasons']}`")
        lines.append(f"- Score buckets: `{p['score_buckets']}`")
        lines.append(f"- ADX buckets: `{p['adx_buckets']}`")
        lines.append(f"- Structure buckets: `{p['structure_buckets']}`")
        lines.append(f"- EMA trend buckets: `{p['ema_trend_buckets']}`")
        lines.append("")
    lines.append("## Interpretation\n")
    lines.append(
        "This task does **not** re-tune LP / ADX / toxic-zone. It only diagnoses whether "
        "W2 negativity is a market-regime problem rather than a broken candidate config.\n"
    )
    lines.append(
        "If W2 is range-bound / high-chop while W1 and W3 are more directional, the candidate "
        "remains **regime-dependent**. That matches BACKTEST-012's mixed/promising label.\n"
    )
    lines.append("## Known Limitations\n")
    lines.append("- 90 days only; three 30-day windows are coarse regime labels")
    lines.append("- No external macro labels (FOMC, DXY shock, etc.)")
    lines.append("- ATR/ADX are strategy-internal; market regime here uses price path proxies")
    lines.append("- Single symbol XAU-USDT-SWAP")
    lines.append("")
    lines.append("## Recommended Next Steps\n")
    if cmp["robustness"] == "mixed_regime_dependent":
        lines.append(
            "1. Build explicit regime classifier (trend vs range vs high-vol chop) before any new filter tuning."
        )
        lines.append(
            "2. Gate `v3_candidate` entries by regime, or reduce size/disable in range-chop windows."
        )
        lines.append(
            "3. Do **not** promote production defaults until regime filter is validated out-of-sample."
        )
    elif cmp["robustness"] == "fragile":
        lines.append("1. Revisit core entry assumptions; candidate edge is not regime-stable.")
        lines.append("2. Prefer deeper diagnostics over more parameter grids.")
    else:
        lines.append(
            "1. Candidate appears robust across windows; proceed to longer OOS validation."
        )
    lines.append("")
    lines.append("## Reproducibility\n")
    lines.append("```bash")
    lines.append("uv run python tools/run_backtest_013_regime_analysis.py")
    lines.append("```\n")
    path.write_text("\n".join(lines))


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
    if not candles:
        print("No candles loaded.")
        return

    bt_config = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )

    n = len(candles)
    c_per = n // 3
    windows = [
        ("W1", candles[0:c_per]),
        ("W2", candles[c_per : 2 * c_per]),
        ("W3", candles[2 * c_per :]),
        ("FULL", candles),
    ]

    print(f"Running regime analysis on {n} candles across 3 windows + FULL...")
    rows = [run_window(name, w_candles, bt_config) for name, w_candles in windows]
    comparison = compare_windows(rows)

    payload = {
        "task_id": "PROJECT-BACKTEST-013",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": "XAU-USDT-SWAP",
        "granularity": "15m",
        "candles": n,
        "windows": rows,
        "comparison": comparison,
        "status": "diagnostics_only",
        "production_ready": False,
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"regime_BACKTEST-013_{ts}.json"
    md_path = report_dir / "regime_BACKTEST-013.md"
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)
    write_markdown(md_path, payload)
    print(f"Done. Wrote {json_path} and {md_path}")
    print(f"Primary failure mode: {comparison['primary_failure_mode']}")
    print(f"Robustness: {comparison['robustness']}")
    for finding in comparison["findings"]:
        print(f"- {finding}")


if __name__ == "__main__":
    asyncio.run(main())
