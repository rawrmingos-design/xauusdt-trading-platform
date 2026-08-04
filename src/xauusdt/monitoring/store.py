"""SQLite persistence for PROJECT-MONITORING-001.

Tables:
  - monitor_heartbeat     : one row per run_id, current runtime state
  - monitor_events        : append-only structured events (dedup-friendly)
  - monitor_alert_state   : active/unresolved alert per (run_id, code)
  - monitor_telegram_delivery : every delivery attempt + result

Survives restart. Idempotent helpers so replaying a poll cycle never doubles
counters or notification state.

All timestamps are stored as UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_heartbeat (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT '',
    strategy_label TEXT NOT NULL DEFAULT '',
    config_hash TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    db_path TEXT NOT NULL DEFAULT '',
    last_processed_candle_time TEXT,
    collector_last_success TEXT,
    collector_last_error TEXT,
    collector_consecutive_errors INTEGER NOT NULL DEFAULT 0,
    collector_total_errors INTEGER NOT NULL DEFAULT 0,
    stale_candle_count INTEGER NOT NULL DEFAULT 0,
    gap_event_count INTEGER NOT NULL DEFAULT 0,
    duplicate_attempt_count INTEGER NOT NULL DEFAULT 0,
    database_error_count INTEGER NOT NULL DEFAULT 0,
    position_open INTEGER NOT NULL DEFAULT 0,
    position_side TEXT NOT NULL DEFAULT '',
    current_equity REAL NOT NULL DEFAULT 0,
    last_error_message TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(run_id, code, timestamp)
);

CREATE TABLE IF NOT EXISTS monitor_alert_state (
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    severity TEXT NOT NULL DEFAULT 'WARNING',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, code)
);

CREATE TABLE IF NOT EXISTS monitor_telegram_delivery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 1,
    detail TEXT NOT NULL DEFAULT ''
);
"""


class MonitorStore:
    """SQLite persistence for monitoring state. Single-writer per DB file."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".hermes" / "xauusdt_paper" / "paper_runs.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=True)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(MONITOR_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- heartbeat
    def upsert_heartbeat(self, hb: dict[str, Any]) -> None:
        hb["updated_at"] = _now_utc()
        self._conn.execute(
            """INSERT OR REPLACE INTO monitor_heartbeat
              (run_id, mode, strategy_label, config_hash, commit_sha, db_path,
               last_processed_candle_time, collector_last_success,
               collector_last_error, collector_consecutive_errors,
               collector_total_errors, stale_candle_count, gap_event_count,
               duplicate_attempt_count, database_error_count, position_open,
               position_side, current_equity, last_error_message, updated_at)
              VALUES (:run_id, :mode, :strategy_label, :config_hash,
               :commit_sha, :db_path, :last_processed_candle_time,
               :collector_last_success, :collector_last_error,
               :collector_consecutive_errors, :collector_total_errors,
               :stale_candle_count, :gap_event_count,
               :duplicate_attempt_count, :database_error_count,
               :position_open, :position_side, :current_equity,
               :last_error_message, :updated_at)""",
            hb,
        )
        self._conn.commit()

    def get_heartbeat(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM monitor_heartbeat WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["position_open"] = bool(d["position_open"])
        return d

    # --------------------------------------------------------------- events
    def record_event(self, ev: dict[str, Any]) -> bool:
        """Insert an event. Returns True if it was new (not a duplicate)."""
        try:
            self._conn.execute(
                """INSERT OR IGNORE INTO monitor_events
                   (run_id, code, severity, message, timestamp, metadata)
                   VALUES (:run_id, :code, :severity, :message, :timestamp,
                           :metadata)""",
                {
                    "run_id": ev["run_id"],
                    "code": ev["code"],
                    "severity": ev["severity"],
                    "message": ev.get("message", ""),
                    "timestamp": ev["timestamp"],
                    "metadata": json.dumps(ev.get("metadata", {}), default=str),
                },
            )
            self._conn.commit()
            return self._conn.total_changes > 0
        except sqlite3.Error:
            self._conn.rollback()
            return True  # do not crash the runner on event-store failure

    def recent_events(self, run_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if run_id is None:
            rows = self._conn.execute(
                "SELECT * FROM monitor_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM monitor_events WHERE run_id=? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):  # oldest -> newest
            d = dict(r)
            d["metadata"] = json.loads(d["metadata"] or "{}")
            out.append(d)
        return out

    # ------------------------------------------------------- alert lifecycle
    def alert_is_active(self, run_id: str, code: str) -> bool:
        row = self._conn.execute(
            "SELECT active FROM monitor_alert_state WHERE run_id=? AND code=?",
            (run_id, code),
        ).fetchone()
        return bool(row and row["active"])

    def alert_open(self, run_id: str, code: str, severity: str, summary: str, ts: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO monitor_alert_state
               (run_id, code, active, severity, first_seen, last_seen, summary)
               VALUES (?,?,1,?,COALESCE((SELECT first_seen FROM monitor_alert_state
                        WHERE run_id=? AND code=?),?),?,?)""",
            (run_id, code, severity, run_id, code, ts, ts, summary),
        )
        self._conn.commit()

    def alert_resolve(self, run_id: str, code: str, ts: str | None = None) -> None:
        self._conn.execute(
            "UPDATE monitor_alert_state SET active=0, last_seen=COALESCE(?, last_seen) "
            "WHERE run_id=? AND code=?",
            (ts, run_id, code),
        )
        self._conn.commit()

    def active_alerts(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM monitor_alert_state WHERE active=1 ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------- telegram delivery
    def record_delivery(
        self,
        run_id: str,
        code: str,
        severity: str,
        ok: bool,
        attempt: int = 1,
        detail: str = "",
        ts: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO monitor_telegram_delivery
               (run_id, code, severity, timestamp, ok, attempt, detail)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, code, severity, ts or _now_utc(), int(ok), attempt, detail),
        )
        self._conn.commit()

    def last_delivery(self, run_id: str, code: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """SELECT * FROM monitor_telegram_delivery
               WHERE run_id=? AND code=? ORDER BY id DESC LIMIT 1""",
            (run_id, code),
        ).fetchone()
        return dict(row) if row else None

    def alert_deliveries(self, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM monitor_telegram_delivery
               WHERE run_id=? ORDER BY id DESC LIMIT ?""",
            (run_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_heartbeat_ts(self, run_id: str, ts: datetime) -> None:
        """Force heartbeat updated_at (deterministic stale tests)."""
        self._conn.execute(
            "UPDATE monitor_heartbeat SET updated_at=? WHERE run_id=?",
            (ts.isoformat(), run_id),
        )
        self._conn.commit()
