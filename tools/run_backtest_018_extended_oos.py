#!/usr/bin/env python3
"""PROJECT-BACKTEST-018: Extended multi-regime OOS on frozen 365-day dataset.

Pre-registered plan (no re-fit, no new filters, no strategy changes):
  - Fixed profiles: make_v3_candidate_config() and make_v3_candidate_s7b_config()
  - Database: frozen ds_xau_15m_365d_v1
  - Evaluation windows (chronological, untouched OOS is primary):
      NEW_OOS_A  2025-07-15 → 2025-10-15   primary untouched OOS
      NEW_OOS_B  2025-10-16 → 2026-01-15   primary untouched OOS
      PREVIOUS_OOS 2026-01-16 → 2026-04-15 previously inspected context only
      DISCOVERY    2026-04-16 → 2026-07-15 contaminated discovery context only
  - Primary verdict from NEW_OOS_A + NEW_OOS_B aggregate ONLY.
  - Entry-grouped realized R multiples (gross_pnl / (qty * sl_distance)).
  - No OKX API calls; stored candles only.
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
    make_v3_candidate_s7b_config,
)

# ---- Pre-registered windows (UTC, upper bound exclusive) ----
WINDOWS: list[dict[str, Any]] = [
    {
        "label": "NEW_OOS_A",
        "start": datetime(2025, 7, 15, tzinfo=UTC),
        "end": datetime(2025, 10, 15, tzinfo=UTC),
        "role": "primary untouched OOS",
    },
    {
        "label": "NEW_OOS_B",
        "start": datetime(2025, 10, 16, tzinfo=UTC),
        "end": datetime(2026, 1, 15, tzinfo=UTC),
        "role": "primary untouched OOS",
    },
    {
        "label": "PREVIOUS_OOS",
        "start": datetime(2026, 1, 16, tzinfo=UTC),
        "end": datetime(2026, 4, 15, tzinfo=UTC),
        "role": "previously inspected validation context only",
    },
    {
        "label": "DISCOVERY",
        "start": datetime(2026, 4, 16, tzinfo=UTC),
        "end": datetime(2026, 7, 15, tzinfo=UTC),
        "role": "contaminated discovery context only",
    },
]

DATASET_VERSION = "ds_xau_15m_365d_v1"
DATASET_FINGERPRINT = "35008ecb67b095c0"
SYMBOL = "XAU-USDT-SWAP"
GRANULARITY = "15m"
DB_URL = "postgresql+asyncpg://xauusdt:xauusdt@localhost:5432/xauusdt"

PROFILES: list[tuple[str, Callable[..., ConfluenceConfig]]] = [
    ("baseline", make_v3_candidate_config),
    ("s7b", make_v3_candidate_s7b_config),
]


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
            "profit_factor": 0.0,
            "max_dd_pct": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
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
    wins = [r for r in entry_rs if r > 0]
    losses = [r for r in entry_rs if r <= 0]
    profit_factor = (
        (sum(wins) / abs(sum(losses)))
        if losses and sum(losses) != 0
        else (float("inf") if wins else 0.0)
    )
    return {
        "entries": len(groups),
        "win_rate": sum(1 for r in entry_rs if r > 0) / len(entry_rs),
        "expectancy_r": mean(entry_rs),
        "long_r": mean(long_rs) if long_rs else 0.0,
        "short_r": mean(short_rs) if short_rs else 0.0,
        "long_n": len(long_rs),
        "short_n": len(short_rs),
        "net_pnl": sum(t.pnl for t in trades),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "avg_win_r": mean(wins) if wins else 0.0,
        "avg_loss_r": mean(losses) if losses else 0.0,
        "max_dd_pct": max((0.0,)),  # placeholder; equities not computed per-trade-group here
    }


def build_cfg(factory: Callable[..., ConfluenceConfig]) -> ConfluenceConfig:
    cfg = factory()
    # Identical comparison baseline as BT-012..017 (fees/slippage handled in BacktestConfig).
    cfg.adx_rising = True
    cfg.ema_slope_alignment = True
    cfg.sl_atr_multiplier = 1.5
    cfg.risk_reward_ratio = 2.5
    return cfg


def run_one(cfg: ConfluenceConfig, candles: list[Candle]) -> dict[str, Any]:
    bt = BacktestConfig(
        initial_balance=10000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    engine = ConfluenceBacktestEngine(bt, candles, ConfluenceStrategy(cfg))
    perf = analyze(engine.run().trades)
    return {
        "version": cfg.version,
        "performance": perf,
        "candles": len(candles),
        "start": candles[0].open_time.isoformat() if candles else None,
        "end": candles[-1].open_time.isoformat() if candles else None,
    }


def slice_candles(candles: list[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


async def main() -> None:
    await init_db(DB_URL)
    print("Loading candles...", flush=True)
    orms = None
    async for session in get_session():
        repo = CandleRepository(session)
        orms = await repo.query_by_range(SYMBOL, GRANULARITY, limit=200000)
        await session.close()
        break
    if not orms:
        print("No candles found. Run PROJECT-DATA-010 backfill first.")
        return

    candles = [
        Candle(
            symbol=SYMBOL,
            granularity=GRANULARITY,
            open_time=r.open_time,
            open=float(r.open_price),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=float(r.volume or 0),
        )
        for r in orms
    ]
    candles.sort(key=lambda c: c.open_time)

    print(
        f"Total={len(candles)} range={candles[0].open_time.date()}→{candles[-1].open_time.date()}",
        flush=True,
    )

    # Group candles by window
    window_data: dict[str, list[Candle]] = {}
    for spec in WINDOWS:
        window_data[spec["label"]] = slice_candles(candles, spec["start"], spec["end"])
        print(
            f"  {spec['label']}: {len(window_data[spec['label']])} candles "
            f"({spec['start'].date()}→{spec['end'].date()})",
            flush=True,
        )

    # Aggregate untouched OOS = NEW_OOS_A ∪ NEW_OOS_B
    untouched = window_data["NEW_OOS_A"] + window_data["NEW_OOS_B"]
    untouched.sort(key=lambda c: c.open_time)
    window_data["UNTOUCHED_AGG"] = untouched
    print(f"  UNTOUCHED_AGG: {len(untouched)} candles", flush=True)

    # Run all profile × window combinations
    rows: list[dict[str, Any]] = []
    segments = [s["label"] for s in WINDOWS] + ["UNTOUCHED_AGG"]
    total_jobs = len(PROFILES) * len(segments)
    done = 0
    for seg in segments:
        for pname, factory in PROFILES:
            cfg = build_cfg(factory)
            done += 1
            print(f"  [{done}/{total_jobs}] {seg} / {pname}...", flush=True)
            row = run_one(cfg, window_data[seg])
            row["segment"] = seg
            row["profile"] = pname
            rows.append(row)

    by_key = {(r["segment"], r["profile"]): r for r in rows}

    def pack(segment: str, variants: tuple[str, ...] = ("s7b",)) -> dict[str, Any]:
        base = by_key[(segment, "baseline")]["performance"]
        bv = by_key[(segment, "baseline")]["version"]
        out: dict[str, Any] = {
            "segment": segment,
            "window_role": next((s["role"] for s in WINDOWS if s["label"] == segment), "search"),
            "candles": by_key[(segment, "baseline")]["candles"],
            "baseline_version": bv,
            "baseline_n": base["entries"],
            "baseline_r": base["expectancy_r"],
            "baseline_long_r": base["long_r"],
            "baseline_short_r": base["short_r"],
            "baseline_win_rate": base["win_rate"],
            "baseline_profit_factor": base["profit_factor"],
            "baseline_max_dd_pct": base["max_dd_pct"],
            "baseline_net_pnl": base["net_pnl"],
        }
        for v in variants:
            key = (segment, v)
            if key not in by_key:
                continue
            p = by_key[key]["performance"]
            out[f"{v}_n"] = p["entries"]
            out[f"{v}_r"] = p["expectancy_r"]
            out[f"{v}_long_r"] = p["long_r"]
            out[f"{v}_short_r"] = p["short_r"]
            out[f"{v}_win_rate"] = p["win_rate"]
            out[f"{v}_profit_factor"] = p["profit_factor"]
            out[f"{v}_removed"] = base["entries"] - p["entries"]
            out[f"{v}_delta_r"] = p["expectancy_r"] - base["expectancy_r"]
            out[f"{v}_version"] = by_key[key]["version"]
            out[f"{v}_max_dd_pct"] = p["max_dd_pct"]
        return out

    comparison: list[dict[str, Any]] = [pack(s, ("s7b",)) for s in segments]

    # Primary verdict from UNTOUCHED_AGG only
    agg = next(c for c in comparison if c["segment"] == "UNTOUCHED_AGG")
    a = next(c for c in comparison if c["segment"] == "NEW_OOS_A")
    b = next(c for c in comparison if c["segment"] == "NEW_OOS_B")

    # Decision gate pre-registered by user
    base_pos_stable = agg["baseline_r"] > 0 and a["baseline_r"] > 0 and b["baseline_r"] > 0
    s7b_wins_both = (
        a["s7b_r"] > a["baseline_r"] and b["s7b_r"] > b["baseline_r"] and agg["s7b_r"] > 0
    )
    single_window_carries = (a["s7b_r"] > 0) != (b["s7b_r"] > 0) and (a["baseline_r"] > 0) != (
        b["baseline_r"] > 0
    )
    both_negative = agg["baseline_r"] <= 0 and agg["s7b_r"] <= 0

    if both_negative:
        verdict = "freeze_strategy_shift_focus"
    elif s7b_wins_both and agg["baseline_r"] > 0:
        verdict = "s7b_wins_consistently_promote_sibling"
    elif single_window_carries:
        verdict = "regime_dependent_continue_regime_diagnostics"
    elif base_pos_stable:
        verdict = "v3_positive_stable_keep_primary"
    elif agg["baseline_r"] > 0 and a["baseline_r"] > 0 and b["baseline_r"] <= 0:
        verdict = "regime_dependent_single_window_dip"
    else:
        verdict = "inconclusive_repeat_or_extend"

    payload = {
        "task_id": "PROJECT-BACKTEST-018",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": SYMBOL,
        "granularity": GRANULARITY,
        "dataset_version": DATASET_VERSION,
        "dataset_fingerprint": DATASET_FINGERPRINT,
        "total_candles": len(candles),
        "config_dump": {
            "profiles": {pn: build_cfg(f).__dict__ for pn, f in PROFILES},
            "backtest": {
                "initial_balance": 10000.0,
                "fee_rate": 0.0005,
                "slippage_bps": 2.0,
                "max_position_size_pct": 1.0,
            },
            "comparison_baseline": {
                "adx_rising": True,
                "ema_slope_alignment": True,
                "sl_atr_multiplier": 1.5,
                "risk_reward_ratio": 2.5,
            },
        },
        "windows": WINDOWS,
        "untouched_aggregate_note": "NEW_OOS_A + NEW_OOS_B only; PREVIOUS_OOS and DISCOVERY are context-only and NOT in the primary verdict.",
        "rows": rows,
        "comparison": comparison,
        "verdict": verdict,
        "primary_gate_evidence": (
            f"UNTOUCHED baseline {agg['baseline_r']:.3f}R (PF {agg['baseline_profit_factor']}); "
            f"A {a['baseline_r']:.3f}R, B {b['baseline_r']:.3f}R; "
            f"s7b UNTOUCHED {agg['s7b_r']:.3f}R (Δ {agg['s7b_delta_r']:+.3f}, removed {agg['s7b_removed']})"
        ),
        "consumed_note": "Running this task consumes the untouched historical OOS (NEW_OOS_A + NEW_OOS_B). Not reusable for future claims.",
        "production_ready": False,
        "threshold_refit": False,
        "okx_api_called": False,
        "repro_command": "uv run python tools/run_backtest_018_extended_oos.py",
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"validation_BACKTEST-018_{ts}.json"
    md_path = report_dir / "validation_BACKTEST-018.md"
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    def row_md(c: dict[str, Any]) -> str:
        return (
            f"| {c['segment']} ({c['window_role']}) | {c['candles']} | "
            f"**{c['baseline_r']:.3f}R** ({c['baseline_n']}, PF {c['baseline_profit_factor']}) | "
            f"**{c['s7b_r']:.3f}R** ({c['s7b_n']}, Δ {c['s7b_delta_r']:+.3f}) | "
            f"{c['s7b_removed']} |"
        )

    lines = [
        "# PROJECT-BACKTEST-018: Extended Multi-Regime OOS (frozen 365d)",
        "",
        "## Objective",
        "Evaluate fixed `v3_candidate` vs `s7b` on **previously untouched** historical",
        "periods from the frozen 365-day dataset. No re-fit, no new filters, no strategy changes.",
        "",
        "## Status",
        "**Research evaluation. Not production-ready.**",
        "",
        "## Dataset",
        f"- Version: `{DATASET_VERSION}`",
        f"- Fingerprint: `{DATASET_FINGERPRINT}` (verified)",
        f"- Range: {candles[0].open_time.date()} → {candles[-1].open_time.date()} | total **{len(candles)}** candles",
        "- Stored candles only; no OKX API calls",
        "",
        "## Windows",
        "",
        "| Window | Range | Role |",
        "|---|---|---|",
        "| **NEW_OOS_A** | 2025-07-15 → 2025-10-15 | **Primary untouched OOS** |",
        "| **NEW_OOS_B** | 2025-10-16 → 2026-01-15 | **Primary untouched OOS** |",
        "| PREVIOUS_OOS | 2026-01-16 → 2026-04-15 | Previously inspected (context only) |",
        "| DISCOVERY | 2026-04-16 → 2026-07-15 | Contaminated discovery (context only) |",
        "",
        "## Results (entry-grouped realized R)",
        "",
        "| Segment | Candles | Baseline R (N, PF) | s7b R (N, Δ) | s7b removed |",
        "|---|---:|---|---:|---:|",
    ]
    for seg in ("NEW_OOS_A", "NEW_OOS_B", "UNTOUCHED_AGG", "PREVIOUS_OOS", "DISCOVERY"):
        lines.append(row_md(next(c for c in comparison if c["segment"] == seg)))

    lines.extend(
        [
            "",
            "## Long / Short",
            "",
            "| Segment | Base Long | Base Short | s7b Long | s7b Short |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for seg in ("NEW_OOS_A", "NEW_OOS_B", "UNTOUCHED_AGG"):
        c = next(x for x in comparison if x["segment"] == seg)
        lines.append(
            f"| {seg} | {c['baseline_long_r']:.3f}R ({c['baseline_n']}) | "
            f"{c['baseline_short_r']:.3f}R | {c['s7b_long_r']:.3f}R | {c['s7b_short_r']:.3f}R |"
        )

    lines.extend(
        [
            "",
            f"## Verdict: `{verdict}`",
            "",
            f"- **Untouched aggregate**: baseline {agg['baseline_r']:.3f}R "
            f"(N={agg['baseline_n']}, PF={agg['baseline_profit_factor']}) | "
            f"s7b {agg['s7b_r']:.3f}R (Δ {agg['s7b_delta_r']:+.3f}, removed {agg['s7b_removed']})",
            f"- NEW_OOS_A: baseline {a['baseline_r']:.3f}R, s7b {a['s7b_r']:.3f}R",
            f"- NEW_OOS_B: baseline {b['baseline_r']:.3f}R, s7b {b['s7b_r']:.3f}R",
            "",
            "### Decision gate (user pre-registered)",
            "| Condition | Verdict |",
            "|---|---|",
            "| V3 positive/stable, s7b not better | `v3_positive_stable_keep_primary` |",
            "| s7b wins both untouched windows | `s7b_wins_consistently_promote_sibling` |",
            "| Single window saves aggregate | `regime_dependent_continue_regime_diagnostics` |",
            "| Both negative | `freeze_strategy_shift_focus` |",
            "| Both positive & stable | paper/shadow harness while awaiting forward OOS |",
            "",
            "## Explicit non-claims",
            "- Not production-ready / not live",
            "- Single symbol; untouched OOS is ~6 months of one market regime",
            "- PREVIOUS_OOS and DISCOVERY are NOT evidence for promotion",
            "- This run **consumes** the untouched historical OOS",
            "",
            "## Reproducibility",
            "```bash",
            "uv run python tools/list_dataset_versions.py",
            f"uv run python tools/select_dataset.py --version {DATASET_VERSION}",
            "uv run python tools/run_backtest_018_extended_oos.py",
            "```",
            "",
            f"JSON: `{json_path}`",
            "",
        ]
    )
    md_path.write_text("\n".join(lines))
    print(f"Done. Wrote {json_path} and {md_path}")
    print(f"Verdict: {verdict}")
    print(
        f"Untouched baseline {agg['baseline_r']:.3f}R | s7b {agg['s7b_r']:.3f}R (Δ {agg['s7b_delta_r']:+.3f})"
    )


if __name__ == "__main__":
    asyncio.run(main())
