#!/usr/bin/env python3
"""PROJECT-BACKTEST-017: True external OOS for v3_candidate vs s7b.

Discovery sample (contaminated by BT-011..016 design):
  2026-04-16 → 2026-07-15  (~90d)

True pre-discovery OOS (never used for filter design):
  2026-01-16 → 2026-04-16  (~90d, from 180d backfill)

Fixed configs only. No threshold re-fit.
Metric: entry-grouped realized R (not dollar PnL sums).
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

# Chronological split aligned with stored history after 180d backfill.
DISCOVERY_START = datetime(2026, 4, 16, tzinfo=UTC)
DISCOVERY_END = datetime(2026, 7, 15, tzinfo=UTC)  # exclusive upper bound for slicing
OOS_START = datetime(2026, 1, 16, tzinfo=UTC)
OOS_END = DISCOVERY_START  # exclusive — true pre-discovery


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
    # Same comparison baseline as BT-012..016
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
    return {
        "version": cfg.version,
        "v3_reject_toxic_score": cfg.v3_reject_toxic_score,
        "v3_block_long_ema_up": cfg.v3_block_long_ema_up,
        "performance": analyze(engine.run().trades),
        "candles": len(candles),
        "start": candles[0].open_time.isoformat() if candles else None,
        "end": candles[-1].open_time.isoformat() if candles else None,
    }


def slice_candles(candles: list[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


def thirds(candles: list[Candle]) -> list[tuple[str, list[Candle]]]:
    n = len(candles)
    if n < 3:
        return [("ALL", candles)]
    c = n // 3
    return [
        ("T1", candles[0:c]),
        ("T2", candles[c : 2 * c]),
        ("T3", candles[2 * c :]),
    ]


def verdict(
    discovery_delta: float,
    oos_delta: float,
    oos_base_r: float,
    oos_s7b_r: float,
    oos_n_base: int,
    oos_n_s7b: int,
) -> str:
    """Research-only promotion rules for true OOS."""
    if oos_n_base < 30 or oos_n_s7b < 20:
        return "insufficient_oos_trades"
    # s7b must not destroy OOS vs baseline
    if oos_delta >= 0.02 and oos_s7b_r > 0 and discovery_delta > 0:
        return "s7b_holds_on_true_oos"
    if oos_delta >= 0.0 and oos_s7b_r > oos_base_r and discovery_delta > 0:
        return "s7b_non_negative_oos_keep_research"
    if oos_delta < -0.02:
        return "s7b_fails_true_oos_revert_baseline"
    if oos_s7b_r < 0 and oos_base_r >= 0:
        return "s7b_turns_oos_negative"
    return "oos_inconclusive"


async def main() -> None:
    db_url = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"
    await init_db(db_url)
    print("Loading candles...")
    orms = None
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range("XAU-USDT-SWAP", "15m", limit=200000)
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
    candles.sort(key=lambda c: c.open_time)

    oos = slice_candles(candles, OOS_START, OOS_END)
    discovery = slice_candles(candles, DISCOVERY_START, DISCOVERY_END)

    print(
        f"Total={len(candles)} OOS={len(oos)} discovery={len(discovery)} "
        f"range={candles[0].open_time.date()}→{candles[-1].open_time.date()}",
        flush=True,
    )
    if len(oos) < 1000 or len(discovery) < 1000:
        print("ERROR: insufficient candles for true OOS split")
        return

    # Primary matrix: all 4 variants on OOS + discovery only (8 × ~90d).
    # Stability: baseline + s7b on OOS thirds (6 × ~30d).
    # Skip ALL_180D and discovery thirds — expensive and not needed for OOS gate.
    primary_variants: list[tuple[str, Callable[..., ConfluenceConfig]]] = [
        ("baseline", make_v3_candidate_config),
        ("s7a", make_v3_candidate_s7a_config),
        ("s7b", make_v3_candidate_s7b_config),
        ("s7c", make_v3_candidate_s7c_config),
    ]
    stability_variants: list[tuple[str, Callable[..., ConfluenceConfig]]] = [
        ("baseline", make_v3_candidate_config),
        ("s7b", make_v3_candidate_s7b_config),
    ]

    jobs: list[tuple[str, list[Candle], list[tuple[str, Callable[..., ConfluenceConfig]]]]] = [
        ("OOS_FULL", oos, primary_variants),
        ("DISCOVERY_FULL", discovery, primary_variants),
    ]
    for label, chunk in thirds(oos):
        jobs.append((f"OOS_{label}", chunk, stability_variants))

    rows: list[dict[str, Any]] = []
    total_jobs = sum(len(vs) for _, _, vs in jobs)
    print(f"Running {total_jobs} backtests (slim matrix)...", flush=True)
    done = 0
    for seg_name, seg_candles, variants in jobs:
        for v_name, factory in variants:
            cfg = build_cfg(factory)
            done += 1
            print(
                f"  [{done}/{total_jobs}] {seg_name} / {v_name} "
                f"({cfg.version}, n={len(seg_candles)})...",
                flush=True,
            )
            row = run_one(cfg, seg_candles)
            row["segment"] = seg_name
            row["variant"] = v_name
            rows.append(row)

    by_key = {(r["segment"], r["variant"]): r for r in rows}

    def pack(segment: str, variants: tuple[str, ...] = ("s7a", "s7b", "s7c")) -> dict[str, Any]:
        base = by_key[(segment, "baseline")]["performance"]
        out: dict[str, Any] = {
            "segment": segment,
            "candles": by_key[(segment, "baseline")]["candles"],
            "start": by_key[(segment, "baseline")]["start"],
            "end": by_key[(segment, "baseline")]["end"],
            "baseline_n": base["entries"],
            "baseline_r": base["expectancy_r"],
            "baseline_long_r": base["long_r"],
            "baseline_short_r": base["short_r"],
            "baseline_wr": base["win_rate"],
        }
        for v in variants:
            key = (segment, v)
            if key not in by_key:
                continue
            p = by_key[key]["performance"]
            out[f"{v}_n"] = p["entries"]
            out[f"{v}_r"] = p["expectancy_r"]
            out[f"{v}_delta_r"] = p["expectancy_r"] - base["expectancy_r"]
            out[f"{v}_removed"] = base["entries"] - p["entries"]
            out[f"{v}_long_r"] = p["long_r"]
            out[f"{v}_short_r"] = p["short_r"]
            out[f"{v}_wr"] = p["win_rate"]
        return out

    comparison = [
        pack("OOS_FULL"),
        pack("DISCOVERY_FULL"),
        pack("OOS_T1", ("s7b",)),
        pack("OOS_T2", ("s7b",)),
        pack("OOS_T3", ("s7b",)),
    ]

    oos_full = next(c for c in comparison if c["segment"] == "OOS_FULL")
    disc_full = next(c for c in comparison if c["segment"] == "DISCOVERY_FULL")
    v = verdict(
        discovery_delta=disc_full["s7b_delta_r"],
        oos_delta=oos_full["s7b_delta_r"],
        oos_base_r=oos_full["baseline_r"],
        oos_s7b_r=oos_full["s7b_r"],
        oos_n_base=oos_full["baseline_n"],
        oos_n_s7b=oos_full["s7b_n"],
    )

    # OOS third stability for s7b
    oos_thirds = [c for c in comparison if c["segment"].startswith("OOS_T")]
    s7b_positive_thirds = sum(1 for c in oos_thirds if c["s7b_r"] > 0)
    s7b_beats_base_thirds = sum(1 for c in oos_thirds if c["s7b_delta_r"] > 0)

    payload = {
        "task_id": "PROJECT-BACKTEST-017",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": "XAU-USDT-SWAP",
        "granularity": "15m",
        "total_candles": len(candles),
        "oos_range": [OOS_START.isoformat(), OOS_END.isoformat()],
        "discovery_range": [DISCOVERY_START.isoformat(), DISCOVERY_END.isoformat()],
        "status": "true_external_oos",
        "production_ready": False,
        "threshold_refit": False,
        "contamination_note": (
            "OOS_FULL is strictly before discovery start and was not used in "
            "BT-011..016 design. Discovery remains contaminated."
        ),
        "rows": rows,
        "comparison": comparison,
        "verdict": v,
        "s7b_oos_positive_thirds": s7b_positive_thirds,
        "s7b_oos_beats_baseline_thirds": s7b_beats_base_thirds,
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"validation_BACKTEST-017_{ts}.json"
    md_path = report_dir / "validation_BACKTEST-017.md"

    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    def row_md_primary(c: dict[str, Any]) -> str:
        return (
            f"| {c['segment']} | {c['baseline_r']:.3f}R ({c['baseline_n']}) | "
            f"{c['s7a_r']:.3f}R ({c['s7a_delta_r']:+.3f}, {c['s7a_n']}) | "
            f"{c['s7b_r']:.3f}R ({c['s7b_delta_r']:+.3f}, {c['s7b_n']}) | "
            f"{c['s7c_r']:.3f}R ({c['s7c_delta_r']:+.3f}, {c['s7c_n']}) |"
        )

    def row_md_s7b(c: dict[str, Any]) -> str:
        return (
            f"| {c['segment']} | {c['baseline_r']:.3f}R ({c['baseline_n']}) | "
            f"{c['s7b_r']:.3f}R ({c['s7b_delta_r']:+.3f}, {c['s7b_n']}) |"
        )

    lines = [
        "# PROJECT-BACKTEST-017: True External OOS (v3_candidate vs s7a/b/c)",
        "",
        "## Objective",
        "Test whether STRATEGY-007 profiles (especially **s7b**) survive on data",
        "**never used** in BT-011..016 filter design.",
        "",
        "## Status",
        "**Research validation. Not production-ready.**",
        "",
        "## Data split",
        "",
        "| Segment | Range | Role |",
        "|---|---|---|",
        f"| **OOS_FULL** | {OOS_START.date()} → {OOS_END.date()} | **True pre-discovery OOS** |",
        f"| DISCOVERY_FULL | {DISCOVERY_START.date()} → {DISCOVERY_END.date()} | Contaminated (design sample) |",
        "",
        f"- Backfill: 180d 15m, gaps=0, total candles loaded: **{len(candles)}**",
        f"- OOS candles: **{len(oos)}** | Discovery candles: **{len(discovery)}**",
        "- Threshold re-fit: **No**",
        "- Matrix: 4 variants × (OOS + discovery) + baseline/s7b on OOS thirds",
        "",
        "## Primary comparison",
        "",
        "| Segment | Baseline R (N) | s7a R (Δ, N) | s7b R (Δ, N) | s7c R (Δ, N) |",
        "|---|---:|---:|---:|---:|",
    ]
    for seg in ("OOS_FULL", "DISCOVERY_FULL"):
        lines.append(row_md_primary(next(c for c in comparison if c["segment"] == seg)))

    lines.extend(
        [
            "",
            "## OOS thirds (stability — baseline vs s7b only)",
            "",
            "| Segment | Baseline R (N) | s7b R (Δ, N) |",
            "|---|---:|---:|",
        ]
    )
    for seg in ("OOS_T1", "OOS_T2", "OOS_T3"):
        lines.append(row_md_s7b(next(c for c in comparison if c["segment"] == seg)))

    lines.extend(
        [
            "",
            "## Long / Short on OOS_FULL",
            "",
            "| Variant | Long R | Short R |",
            "|---|---:|---:|",
            f"| baseline | {oos_full['baseline_long_r']:.3f}R | {oos_full['baseline_short_r']:.3f}R |",
            f"| s7a | {oos_full['s7a_long_r']:.3f}R | {oos_full['s7a_short_r']:.3f}R |",
            f"| s7b | {oos_full['s7b_long_r']:.3f}R | {oos_full['s7b_short_r']:.3f}R |",
            f"| s7c | {oos_full['s7c_long_r']:.3f}R | {oos_full['s7c_short_r']:.3f}R |",
            "",
            f"## Verdict: `{v}`",
            "",
            f"- Discovery s7b Δ: **{disc_full['s7b_delta_r']:+.3f}R** (contaminated, expect positive)",
            f"- **True OOS s7b Δ: {oos_full['s7b_delta_r']:+.3f}R** "
            f"(base {oos_full['baseline_r']:.3f}R → s7b {oos_full['s7b_r']:.3f}R)",
            f"- OOS thirds s7b > 0: **{s7b_positive_thirds}/3**",
            f"- OOS thirds s7b beats baseline: **{s7b_beats_base_thirds}/3**",
            f"- Entries removed on OOS (s7b): **{oos_full['s7b_removed']}** "
            f"({oos_full['baseline_n']} → {oos_full['s7b_n']})",
            "",
            "### Decision guide",
            "| Verdict | Action |",
            "|---|---|",
            "| `s7b_holds_on_true_oos` | Keep s7b as preferred research sibling; still not prod |",
            "| `s7b_non_negative_oos_keep_research` | Keep experimental; need more history |",
            "| `s7b_fails_true_oos_revert_baseline` | Drop s7b promotion path; freeze on v3_candidate |",
            "| `s7b_turns_oos_negative` | Do not use s7b; baseline safer on OOS |",
            "| `oos_inconclusive` | Need longer OOS / more symbols |",
            "",
            "### Interpretation rules",
            "1. Discovery lift alone is **not** evidence (contaminated).",
            "2. True OOS is the primary gate for s7b.",
            "3. Large trade removal with flat/negative OOS Δ = not useful.",
            "4. No new filters designed from OOS in this task (no peeking loop).",
            "",
            "## Explicit non-claims",
            "- Not production-ready / not live",
            "- Single symbol, ~90d OOS only",
            "- No production default change",
            "",
            "## Reproducibility",
            "```bash",
            "# Ensure 180d candles exist:",
            "uv run python tools/run_backfill.py --days 180 --granularity 15m",
            "uv run python tools/run_backtest_017_external_oos.py",
            "```",
            "",
            f"JSON: `{json_path}`",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    print(f"Done. Wrote {json_path} and {md_path}")
    print(f"Verdict: {v}")
    print(
        f"OOS: base={oos_full['baseline_r']:.3f}R s7b={oos_full['s7b_r']:.3f}R "
        f"Δ={oos_full['s7b_delta_r']:+.3f}R"
    )
    print(
        f"DISC: base={disc_full['baseline_r']:.3f}R s7b={disc_full['s7b_r']:.3f}R "
        f"Δ={disc_full['s7b_delta_r']:+.3f}R"
    )


if __name__ == "__main__":
    asyncio.run(main())
