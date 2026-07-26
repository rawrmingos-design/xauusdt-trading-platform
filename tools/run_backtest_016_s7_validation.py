#!/usr/bin/env python3
"""PROJECT-BACKTEST-016: Validate STRATEGY-007 profiles A/B/C vs v3_candidate.

Protocol:
  1. Fixed configs only — no threshold re-fit.
  2. Walk-forward W1/W2/W3/FULL side-by-side.
  3. Chronological pseudo hold-out = W3 (last 1/3), with contamination warning:
     BACKTEST-015 discovery used the full 90-day path, so W3 is not pure OOS.
  4. True external OOS requires extended history (reported if unavailable).

Metric: entry-grouped realized R-multiples (not dollar PnL sums).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from statistics import mean
from typing import Any

from xauusdt.backtest.confluence_engine import ConfluenceBacktestEngine
from xauusdt.backtest.models import BacktestConfig, BacktestTrade
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository
from xauusdt.storage.database import get_session, init_db
from xauusdt.strategy.confluence import (
    ConfluenceConfig,
    ConfluenceStrategy,
    make_v3_candidate_config,
    make_v3_candidate_s7a_config,
    make_v3_candidate_s7b_config,
    make_v3_candidate_s7c_config,
)


def calc_r(trade: BacktestTrade) -> float:
    if trade.sl_distance <= 0 or trade.quantity <= 0:
        return 0.0
    return trade.gross_pnl / (trade.quantity * trade.sl_distance)


def analyze(trades: list[BacktestTrade]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: t.entry_candle_time)
    groups = [list(g) for _, g in groupby(ordered, key=lambda t: t.entry_candle_time)]
    if not groups:
        return {
            "entries": 0,
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "long_r": 0.0,
            "short_r": 0.0,
            "long_n": 0,
            "short_n": 0,
            "net_pnl": 0.0,
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
    return {
        "entries": len(groups),
        "win_rate": sum(1 for r in entry_rs if r > 0) / len(entry_rs),
        "expectancy_r": mean(entry_rs),
        "long_r": mean(long_rs) if long_rs else 0.0,
        "short_r": mean(short_rs) if short_rs else 0.0,
        "long_n": len(long_rs),
        "short_n": len(short_rs),
        "net_pnl": sum(t.pnl for t in trades),
    }


def build_cfg(factory: Callable[..., ConfluenceConfig]) -> ConfluenceConfig:
    cfg = factory()
    # Same comparison baseline as BACKTEST-012/013/014/015
    cfg.adx_rising = True
    cfg.ema_slope_alignment = True
    cfg.sl_atr_multiplier = 1.5
    cfg.risk_reward_ratio = 2.5
    return cfg


def run_one(cfg: ConfluenceConfig, candles: list[Candle]) -> dict[str, Any]:
    bt = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    engine = ConfluenceBacktestEngine(bt, candles, ConfluenceStrategy(cfg))
    perf = analyze(engine.run().trades)
    return {
        "version": cfg.version,
        "v3_reject_toxic_score": cfg.v3_reject_toxic_score,
        "v3_block_long_ema_up": cfg.v3_block_long_ema_up,
        "performance": perf,
    }


def verdict_for(comparison: list[dict[str, Any]]) -> str:
    """Promotion rules (research only; same-sample optimistic)."""
    by_w = {c["window"]: c for c in comparison}
    # Prefer s7a if it improves W2 and FULL without wrecking W3
    best = "baseline"
    best_score = -1e9
    for name in ("s7a", "s7b", "s7c"):
        score = 0.0
        for w, weight in (("W2", 2.0), ("FULL", 1.5), ("W3", 1.5), ("W1", 1.0)):
            row = by_w[w]
            score += weight * row[f"{name}_delta_r"]
        # Penalize huge trade removal on FULL (>40%)
        base_n = by_w["FULL"]["baseline_n"]
        n = by_w["FULL"][f"{name}_n"]
        if base_n > 0 and (base_n - n) / base_n > 0.40:
            score -= 0.05
        if score > best_score:
            best_score = score
            best = name

    w3 = by_w["W3"]
    full = by_w["FULL"]
    w2 = by_w["W2"]
    if best == "baseline":
        return "no_profile_beats_baseline"
    # Require W2 improvement and non-negative FULL delta and W3 not destroyed
    if (
        w2[f"{best}_delta_r"] > 0.02
        and full[f"{best}_delta_r"] >= 0.0
        and w3[f"{best}_delta_r"] >= -0.02
    ):
        return f"promote_research_{best}_same_sample_only"
    if w2[f"{best}_delta_r"] > 0 and full[f"{best}_delta_r"] < 0:
        return f"{best}_helps_w2_hurts_full"
    return f"{best}_inconclusive_needs_true_oos"


async def main() -> None:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)
    print("Loading candles...")
    orms = None
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range("XAU-USDT-SWAP", "15m", limit=100000)
        await session.close()
        break

    if not orms:
        print("No candles")
        return

    candles = [
        Candle(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            open_time=r.open_time,  # type: ignore[arg-type]
            open=float(r.open_price),  # type: ignore[arg-type]
            high=float(r.high),  # type: ignore[arg-type]
            low=float(r.low),  # type: ignore[arg-type]
            close=float(r.close),  # type: ignore[arg-type]
            volume=float(r.volume or 0),  # type: ignore[arg-type]
        )
        for r in orms
    ]

    n = len(candles)
    c_per = n // 3
    windows = [
        ("W1", candles[0:c_per]),
        ("W2", candles[c_per : 2 * c_per]),
        ("W3", candles[2 * c_per :]),  # pseudo hold-out slice
        ("FULL", candles),
    ]

    variants: list[tuple[str, Callable[..., ConfluenceConfig]]] = [
        ("baseline", make_v3_candidate_config),
        ("s7a", make_v3_candidate_s7a_config),
        ("s7b", make_v3_candidate_s7b_config),
        ("s7c", make_v3_candidate_s7c_config),
    ]

    rows: list[dict[str, Any]] = []
    print(f"Running {len(variants)} variants × {len(windows)} windows on {n} candles...")
    for w_name, w_candles in windows:
        for v_name, factory in variants:
            cfg = build_cfg(factory)
            print(f"  {w_name} / {v_name} ({cfg.version})...")
            row = run_one(cfg, w_candles)
            row["window"] = w_name
            row["variant"] = v_name
            rows.append(row)

    by_key = {(r["window"], r["variant"]): r for r in rows}
    comparison: list[dict[str, Any]] = []
    for w_name, _ in windows:
        base = by_key[(w_name, "baseline")]["performance"]
        rec: dict[str, Any] = {
            "window": w_name,
            "baseline_n": base["entries"],
            "baseline_r": base["expectancy_r"],
            "baseline_long_r": base["long_r"],
            "baseline_short_r": base["short_r"],
        }
        for v_name in ("s7a", "s7b", "s7c"):
            p = by_key[(w_name, v_name)]["performance"]
            rec[f"{v_name}_n"] = p["entries"]
            rec[f"{v_name}_r"] = p["expectancy_r"]
            rec[f"{v_name}_delta_r"] = p["expectancy_r"] - base["expectancy_r"]
            rec[f"{v_name}_removed"] = base["entries"] - p["entries"]
            rec[f"{v_name}_long_r"] = p["long_r"]
            rec[f"{v_name}_short_r"] = p["short_r"]
        comparison.append(rec)

    verdict = verdict_for(comparison)
    w3 = next(c for c in comparison if c["window"] == "W3")
    full = next(c for c in comparison if c["window"] == "FULL")
    w2 = next(c for c in comparison if c["window"] == "W2")

    # Rank profiles by W3 (pseudo-holdout) then FULL
    rank = sorted(
        ("s7a", "s7b", "s7c"),
        key=lambda name: (w3[f"{name}_delta_r"], full[f"{name}_delta_r"], w2[f"{name}_delta_r"]),
        reverse=True,
    )

    payload = {
        "task_id": "PROJECT-BACKTEST-016",
        "strategy_task": "PROJECT-STRATEGY-007",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": "XAU-USDT-SWAP",
        "granularity": "15m",
        "candles": n,
        "status": "research_validation",
        "production_ready": False,
        "holdout_note": (
            "W3 is a chronological pseudo hold-out only. BACKTEST-015 discovery "
            "used the full 90-day sample, so W3 is contaminated. Not true external OOS."
        ),
        "threshold_refit": False,
        "rows": rows,
        "comparison": comparison,
        "verdict": verdict,
        "rank_by_w3_then_full": rank,
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"validation_BACKTEST-016_{ts}.json"
    md_path = report_dir / "validation_BACKTEST-016.md"

    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    lines = [
        "# PROJECT-BACKTEST-016: STRATEGY-007 A/B/C Validation",
        "",
        "## Objective",
        "Compare fixed experimental profiles **s7a / s7b / s7c** against baseline "
        "`v3_candidate` without re-fitting thresholds.",
        "",
        "## Status",
        "**Research validation only. Not production-ready.**",
        "",
        "## Hold-out honesty",
        "> W3 (last 1/3) is a **chronological pseudo hold-out**. Discovery in "
        "BACKTEST-015 used the **full** 90-day path, so W3 is **contaminated**. "
        "This is **not** true external OOS. Extended history required for real OOS.",
        "",
        "- Threshold re-fit: **No** (fixed [75,84] and LONG|EMA-UP from BT-015)",
        f"- Candles: {n} × 15m",
        "",
        "## Profiles",
        "| Variant | Factory | Filters |",
        "|---|---|---|",
        "| baseline | `make_v3_candidate_config()` | — |",
        "| s7a | `make_v3_candidate_s7a_config()` | toxic [75,84] |",
        "| s7b | `make_v3_candidate_s7b_config()` | block LONG when EMA-UP |",
        "| s7c | `make_v3_candidate_s7c_config()` | A + B |",
        "",
        "## Walk-Forward Expectancy",
        "",
        "| Window | Baseline R (N) | s7a R (Δ, N) | s7b R (Δ, N) | s7c R (Δ, N) |",
        "|---|---:|---:|---:|---:|",
    ]
    for c in comparison:
        lines.append(
            f"| {c['window']} | {c['baseline_r']:.3f}R ({c['baseline_n']}) | "
            f"{c['s7a_r']:.3f}R ({c['s7a_delta_r']:+.3f}, {c['s7a_n']}) | "
            f"{c['s7b_r']:.3f}R ({c['s7b_delta_r']:+.3f}, {c['s7b_n']}) | "
            f"{c['s7c_r']:.3f}R ({c['s7c_delta_r']:+.3f}, {c['s7c_n']}) |"
        )

    lines.extend(
        [
            "",
            "## Long / Short (expectancy R)",
            "",
            "| Window | Baseline L/S | s7a L/S | s7b L/S | s7c L/S |",
            "|---|---|---|---|---|",
        ]
    )
    for c in comparison:
        lines.append(
            f"| {c['window']} | {c['baseline_long_r']:.3f}/{c['baseline_short_r']:.3f} | "
            f"{c['s7a_long_r']:.3f}/{c['s7a_short_r']:.3f} | "
            f"{c['s7b_long_r']:.3f}/{c['s7b_short_r']:.3f} | "
            f"{c['s7c_long_r']:.3f}/{c['s7c_short_r']:.3f} |"
        )

    lines.extend(
        [
            "",
            f"## Verdict: `{verdict}`",
            "",
            f"- Rank by W3 then FULL: **{' > '.join(rank)}**",
            f"- W2 deltas: s7a {w2['s7a_delta_r']:+.3f}R, s7b {w2['s7b_delta_r']:+.3f}R, s7c {w2['s7c_delta_r']:+.3f}R",
            f"- W3 (pseudo-holdout) deltas: s7a {w3['s7a_delta_r']:+.3f}R, s7b {w3['s7b_delta_r']:+.3f}R, s7c {w3['s7c_delta_r']:+.3f}R",
            f"- FULL deltas: s7a {full['s7a_delta_r']:+.3f}R, s7b {full['s7b_delta_r']:+.3f}R, s7c {full['s7c_delta_r']:+.3f}R",
            "",
            "### Decision guide",
            "| Outcome | Action |",
            "|---|---|",
            "| Helps W2 + FULL + W3 | Keep as research profile; still need true OOS |",
            "| Helps W2 only | Reject as global default |",
            "| High removal, similar R | Prefer narrower profile (A or B over C) |",
            "| True OOS unavailable | **Do not promote to production** |",
            "",
            "## Explicit non-claims",
            "- Not production-ready",
            "- Same-sample re-measurement is optimistic",
            "- No live trading / no production default change",
            "",
            "## Reproducibility",
            "```bash",
            "uv run python tools/run_backtest_016_s7_validation.py",
            "```",
            "",
            f"JSON: `{json_path}`",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    print(f"Done. Wrote {json_path} and {md_path}")
    print(f"Verdict: {verdict}")
    print(f"Rank: {rank}")
    for c in comparison:
        print(
            f"  {c['window']}: base={c['baseline_r']:.3f}R "
            f"a={c['s7a_delta_r']:+.3f} b={c['s7b_delta_r']:+.3f} c={c['s7c_delta_r']:+.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
