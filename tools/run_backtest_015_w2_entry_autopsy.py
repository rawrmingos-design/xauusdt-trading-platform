#!/usr/bin/env python3
"""PROJECT-BACKTEST-015: W2 entry-context autopsy for v3_candidate.

Goal: explain *which entry contexts* destroy expectancy in walk-forward
Window 2, by contrasting the same contexts in W1/W3.

This is diagnostics only:
- Fixed config: make_v3_candidate_config() (+ v2 quality flags as in BT-012/013)
- No parameter search / no production default changes
- R-multiples grouped by entry_candle_time (not raw dollar PnL sums)
- Stored candles only

Evidence hooks from BACKTEST-013:
- W2 both sides negative; SHORT edge collapse
- Toxic mid-score 75–84 reappears in W2
- EMA-UP entries fail on wide down path
- SIGNAL exits rise / full TP falls
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
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
)


def calc_r(trade: BacktestTrade) -> float:
    if trade.sl_distance <= 0 or trade.quantity <= 0:
        return 0.0
    return trade.gross_pnl / (trade.quantity * trade.sl_distance)


def group_entries(trades: list[BacktestTrade]) -> list[dict[str, Any]]:
    """Collapse multi-leg exits into one entry record with total R."""
    ordered = sorted(trades, key=lambda t: t.entry_candle_time)
    entries: list[dict[str, Any]] = []
    for entry_time, legs_iter in groupby(ordered, key=lambda t: t.entry_candle_time):
        legs = list(legs_iter)
        first = legs[0]
        total_r = sum(calc_r(t) for t in legs)
        exit_reasons = sorted({t.exit_reason for t in legs})
        # Prefer non-partial final reason if present
        primary_exit = exit_reasons[-1]
        for pref in ("TP", "SL", "BREAK_EVEN", "SIGNAL", "EOL"):
            if pref in exit_reasons:
                primary_exit = pref
                break
        entries.append(
            {
                "entry_time": entry_time.isoformat()
                if hasattr(entry_time, "isoformat")
                else str(entry_time),
                "side": first.side,
                "total_r": total_r,
                "win": total_r > 0,
                "score": first.context_score,
                "adx": first.context_adx,
                "ema_trend": first.context_ema_trend,
                "structure": first.context_structure,
                "conflict": bool(first.context_conflict),
                "swing_recency": int(first.context_swing_recency),
                "exit_reason": primary_exit,
                "exit_reasons": exit_reasons,
                "legs": len(legs),
                "pnl": sum(t.pnl for t in legs),
            }
        )
    return entries


def score_band(score: float) -> str:
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


def adx_band(adx: float) -> str:
    if adx < 15:
        return "<15"
    if adx < 20:
        return "15-19"
    if adx < 25:
        return "20-24"
    if adx < 30:
        return "25-29"
    if adx < 40:
        return "30-39"
    if adx < 45:
        return "40-44"
    return "45+"


def swing_band(n: int) -> str:
    if n <= 3:
        return "0-3"
    if n <= 8:
        return "4-8"
    if n <= 15:
        return "9-15"
    return "16+"


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if not entries:
        return {
            "n": 0,
            "win_rate": 0.0,
            "expectancy_r": 0.0,
            "long_n": 0,
            "short_n": 0,
            "long_r": 0.0,
            "short_r": 0.0,
            "net_pnl": 0.0,
        }
    rs = [e["total_r"] for e in entries]
    longs = [e for e in entries if e["side"] == "LONG"]
    shorts = [e for e in entries if e["side"] == "SHORT"]
    return {
        "n": len(entries),
        "win_rate": sum(1 for e in entries if e["win"]) / len(entries),
        "expectancy_r": mean(rs),
        "long_n": len(longs),
        "short_n": len(shorts),
        "long_r": mean([e["total_r"] for e in longs]) if longs else 0.0,
        "short_r": mean([e["total_r"] for e in shorts]) if shorts else 0.0,
        "net_pnl": sum(e["pnl"] for e in entries),
    }


def bucket_stats(entries: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[str(key_fn(e))].append(e)
    out: dict[str, dict[str, Any]] = {}
    for k, group in sorted(groups.items(), key=lambda kv: kv[0]):
        s = summarize(group)
        out[k] = s
    return out


def cross_window_delta(by_window: dict[str, list[dict[str, Any]]], key_fn) -> list[dict[str, Any]]:
    """For each context key, compute W2 vs W1/W3 expectancy."""
    keys: set[str] = set()
    buckets: dict[str, dict[str, dict[str, Any]]] = {}
    for w, entries in by_window.items():
        buckets[w] = bucket_stats(entries, key_fn)
        keys.update(buckets[w].keys())

    rows: list[dict[str, Any]] = []
    for k in sorted(keys):
        w1 = buckets.get("W1", {}).get(k, summarize([]))
        w2 = buckets.get("W2", {}).get(k, summarize([]))
        w3 = buckets.get("W3", {}).get(k, summarize([]))
        # Reference = mean of W1/W3 expectancy when both have samples, else whichever exists
        refs = []
        if w1["n"] > 0:
            refs.append(w1["expectancy_r"])
        if w3["n"] > 0:
            refs.append(w3["expectancy_r"])
        ref_r = mean(refs) if refs else 0.0
        w2_r = w2["expectancy_r"] if w2["n"] > 0 else 0.0
        rows.append(
            {
                "key": k,
                "w1_n": w1["n"],
                "w1_r": w1["expectancy_r"],
                "w2_n": w2["n"],
                "w2_r": w2_r,
                "w3_n": w3["n"],
                "w3_r": w3["expectancy_r"],
                "ref_r": ref_r,
                "delta_w2_vs_ref": w2_r - ref_r if w2["n"] > 0 and refs else None,
                "w2_only_toxic": bool(w2["n"] >= 5 and w2_r < -0.05 and (not refs or ref_r > 0.0)),
            }
        )
    # Sort: most negative W2 delta first, then lowest W2 R
    rows.sort(
        key=lambda r: (
            r["delta_w2_vs_ref"] is None,
            r["delta_w2_vs_ref"] if r["delta_w2_vs_ref"] is not None else 0.0,
            r["w2_r"],
        )
    )
    return rows


def counterfactual_filter(entries: list[dict[str, Any]], reject_fn) -> dict[str, Any]:
    """What if we rejected entries matching reject_fn? Diagnostics only."""
    kept = [e for e in entries if not reject_fn(e)]
    removed = [e for e in entries if reject_fn(e)]
    base = summarize(entries)
    after = summarize(kept)
    rem = summarize(removed)
    return {
        "base_n": base["n"],
        "base_r": base["expectancy_r"],
        "kept_n": after["n"],
        "kept_r": after["expectancy_r"],
        "removed_n": rem["n"],
        "removed_r": rem["expectancy_r"],
        "delta_r": after["expectancy_r"] - base["expectancy_r"],
        "removed_winners": sum(1 for e in removed if e["win"]),
        "removed_losers": sum(1 for e in removed if not e["win"]),
    }


def build_candidate_cfg() -> ConfluenceConfig:
    cfg = make_v3_candidate_config()
    # Match BACKTEST-012/013/014 comparison baseline
    cfg.adx_rising = True
    cfg.ema_slope_alignment = True
    cfg.sl_atr_multiplier = 1.5
    cfg.risk_reward_ratio = 2.5
    return cfg


def run_bt(candles: list[Candle], cfg: ConfluenceConfig) -> list[BacktestTrade]:
    bt = BacktestConfig(
        initial_balance=1000.0,
        fee_rate=0.0005,
        slippage_bps=2.0,
        max_position_size_pct=1.0,
    )
    engine = ConfluenceBacktestEngine(bt, candles, ConfluenceStrategy(cfg))
    return engine.run().trades


def assign_windows(
    entries: list[dict[str, Any]], candles: list[Candle]
) -> dict[str, list[dict[str, Any]]]:
    n = len(candles)
    c_per = n // 3
    t1 = candles[c_per - 1].open_time
    t2 = candles[2 * c_per - 1].open_time
    out: dict[str, list[dict[str, Any]]] = {"W1": [], "W2": [], "W3": []}
    for e in entries:
        # parse iso
        et = e["entry_time"]
        if isinstance(et, str):
            from datetime import datetime as dt

            et_dt = dt.fromisoformat(et)
        else:
            et_dt = et
        if et_dt <= t1:
            out["W1"].append(e)
        elif et_dt <= t2:
            out["W2"].append(e)
        else:
            out["W3"].append(e)
    return out


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return lines


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

    cfg = build_candidate_cfg()
    print(f"Running v3_candidate on {len(candles)} candles...")
    trades = run_bt(candles, cfg)
    entries = group_entries(trades)
    by_w = assign_windows(entries, candles)

    window_summary = {w: summarize(es) for w, es in by_w.items()}
    full_summary = summarize(entries)

    dims = {
        "score_band": lambda e: score_band(e["score"]),
        "adx_band": lambda e: adx_band(e["adx"]),
        "side": lambda e: e["side"],
        "ema_trend": lambda e: e["ema_trend"],
        "structure": lambda e: e["structure"],
        "conflict": lambda e: "CONFLICT" if e["conflict"] else "ALIGNED",
        "swing_recency": lambda e: swing_band(e["swing_recency"]),
        "exit_reason": lambda e: e["exit_reason"],
        "side_x_ema": lambda e: f"{e['side']}|{e['ema_trend']}",
        "side_x_score": lambda e: f"{e['side']}|{score_band(e['score'])}",
        "side_x_exit": lambda e: f"{e['side']}|{e['exit_reason']}",
    }

    cross: dict[str, list[dict[str, Any]]] = {}
    for name, fn in dims.items():
        cross[name] = cross_window_delta(by_w, fn)

    # Focus: toxic mid-score + EMA-UP + short collapse candidates
    filters = {
        "toxic_score_75_84": lambda e: 75.0 <= e["score"] <= 84.0,
        "ema_up": lambda e: e["ema_trend"] == "UP",
        "long_only": lambda e: e["side"] == "LONG",
        "short_only": lambda e: e["side"] == "SHORT",
        "long_and_ema_up": lambda e: e["side"] == "LONG" and e["ema_trend"] == "UP",
        "short_and_ema_up": lambda e: e["side"] == "SHORT" and e["ema_trend"] == "UP",
        "toxic_score_or_ema_up": lambda e: (75.0 <= e["score"] <= 84.0) or e["ema_trend"] == "UP",
        "conflict": lambda e: e["conflict"],
        "signal_exit": lambda e: e["exit_reason"] == "SIGNAL",
    }

    counterfactuals: dict[str, dict[str, Any]] = {}
    for fname, fn in filters.items():
        counterfactuals[fname] = {
            "FULL": counterfactual_filter(entries, fn),
            "W1": counterfactual_filter(by_w["W1"], fn),
            "W2": counterfactual_filter(by_w["W2"], fn),
            "W3": counterfactual_filter(by_w["W3"], fn),
        }

    # Rank toxic contexts: W2 n>=5, negative W2 R, worse than ref
    toxic_hits: list[dict[str, Any]] = []
    for dim, rows in cross.items():
        for r in rows:
            if r["w2_n"] >= 5 and r["w2_r"] < -0.05:
                toxic_hits.append({"dim": dim, **r})
    toxic_hits.sort(
        key=lambda r: (r["delta_w2_vs_ref"] is None, r.get("delta_w2_vs_ref") or 0.0, r["w2_r"])
    )

    # Contribution: share of W2 negative R mass by context
    w2 = by_w["W2"]
    w2_loss_r = sum(e["total_r"] for e in w2 if e["total_r"] < 0)
    loss_contrib = []
    for e in w2:
        if e["total_r"] >= 0:
            continue
        loss_contrib.append(e)

    # group loss R by side_x_score and side_x_ema
    def loss_share(key_fn) -> list[dict[str, Any]]:
        groups: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for e in w2:
            if e["total_r"] >= 0:
                continue
            k = str(key_fn(e))
            groups[k] += e["total_r"]
            counts[k] += 1
        total_neg = sum(groups.values()) or 1.0
        rows = []
        for k, val in groups.items():
            rows.append(
                {
                    "key": k,
                    "loss_r_sum": val,
                    "n_losers": counts[k],
                    "share_of_w2_loss_r": val / total_neg,
                }
            )
        rows.sort(key=lambda r: r["loss_r_sum"])  # most negative first
        return rows

    loss_by = {
        "side": loss_share(lambda e: e["side"]),
        "score_band": loss_share(lambda e: score_band(e["score"])),
        "ema_trend": loss_share(lambda e: e["ema_trend"]),
        "side_x_ema": loss_share(lambda e: f"{e['side']}|{e['ema_trend']}"),
        "side_x_score": loss_share(lambda e: f"{e['side']}|{score_band(e['score'])}"),
        "exit_reason": loss_share(lambda e: e["exit_reason"]),
    }

    # Decision helpers
    best_cf = None
    best_cf_name = None
    for name, data in counterfactuals.items():
        # Prefer filters that lift W2 without wrecking FULL/W1/W3
        w2d = data["W2"]["delta_r"]
        fulld = data["FULL"]["delta_r"]
        w1d = data["W1"]["delta_r"]
        w3d = data["W3"]["delta_r"]
        score = w2d + 0.5 * fulld + 0.25 * (w1d + w3d)
        if best_cf is None or score > best_cf:
            best_cf = score
            best_cf_name = name

    verdict_parts = []
    w2_s = window_summary["W2"]
    if w2_s["short_r"] < 0 and w2_s["long_r"] < 0:
        verdict_parts.append("both_sides_negative_in_w2")
    if any(r["w2_only_toxic"] and r["key"] in ("75-79", "80-84") for r in cross["score_band"]):
        verdict_parts.append("toxic_mid_score_w2_specific")
    ema_up_row = next((r for r in cross["ema_trend"] if r["key"] == "UP"), None)
    if ema_up_row and ema_up_row["w2_n"] >= 5 and ema_up_row["w2_r"] < -0.05:
        verdict_parts.append("ema_up_toxic_in_w2")
    short_row = next((r for r in cross["side"] if r["key"] == "SHORT"), None)
    if (
        short_row
        and short_row["delta_w2_vs_ref"] is not None
        and short_row["delta_w2_vs_ref"] < -0.1
    ):
        verdict_parts.append("short_edge_regime_collapse")

    # Classify counterfactual quality of best filter
    assert best_cf_name is not None
    bcf = counterfactuals[best_cf_name]
    if (
        bcf["W2"]["delta_r"] > 0.03
        and bcf["FULL"]["delta_r"] >= -0.01
        and bcf["W1"]["delta_r"] >= -0.02
        and bcf["W3"]["delta_r"] >= -0.02
        and bcf["W2"]["removed_n"] >= 5
    ):
        cf_verdict = "actionable_context_filter_candidate"
    elif bcf["W2"]["delta_r"] > 0 and bcf["FULL"]["delta_r"] < -0.02:
        cf_verdict = "helps_w2_hurts_full"
    elif bcf["W2"]["delta_r"] <= 0:
        cf_verdict = "no_simple_context_filter_helps"
    else:
        cf_verdict = "weak_signal_needs_holdout"

    payload = {
        "task_id": "PROJECT-BACKTEST-015",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": "XAU-USDT-SWAP",
        "granularity": "15m",
        "candles": len(candles),
        "config": "v3_candidate + adx_rising + ema_slope_alignment + sl1.5/rr2.5",
        "status": "research_diagnostics",
        "production_ready": False,
        "window_summary": window_summary,
        "full_summary": full_summary,
        "cross_window": cross,
        "counterfactuals": counterfactuals,
        "toxic_hits_top": toxic_hits[:40],
        "w2_loss_contribution": loss_by,
        "w2_loss_r_sum": w2_loss_r,
        "best_counterfactual": best_cf_name,
        "counterfactual_verdict": cf_verdict,
        "verdict_tags": verdict_parts,
        # store entries counts only (full entry dump is large); include sample of worst W2
        "worst_w2_entries": sorted(w2, key=lambda e: e["total_r"])[:25],
    }

    report_dir = Path("docs/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"autopsy_BACKTEST-015_{ts}.json"
    md_path = report_dir / "autopsy_BACKTEST-015.md"

    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    # --- Markdown ---
    lines: list[str] = [
        "# PROJECT-BACKTEST-015: W2 Entry-Context Autopsy",
        "",
        "## Objective",
        "Identify **which entry contexts** destroy `v3_candidate` expectancy in walk-forward **W2**,",
        "by contrasting the same contexts in W1/W3. Diagnostics only — no re-tuning production defaults.",
        "",
        "## Status",
        "**Research diagnostics. Not production-ready. No candidate promotion in this task.**",
        "",
        "- Config: `v3_candidate` + V2 quality flags (same baseline as BACKTEST-012/013/014)",
        f"- Candles: {len(candles)} × 15m XAU-USDT-SWAP",
        "- Metric: entry-grouped realized R (not dollar PnL sums)",
        "",
        "## Window Performance (baseline)",
        "",
    ]
    lines.extend(
        md_table(
            ["Window", "N", "Exp R", "Win%", "Long R", "Short R", "Long N", "Short N"],
            [
                [
                    w,
                    str(window_summary[w]["n"]),
                    f"{window_summary[w]['expectancy_r']:.3f}R",
                    f"{window_summary[w]['win_rate'] * 100:.1f}%",
                    f"{window_summary[w]['long_r']:.3f}R",
                    f"{window_summary[w]['short_r']:.3f}R",
                    str(window_summary[w]["long_n"]),
                    str(window_summary[w]["short_n"]),
                ]
                for w in ("W1", "W2", "W3")
            ]
            + [
                [
                    "FULL",
                    str(full_summary["n"]),
                    f"{full_summary['expectancy_r']:.3f}R",
                    f"{full_summary['win_rate'] * 100:.1f}%",
                    f"{full_summary['long_r']:.3f}R",
                    f"{full_summary['short_r']:.3f}R",
                    str(full_summary["long_n"]),
                    str(full_summary["short_n"]),
                ]
            ],
        )
    )

    lines.extend(["", "## Cross-Window Context (key dimensions)", ""])

    for dim in (
        "side",
        "score_band",
        "ema_trend",
        "side_x_ema",
        "side_x_score",
        "exit_reason",
        "conflict",
        "adx_band",
    ):
        lines.append(f"### {dim}")
        lines.append("")
        rows_md = []
        for r in cross[dim]:
            if r["w1_n"] + r["w2_n"] + r["w3_n"] == 0:
                continue
            delta = "n/a" if r["delta_w2_vs_ref"] is None else f"{r['delta_w2_vs_ref']:+.3f}R"
            flag = " **TOXIC-W2**" if r.get("w2_only_toxic") else ""
            rows_md.append(
                [
                    f"{r['key']}{flag}",
                    f"{r['w1_n']}/{r['w1_r']:.3f}R",
                    f"{r['w2_n']}/{r['w2_r']:.3f}R",
                    f"{r['w3_n']}/{r['w3_r']:.3f}R",
                    delta,
                ]
            )
        lines.extend(md_table(["Context", "W1 n/R", "W2 n/R", "W3 n/R", "W2−ref"], rows_md))
        lines.append("")

    lines.extend(
        [
            "## W2 Loss Mass Contribution",
            "",
            f"Sum of negative entry R in W2: **{w2_loss_r:.2f}R**",
            "",
        ]
    )
    for name in ("side", "score_band", "ema_trend", "side_x_ema", "side_x_score", "exit_reason"):
        lines.append(f"### Loss share by {name}")
        lines.append("")
        rows_md = [
            [
                r["key"],
                str(r["n_losers"]),
                f"{r['loss_r_sum']:.2f}R",
                f"{r['share_of_w2_loss_r'] * 100:.1f}%",
            ]
            for r in loss_by[name][:12]
        ]
        lines.extend(md_table(["Key", "Losers", "Loss R sum", "Share of W2 loss R"], rows_md))
        lines.append("")

    lines.extend(["## Counterfactual Filters (diagnostics only — not applied to production)", ""])
    lines.extend(
        md_table(
            ["Filter", "W2 ΔR", "W2 rem n/R", "FULL ΔR", "W1 ΔR", "W3 ΔR", "Rem W/L FULL"],
            [
                [
                    name,
                    f"{data['W2']['delta_r']:+.3f}R",
                    f"{data['W2']['removed_n']}/{data['W2']['removed_r']:.3f}R",
                    f"{data['FULL']['delta_r']:+.3f}R",
                    f"{data['W1']['delta_r']:+.3f}R",
                    f"{data['W3']['delta_r']:+.3f}R",
                    f"{data['FULL']['removed_winners']}/{data['FULL']['removed_losers']}",
                ]
                for name, data in counterfactuals.items()
            ],
        )
    )

    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"- Tags: `{', '.join(verdict_parts) if verdict_parts else 'none'}`",
            f"- Best diagnostic counterfactual: **`{best_cf_name}`**",
            f"- Counterfactual class: **`{cf_verdict}`**",
            "",
            "### Interpretation",
            "1. This task does **not** promote a new filter. It only ranks contexts.",
            "2. A context is interesting if it is **bad in W2** and **not bad (or good) in W1/W3**.",
            "3. Counterfactuals that help W2 but hurt FULL/W1/W3 are **not** candidates.",
            "4. Do **not** retune LP/ADX/regime thresholds on this same 90-day sample as a fix.",
            "",
            "### Recommended next step",
        ]
    )
    if cf_verdict == "actionable_context_filter_candidate":
        lines.append(
            f"- Design an **explicit experimental filter** for `{best_cf_name}` as a new research profile "
            "(e.g. STRATEGY-007), then validate on a **hold-out / extended sample** — not re-fit on W2."
        )
    elif cf_verdict == "helps_w2_hurts_full":
        lines.append(
            "- Context helps W2 only by discarding edge elsewhere → **reject** as global filter; "
            "consider regime-conditional application only with hold-out data."
        )
    else:
        lines.append(
            "- No single simple context cleanly fixes W2. Next: multi-factor interaction study "
            "or extended OOS sample before any new filter."
        )

    lines.extend(
        [
            "",
            "## Explicit non-claims",
            "- Not production-ready",
            "- No live trading / no production default change",
            "- Counterfactuals are **what-if diagnostics**, not optimized strategy variants",
            "",
            "## Reproducibility",
            "```bash",
            "uv run python tools/run_backtest_015_w2_entry_autopsy.py",
            "```",
            "",
            f"JSON: `{json_path}`",
            "",
        ]
    )

    md_path.write_text("\n".join(lines))
    print(f"Done. Wrote {json_path} and {md_path}")
    print("Window summary:")
    for w in ("W1", "W2", "W3"):
        s = window_summary[w]
        print(
            f"  {w}: n={s['n']} exp={s['expectancy_r']:.3f}R L={s['long_r']:.3f}R S={s['short_r']:.3f}R"
        )
    print(f"Best CF: {best_cf_name} class={cf_verdict}")
    print(f"Tags: {verdict_parts}")
    print("Top toxic W2 contexts:")
    for r in toxic_hits[:8]:
        print(
            f"  {r['dim']}={r['key']}: W2 {r['w2_n']}/{r['w2_r']:.3f}R delta={r['delta_w2_vs_ref']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
