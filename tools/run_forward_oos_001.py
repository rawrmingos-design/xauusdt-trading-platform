#!/usr/bin/env python3
"""PROJECT-FORWARD-OOS-001 — frozen forward OOS evaluation CLI.

Pre-registered before the 60-day checkpoint. Anti-peeking:
  - `status`   : operational & data-quality info ONLY (no performance metrics)
  - `evaluate` : REFUSES before 2026-09-13T00:00:00Z; no force/bypass option

Replay reads ONLY stored candles (paper_candles) from the paper DB; the
runtime persists candles as it polls. No live OKX call during evaluation.

Usage:
  python tools/run_forward_oos_001.py status  --run-id ... --db ...
  python tools/run_forward_oos_001.py evaluate --checkpoint 60d --run-id ... --db ...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from xauusdt.execution.paper.store import PaperStore
from xauusdt.forward_oos.evaluator import (
    CHECKPOINT_60D,
    build_parity,
    checkpoint_end,
    compute_metrics,
    current_checkpoint,
    evaluate_gate,
    replay_offline,
    utc_now,
)
from xauusdt.forward_oos.manifest import FORWARD_START, audit_window

DEFAULT_RUN_ID = "forward-paper-v3-candidate-20260716"
DEFAULT_DB = "/var/lib/xauusdt/paper_runs.db"


def _store(db: str) -> PaperStore:
    return PaperStore(db)


def _finalized_boundary() -> datetime:
    """Most recent 15m grid slot certain to be finalized.

    A 15m candle whose bar is ``HH:MM`` is only finalized once the clock
    passes the following grid boundary (e.g. a 05:00 bar is final after
    05:15). Auditing/backfill up to raw ``utc_now`` can count the in-flight
    candle as a false gap. Floor ``now`` to the last 15m boundary and step
    back one slot so we never demand a candle not yet finalized.
    """
    now = utc_now()
    return (
        now - timedelta(minutes=now.minute % 15, seconds=now.second, microseconds=now.microsecond)
    ) - timedelta(minutes=15)


def _record_time(rec: dict[str, Any], key: str) -> datetime:
    """Parse a stored UTC timestamp column into a tz-aware datetime."""
    return datetime.fromisoformat(str(rec[key]).replace("Z", "+00:00"))


def _records_from(candles: list[Any], start: datetime | None) -> list[Any]:
    """Filter candles to those at/after `start` (None => unchanged)."""
    if start is None:
        return candles
    return [c for c in candles if c.open_time >= start]


def _monitor_heartbeat(db: str, run_id: str) -> dict[str, Any]:
    """Read monitor_heartbeat row (operational only)."""
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM monitor_heartbeat WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
        if row is None:
            return {"alive": False, "reason": "no_heartbeat_row"}
        from datetime import datetime as _dt

        updated = _dt.fromisoformat(row["updated_at"])
        age = (utc_now() - updated).total_seconds()
        if age < 300:
            return {"alive": True, "heartbeat_age_seconds": round(age, 1)}
        return {
            "alive": False,
            "reason": f"stale ({round(age, 1)}s)",
            "heartbeat_age_seconds": round(age, 1),
        }
    except Exception as e:  # operational robustness
        return {"alive": False, "reason": str(e)}


def _db_integrity(db: str) -> str:
    try:
        conn = sqlite3.connect(db)
        res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        return res
    except Exception as e:
        return f"error:{e}"


def _deployment_meta(db: str) -> dict[str, Any]:
    """deployment.json next to the DB, plus paper_runs row for config hash."""
    meta: dict[str, Any] = {}
    dep_path = Path(db).parent / "deployment.json"
    if dep_path.exists():
        try:
            meta["deployment"] = json.loads(dep_path.read_text())
        except Exception:
            meta["deployment"] = {"error": "unreadable"}
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT run_id, config_hash, commit_sha, strategy_version, "
            "candles_processed, created_at FROM paper_runs WHERE run_id = ?",
            (meta.get("run_id") or DEFAULT_RUN_ID,),
        ).fetchone()
        conn.close()
        if row is not None:
            meta["run"] = dict(row)
    except Exception as e:
        meta["run"] = {"error": str(e)}
    return meta


def _backup_freshness(db: str) -> dict[str, Any]:
    """Newest backup file in the sibling backups dir, if any."""
    try:
        backups = Path(db).parent.parent / "backups" / "xauusdt"
        if not backups.exists():
            backups = Path("/var/backups/xauusdt")
        files = sorted(backups.glob("paper_runs_*.db"), reverse=True)
        if not files:
            return {"exists": False}
        mtime = datetime.fromtimestamp(files[0].stat().st_mtime, UTC)
        return {
            "exists": True,
            "newest": files[0].name,
            "age_seconds": round((utc_now() - mtime).total_seconds()),
        }
    except Exception as e:
        return {"exists": False, "error": str(e)}


# ---------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    store = _store(args.db)
    candles = store.load_candles(args.run_id)
    # Audit to utc_now: coverage reflects finalized candles only. Auditing to
    # CHECKPOINT_60D counts every future 15m slot as a gap, which falsely
    # signals missing data before the checkpoint wall-clock has passed.
    aud = audit_window(args.run_id, candles, FORWARD_START, _finalized_boundary())
    hb = _monitor_heartbeat(args.db, args.run_id)
    integ = _db_integrity(args.db)
    backup = _backup_freshness(args.db)
    meta = _deployment_meta(args.db)
    store.close()

    cp = current_checkpoint()
    locked = cp is None
    print("Forward start :", FORWARD_START.isoformat())
    print("Latest candle :", aud.last_candle_time or "(none)")
    print(f"Coverage      : {aud.coverage_pct}% ({aud.actual_count}/{aud.expected_count})")
    print("Gaps          :", aud.gap_count)
    print("Duplicates    :", aud.duplicate_count)
    print("Heartbeat     :", "healthy" if hb.get("alive") else f"unhealthy ({hb.get('reason')})")
    print("DB integrity  :", integ)
    print("Backup        :", "current" if backup.get("exists") else "missing")
    if meta.get("deployment"):
        d = meta["deployment"]
        print("Commit        :", d.get("commit_sha"))
        print("Config hash   :", d.get("config_hash"))
    if locked:
        print("Evaluation    :", f"LOCKED until {CHECKPOINT_60D.isoformat()}")
    else:
        print("Evaluation    :", f"UNLOCKED (checkpoint {cp.isoformat()})")
    return 0


# --------------------------------------------------------------- evaluate


def cmd_evaluate(args: argparse.Namespace) -> int:
    evaluate_gate()  # raises SystemExit(1) if locked (anti-peek)
    end = checkpoint_end(args.checkpoint)
    if end is None:
        print(f"error: unknown checkpoint {args.checkpoint!r}", file=sys.stderr)
        return 2
    store = _store(args.db)

    # config-hash validation: paper_runs row must exist with a config_hash
    row = store.get_run(args.run_id)
    if row is None:
        print(f"error: run {args.run_id!r} not found in paper_runs", file=sys.stderr)
        store.close()
        return 2
    cfg_hash = row.get("config_hash")
    if not cfg_hash:
        print("error: run has no config_hash", file=sys.stderr)
        store.close()
        return 2

    candles = store.load_candles(args.run_id, start=FORWARD_START, end=end)
    aud = audit_window(args.run_id, candles, FORWARD_START, end)
    if not aud.complete:
        print(
            f"error: dataset incomplete (coverage={aud.coverage_pct}%, "
            f"gaps={aud.gap_count}, dupes={aud.duplicate_count}). "
            "Refusing to evaluate gap-contaminated forward data.",
            file=sys.stderr,
        )
        store.close()
        return 2

    replay = replay_offline(candles)
    metrics = compute_metrics(replay)

    # runtime_observation_start: when the live paper runtime first became
    # active (paper_runs.created_at). Parity is only meaningful from this
    # point — the pre-deployment window has no live execution, and backfilled
    # candles must never be mistaken for live paper execution.
    run_created = row.get("created_at") if row else None
    runtime_obs_start = run_created if run_created else None

    # Build parity on the live-observation window ONLY: filter paper records
    # to candle_time >= runtime_observation_start, and re-run the offline
    # replay on that same candle sub-window. Otherwise parity would compare
    # ~60d of replay signals against a few days of live paper signals.
    obs_start = None
    if runtime_obs_start:
        try:
            obs_start = datetime.fromisoformat(runtime_obs_start)
        except ValueError:
            obs_start = None

    observ = _records_from(candles, obs_start) if obs_start else candles
    paper_signals_all = store.get_signals(args.run_id)
    paper_signals = len(
        [
            s
            for s in paper_signals_all
            if obs_start is None or _record_time(s, "candle_time") >= obs_start
        ]
    )
    paper_orders = store.get_orders(args.run_id)
    paper_entries = len(
        [
            o
            for o in paper_orders
            if o["order_type"] == "ENTRY"
            and (obs_start is None or _record_time(o, "candle_time") >= obs_start)
        ]
    )
    paper_exits_all = store.get_exits(args.run_id)
    paper_exits = len(
        [
            e
            for e in paper_exits_all
            if obs_start is None or _record_time(e, "exit_candle_time") >= obs_start
        ]
    )

    replay_obs = replay_offline(observ)
    r_metrics = compute_metrics(replay_obs)
    replay_signals = r_metrics.trade_count
    replay_entries = r_metrics.trade_count
    replay_exits = sum(r_metrics.exit_reasons.values())
    parity = build_parity(
        paper_signals,
        replay_signals,
        paper_entries,
        replay_entries,
        paper_exits,
        replay_exits,
    )
    store.close()

    report = {
        "checkpoint": args.checkpoint,
        "executed_utc": utc_now().isoformat(),
        "run_id": args.run_id,
        "config_hash": cfg_hash,
        "runtime_observation_start": runtime_obs_start,
        "dataset": aud.manifest_json,
        "metrics": {
            "trade_count": metrics.trade_count,
            "long_count": metrics.long_count,
            "short_count": metrics.short_count,
            "expectancy_r": metrics.expectancy_r,
            "profit_factor": metrics.profit_factor,
            "win_rate_pct": metrics.win_rate_pct,
            "net_pnl": metrics.net_pnl,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "avg_realized_r": metrics.avg_realized_r,
            "exit_reasons": metrics.exit_reasons,
            "break_even_count": metrics.break_even_count,
        },
        "parity": {
            "paper_signals": parity.signal_count_paper,
            "replay_signals": parity.signal_count_replay,
            "paper_entries": parity.entry_count_paper,
            "replay_entries": parity.entry_count_replay,
            "paper_exits": parity.exit_count_paper,
            "replay_exits": parity.exit_count_replay,
            "signal_parity_pct": parity.signal_parity_pct,
            "entry_parity_pct": parity.entry_parity_pct,
            "exit_parity_pct": parity.exit_parity_pct,
        },
    }
    out = Path(args.out_dir) / f"forward_oos_{args.checkpoint}_{utc_now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nreport written: {out}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """Backfill paper_candles from OKX history for [start, latest finalized).

    OPS task run pre-checkpoint (NOT evaluation). No performance metrics are
    computed or displayed. Idempotent: existing (run_id, open_time) rows are
    ignored, so runtime-persisted candles survive and only missing slots are
    filled. The checkpoint evaluator still reads ONLY stored candles.
    """
    import asyncio

    from xauusdt.exchange.okx_client import OKXClient

    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))

    async def _run() -> dict[str, int]:
        store = _store(args.db)
        end = _finalized_boundary()
        existing_before = store.candle_count(args.run_id)
        new = 0
        try:
            async with OKXClient() as client:
                async for candle in client.fetch_candles_paginated(
                    symbol=args.symbol,
                    granularity=args.granularity,
                    start_time=start,
                    end_time=end,
                ):
                    if store.append_candles(args.run_id, [candle]):
                        new += 1
        finally:
            store.close()
        return {"filled": new, "existing_before": existing_before}

    stats = asyncio.run(_run())

    # post-audit: coverage/gaps/dupes on the backfilled range (operational only)
    store = _store(args.db)
    candles_now = store.load_candles(args.run_id, start=FORWARD_START)
    aud = audit_window(args.run_id, candles_now, FORWARD_START, _finalized_boundary())
    store.close()

    r = args.run_id
    print(f"Run ID          : {r}")
    print(f"Requested start : {start.isoformat()}")
    print(f"Finalized end   : {aud.last_candle_time or '(none)'}")
    print(f"Existing        : {stats['existing_before']}")
    print(f"Inserted        : {stats['filled']}")
    print(f"Gaps            : {aud.gap_count}")
    print(f"Duplicates      : {aud.duplicate_count}")
    print(f"Coverage        : {aud.coverage_pct}%")
    print("Performance     : LOCKED until 2026-09-13T00:00:00Z")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PROJECT-FORWARD-OOS-001 forward OOS CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="operational/data-quality status (no metrics)")
    ps.add_argument("--run-id", default=DEFAULT_RUN_ID)
    ps.add_argument("--db", default=DEFAULT_DB)
    ps.set_defaults(func=cmd_status)

    pe = sub.add_parser("evaluate", help="frozen checkpoint evaluation (LOCKED pre-2026-09-13)")
    pe.add_argument("--checkpoint", choices=["60d", "90d"], required=True)
    pe.add_argument("--run-id", default=DEFAULT_RUN_ID)
    pe.add_argument("--db", default=DEFAULT_DB)
    pe.add_argument("--out-dir", default="docs/reports")
    pe.set_defaults(func=cmd_evaluate)

    pb = sub.add_parser(
        "backfill", help="OPS: fill paper_candles from OKX history (pre-checkpoint, no metrics)"
    )
    pb.add_argument("--run-id", default=DEFAULT_RUN_ID)
    pb.add_argument("--db", default=DEFAULT_DB)
    pb.add_argument("--symbol", default="XAU-USDT-SWAP")
    pb.add_argument("--granularity", default="15m")
    pb.add_argument(
        "--start",
        default=f"{FORWARD_START.isoformat()}Z",
        help="UTC start, e.g. 2026-07-16T00:00:00Z",
    )
    pb.set_defaults(func=cmd_backfill)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
