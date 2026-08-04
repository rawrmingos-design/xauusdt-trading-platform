"""Daily operational reports for PROJECT-MONITORING-001.

Generates Markdown and JSON snapshots of runtime health, risk state,
position state, and recent events for a run. Reports are labeled
engineering-only / non-promotional (forward data stays quarantined).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.monitoring.store import MonitorStore


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def build_daily_snapshot(
    store: MonitorStore,
    run_id: str,
    risk_state: dict[str, Any] | None = None,
    paper_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the daily operational snapshot for a run."""
    hb = store.get_heartbeat(run_id) or {}
    events = store.recent_events(run_id, limit=200)
    alerts = [a for a in store.active_alerts() if a["run_id"] == run_id]

    # risk metrics (best-effort from caller-provided risk_state)
    risk = risk_state or {}

    return {
        "generated_at": _now_utc(),
        "run_id": run_id,
        "labels": {
            "engineering_only": True,
            "non_promotional": True,
            "strategy_profile": "v3_candidate reference baseline",
            "note": "forward observations are quarantined from strategy redesign",
        },
        "heartbeat": hb,
        "health": {
            "runtime_alive": bool(hb),
            "current_equity": float(hb.get("current_equity", 0.0)),
            "position_open": bool(hb.get("position_open", False)),
            "position_side": hb.get("position_side", ""),
        },
        "risk": {
            "daily_realized_loss_pct": risk.get("daily_realized_loss_pct"),
            "weekly_realized_loss_pct": risk.get("weekly_realized_loss_pct"),
            "consecutive_loss_count": risk.get("consecutive_losses"),
            "cooldown_active": risk.get("cooldown_active"),
            "kill_switch_active": risk.get("kill_switch_active"),
            "risk_rejection_count": risk.get("risk_rejection_count", 0),
        },
        "position": {
            "open": bool(hb.get("position_open", False)),
            "side": hb.get("position_side", ""),
            "last_update": hb.get("last_processed_candle_time"),
        },
        "counters": {
            "stale_candle_count": hb.get("stale_candle_count", 0),
            "gap_event_count": hb.get("gap_event_count", 0),
            "duplicate_attempt_count": hb.get("duplicate_attempt_count", 0),
            "database_error_count": hb.get("database_error_count", 0),
            "collector_total_errors": hb.get("collector_total_errors", 0),
        },
        "alerts": alerts,
        "recent_events": events[-50:],
        "event_count": len(events),
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    """Render a daily operational report as Markdown."""
    risk = snapshot.get("risk", {})
    lines = [
        "# Paper Runtime — Daily Operational Report",
        "",
        f"**Run:** `{snapshot['run_id']}`  ",
        f"**Generated:** {snapshot['generated_at']}  ",
        f"**Profile:** {snapshot['labels']['strategy_profile']}  ",
        "**Label:** engineering-only / non-promotional (forward data quarantined)",
        "",
        "## Health",
        f"- Runtime alive: `{snapshot['health']['runtime_alive']}`",
        f"- Current equity: `${snapshot['health']['current_equity']:,.2f}`",
        f"- Position: {snapshot['health']['position_side'] or 'flat'}"
        + (" (open)" if snapshot["health"]["position_open"] else ""),
        "",
        "## Risk",
        f"- Daily realized loss: `{risk.get('daily_realized_loss_pct')}`",
        f"- Weekly realized loss: `{risk.get('weekly_realized_loss_pct')}`",
        f"- Consecutive losses: `{risk.get('consecutive_loss_count')}`",
        f"- Cooldown active: `{risk.get('cooldown_active')}`",
        f"- Kill switch active: `{risk.get('kill_switch_active')}`",
        f"- Risk rejections: `{risk.get('risk_rejection_count')}`",
        "",
        "## Counters",
        f"- Stale candles: `{snapshot['counters']['stale_candle_count']}`",
        f"- Candle gaps: `{snapshot['counters']['gap_event_count']}`",
        f"- Duplicate attempts: `{snapshot['counters']['duplicate_attempt_count']}`",
        f"- DB write failures: `{snapshot['counters']['database_error_count']}`",
        f"- Collector total errors: `{snapshot['counters']['collector_total_errors']}`",
        "",
        "## Active Alerts",
    ]
    if snapshot["alerts"]:
        for a in snapshot["alerts"]:
            lines.append(f"- `{a['code']}` ({a['severity']}) since {a['first_seen']}")
    else:
        lines.append("- (none)")
    lines += ["", "## Recent Events", ""]
    for ev in snapshot["recent_events"]:
        lines.append(
            f"- `{ev['severity']}` `{ev['code']}` @ {ev['timestamp']}"
            + (f" — {ev['message']}" if ev.get("message") else "")
        )
    lines.append("")
    lines.append("> Engineering-only operational view. Does not constitute a strategy verdict.")
    return "\n".join(lines)


def write_daily_report(
    store: MonitorStore,
    run_id: str,
    out_dir: str | Path,
    risk_state: dict[str, Any] | None = None,
    paper_state: dict[str, Any] | None = None,
    prefix: str = "monitor",
) -> tuple[Path, Path]:
    """Write daily JSON + Markdown reports. Returns (json_path, md_path)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    today = f"{prefix}_{stamp}"
    snap = build_daily_snapshot(store, run_id, risk_state, paper_state)
    jp = out / f"{today}.json"
    jp.write_text(json.dumps(snap, indent=2, default=str))
    mp = out / f"{today}.md"
    mp.write_text(render_markdown(snap))
    return jp, mp
