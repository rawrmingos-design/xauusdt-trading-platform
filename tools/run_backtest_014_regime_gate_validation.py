#!/usr/bin/env python3
"""PROJECT-BACKTEST-014: OOS walk-forward validation of regime-gated v3_candidate.

Compares:
  - v3_candidate (ungated baseline from STRATEGY-005 / BACKTEST-013)
  - v3_candidate_regime_gated (STRATEGY-006)

Fixed config. No parameter search. Stored candles only.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from statistics import mean

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import (
    ConfluenceConfig,
    ConfluenceStrategy,
    make_v3_candidate_config,
    make_v3_candidate_regime_gated_config,
)
from xauusdt.strategy.regime import RegimeConfig, classify_regime


def calc_r(trade: BacktestTrade) -> float:
    if trade.sl_distance <= 0 or trade.quantity <= 0:
        return 0.0
    return trade.gross_pnl / (trade.quantity * trade.sl_distance)


def group_by_entry(trades: list[BacktestTrade]) -> list[list[BacktestTrade]]:
    ordered = sorted(trades, key=lambda t: t.entry_candle_time)
    return [list(g) for _, g in groupby(ordered, key=lambda t: t.entry_candle_time)]


def analyze(trades: list[BacktestTrade]) -> dict:
    groups = group_by_entry(trades)
    if not groups:
        return {
            "entries": 0,
            "legs": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "expectancy_r": 0.0,
            "long_r": 0.0,
            "short_r": 0.0,
            "long_entries": 0,
            "short_entries": 0,
        }
    entry_rs: list[float] = []
    long_rs: list[float] = []
    short_rs: list[float] = []
    for legs in groups:
        total_r = sum(calc_r(t) for t in legs)
        entry_rs.append(total_r)
        if legs[0].side == "LONG":
            long_rs.append(total_r)
        else:
            short_rs.append(total_r)
    wins = sum(1 for r in entry_rs if r > 0)
    return {
        "entries": len(groups),
        "legs": len(trades),
        "win_rate": wins / len(groups),
        "net_pnl": sum(t.pnl for t in trades),
        "expectancy_r": mean(entry_rs),
        "long_r": mean(long_rs) if long_rs else 0.0,
        "short_r": mean(short_rs) if short_rs else 0.0,
        "long_entries": len(long_rs),
        "short_entries": len(short_rs),
    }


def regime_share(candles: list[Candle], lookback: int = 96) -> dict:
    """Fraction of bars labeled RANGE_CHOP using rolling classify (no lookahead)."""
    if len(candles) < lookback:
        return {"bars": len(candles), "range_chop_pct": 0.0, "trend_pct": 0.0}
    cfg = RegimeConfig(lookback=lookback)
    chop = 0
    trend = 0
    unknown = 0
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    for i in range(lookback, len(candles) + 1):
        snap = classify_regime(closes[:i], highs[:i], lows[:i], cfg)
        if snap.label.value == "RANGE_CHOP":
            chop += 1
        elif snap.label.value == "TREND":
            trend += 1
        else:
            unknown += 1
    total = chop + trend + unknown
    return {
        "bars_classified": total,
        "range_chop_pct": chop / total * 100.0 if total else 0.0,
        "trend_pct": trend / total * 100.0 if total else 0.0,
        "unknown_pct": unknown / total * 100.0 if total else 0.0,
    }


def build_cfg(factory) -> ConfluenceConfig:
    cfg = factory()
    # Align with BACKTEST-012/013 candidate comparison baseline
    cfg.adx_rising = True
    cfg.ema_slope_alignment = True
    cfg.sl_atr_multiplier = 1.5
    cfg.risk_reward_ratio = 2.5
    return cfg


def run_one(name: str, cfg: ConfluenceConfig, candles: list[Candle], bt: BacktestConfig) -> dict:
    engine = ConfluenceBacktestEngine(bt, candles, ConfluenceStrategy(cfg))
    result = engine.run()
    perf = analyze(result.trades)
    return {
        "variant": name,
        "version": cfg.version,
        "v3_regime_gate": cfg.v3_regime_gate,
        "performance": perf,
        "regime_share": regime_share(candles),
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
    if not candles:
        print("No candles")
        return

    bt = BacktestConfig(
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

    variants = [
        ("ungated", make_v3_candidate_config),
        ("regime_gated", make_v3_candidate_regime_gated_config),
    ]

    rows: list[dict] = []
    print(f"Running {len(variants)} variants x {len(windows)} windows on {n} candles...")
    for w_name, w_candles in windows:
        for v_name, factory in variants:
            cfg = build_cfg(factory)
            print(f"  {w_name} / {v_name}...")
            row = run_one(v_name, cfg, w_candles, bt)
            row["window"] = w_name
            rows.append(row)

    # Comparison summary
    by_key = {(r["window"], r["variant"]): r for r in rows}
    comparison = []
    for w_name, _ in windows:
        u = by_key[(w_name, "ungated")]["performance"]
        g = by_key[(w_name, "regime_gated")]["performance"]
        comparison.append(
            {
                "window": w_name,
                "ungated_exp_r": u["expectancy_r"],
                "gated_exp_r": g["expectancy_r"],
                "delta_exp_r": g["expectancy_r"] - u["expectancy_r"],
                "ungated_entries": u["entries"],
                "gated_entries": g["entries"],
                "entries_removed": u["entries"] - g["entries"],
                "ungated_short_r": u["short_r"],
                "gated_short_r": g["short_r"],
                "ungated_long_r": u["long_r"],
                "gated_long_r": g["long_r"],
                "regime_chop_pct": by_key[(w_name, "ungated")]["regime_share"]["range_chop_pct"],
            }
        )

    positive_ungated = sum(
        1 for c in comparison if c["window"] != "FULL" and c["ungated_exp_r"] > 0
    )
    positive_gated = sum(1 for c in comparison if c["window"] != "FULL" and c["gated_exp_r"] > 0)
    w2 = next(c for c in comparison if c["window"] == "W2")
    full = next(c for c in comparison if c["window"] == "FULL")

    if positive_gated == 3 and full["gated_exp_r"] > full["ungated_exp_r"]:
        verdict = "improved_and_more_stable"
    elif (
        w2["gated_exp_r"] > w2["ungated_exp_r"]
        and full["gated_exp_r"] >= full["ungated_exp_r"] - 0.01
    ):
        verdict = "w2_helped_without_full_damage"
    elif full["gated_exp_r"] < full["ungated_exp_r"] - 0.02:
        verdict = "gate_hurts_overall"
    else:
        verdict = "inconclusive"

    payload = {
        "task_id": "PROJECT-BACKTEST-014",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": "XAU-USDT-SWAP",
        "granularity": "15m",
        "candles": n,
        "status": "research_validation",
        "production_ready": False,
        "rows": rows,
        "comparison": comparison,
        "verdict": verdict,
        "positive_windows_ungated": positive_ungated,
        "positive_windows_gated": positive_gated,
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"validation_BACKTEST-014_{ts}.json"
    md_path = report_dir / "validation_BACKTEST-014.md"

    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    lines = [
        "# PROJECT-BACKTEST-014: Regime-Gated V3 Candidate Walk-Forward Validation",
        "",
        "## Objective",
        "Validate whether blocking entries in RANGE_CHOP improves W2 without destroying W1/W3 edge.",
        "",
        "## Status",
        "**Research validation only. Not production-ready.**",
        "",
        "## Variants",
        "| Variant | Factory | Gate |",
        "|---|---|---|",
        "| ungated | `make_v3_candidate_config()` | OFF |",
        "| regime_gated | `make_v3_candidate_regime_gated_config()` | block RANGE_CHOP |",
        "",
        "## Walk-Forward Comparison",
        "",
        "| Window | Ungated Exp R | Gated Exp R | Δ Exp R | Ungated N | Gated N | Removed | Chop % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in comparison:
        lines.append(
            f"| {c['window']} | {c['ungated_exp_r']:.3f}R | {c['gated_exp_r']:.3f}R | "
            f"{c['delta_exp_r']:+.3f}R | {c['ungated_entries']} | {c['gated_entries']} | "
            f"{c['entries_removed']} | {c['regime_chop_pct']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Long / Short Breakdown",
            "",
            "| Window | Ungated L/S | Gated L/S |",
            "|---|---|---|",
        ]
    )
    for c in comparison:
        lines.append(
            f"| {c['window']} | {c['ungated_long_r']:.3f}R / {c['ungated_short_r']:.3f}R | "
            f"{c['gated_long_r']:.3f}R / {c['gated_short_r']:.3f}R |"
        )
    lines.extend(
        [
            "",
            f"## Verdict: `{verdict}`",
            "",
            f"- Positive windows ungated: **{positive_ungated}/3**",
            f"- Positive windows gated: **{positive_gated}/3**",
            f"- W2 delta: **{w2['delta_exp_r']:+.3f}R**",
            f"- FULL delta: **{full['delta_exp_r']:+.3f}R**",
            "",
            "## Interpretation Guardrails",
            "- If gate only helps W2 by killing almost all trades → not useful.",
            "- If gate helps W2 and preserves W1/W3 → promote as research gated candidate.",
            "- If gate hurts FULL expectancy materially → reject or retune classifier thresholds later.",
            "",
            "## Known Limitations",
            "- Classifier thresholds are research defaults from BACKTEST-013, not optimized.",
            "- 90-day sample only; three 30-day windows.",
            "- No external macro regime labels.",
            "",
            "## Reproducibility",
            "```bash",
            "uv run python tools/run_backtest_014_regime_gate_validation.py",
            "```",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    print(f"Done. Wrote {json_path} and {md_path}")
    print(f"Verdict: {verdict}")
    for c in comparison:
        print(
            f"  {c['window']}: ungated={c['ungated_exp_r']:.3f}R gated={c['gated_exp_r']:.3f}R "
            f"delta={c['delta_exp_r']:+.3f}R removed={c['entries_removed']} chop={c['regime_chop_pct']:.1f}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
