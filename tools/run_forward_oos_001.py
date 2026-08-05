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
from datetime import UTC, datetime
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
    aud = audit_window(args.run_id, candles, FORWARD_START, current_checkpoint() or CHECKPOINT_60D)
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

    paper_signals = len(store.get_signals(args.run_id))
    paper_entries = len([o for o in store.get_orders(args.run_id) if o["order_type"] == "ENTRY"])
    paper_exits = len(store.get_exits(args.run_id))
    replay_signals = metrics.trade_count
    replay_entries = metrics.trade_count
    replay_exits = sum(metrics.exit_reasons.values())
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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
