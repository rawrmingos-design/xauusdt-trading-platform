"""SQLite persistence for the risk engine (PROJECT-RISK-001).

Stores risk counters, kill-switch state, and every risk decision scoped by
run_id. State survives normal and crash-style restarts. Replays of already
processed candles do not change counters (decision rows are keyed by
(run_id, candle_time)).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.risk.models import KillSwitchState, RiskDecision, RiskStateSnapshot

RISK_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_state (
    run_id TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    daily_realized_loss REAL NOT NULL DEFAULT 0,
    weekly_realized_loss REAL NOT NULL DEFAULT 0,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    day_key TEXT,
    week_key TEXT,
    last_rejection_codes TEXT NOT NULL DEFAULT '[]',
    last_rejection_time TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_kill_switch (
    run_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    timestamp TEXT,
    actor TEXT NOT NULL DEFAULT 'cli',
    source TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    approved INTEGER NOT NULL,
    rejection_codes TEXT NOT NULL DEFAULT '[]',
    rejection_reasons TEXT NOT NULL DEFAULT '[]',
    quantity REAL NOT NULL DEFAULT 0,
    risk_budget REAL NOT NULL DEFAULT 0,
    risk_per_unit REAL NOT NULL DEFAULT 0,
    estimated_entry_fee REAL NOT NULL DEFAULT 0,
    estimated_exit_fee REAL NOT NULL DEFAULT 0,
    estimated_entry_slippage REAL NOT NULL DEFAULT 0,
    estimated_exit_slippage REAL NOT NULL DEFAULT 0,
    entry_price REAL NOT NULL DEFAULT 0,
    stop_price REAL NOT NULL DEFAULT 0,
    UNIQUE(run_id, candle_time)
);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_run ON risk_decisions(run_id);
"""


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


class RiskStore:
    """SQLite-backed persistence for risk counters and decisions."""

    def __init__(self, db_path: str | Path | None = None, run_id: str = "") -> None:
        if db_path is None:
            db_path = Path.home() / ".hermes" / "xauusdt_paper" / "paper_runs.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(RISK_SCHEMA)
        self._conn.commit()
        self._run_id = run_id

    def close(self) -> None:
        self._conn.close()

    def _run(self) -> str:
        return self._run_id

    # ------------------------------------------------------------- helpers

    def _ensure_state_row(self, run_id: str, equity: float) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO risk_state
               (run_id, equity, daily_realized_loss, weekly_realized_loss,
                consecutive_losses, cooldown_until, day_key, week_key,
                last_rejection_codes, last_rejection_time, updated_at)
               VALUES (?,?,0,0,0,NULL,NULL,NULL,'[]',NULL,?)""",
            (run_id, equity, _now_utc()),
        )

    def _ensure_kill_switch_row(self, run_id: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO risk_kill_switch
               (run_id, enabled, reason, timestamp, actor, source)
               VALUES (?,0,'',NULL,'cli','manual')""",
            (run_id,),
        )

    # -------------------------------------------------------------- writes

    def save_kill_switch(self, run_id: str, state: KillSwitchState) -> None:
        self._ensure_kill_switch_row(run_id)
        self._conn.execute(
            """INSERT OR REPLACE INTO risk_kill_switch
               (run_id, enabled, reason, timestamp, actor, source)
               VALUES (?,?,?,?,?,?)""",
            (
                run_id,
                1 if state.enabled else 0,
                state.reason,
                state.timestamp,
                state.actor,
                state.source,
            ),
        )
        self._conn.commit()

    def load_kill_switch(self, run_id: str) -> KillSwitchState:
        self._ensure_kill_switch_row(run_id)
        row = self._conn.execute(
            "SELECT enabled, reason, timestamp, actor, source FROM risk_kill_switch WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return KillSwitchState(
            enabled=bool(row["enabled"]),
            reason=row["reason"] or "",
            timestamp=row["timestamp"],
            actor=row["actor"] or "cli",
            source=row["source"] or "manual",
        )

    def save_costate(
        self,
        run_id: str,
        equity: float,
        daily_realized_loss: float,
        weekly_realized_loss: float,
        consecutive_losses: int,
        cooldown_until: str | None,
        last_rejection_codes: list[str],
        last_rejection_time: str | None,
        day_key: str | None = None,
        week_key: str | None = None,
    ) -> None:
        """Upsert risk counters (no decision rows)."""
        self._ensure_state_row(run_id, equity)
        self._conn.execute(
            """INSERT OR REPLACE INTO risk_state
               (run_id, equity, daily_realized_loss, weekly_realized_loss,
                consecutive_losses, cooldown_until, day_key, week_key,
                last_rejection_codes, last_rejection_time, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                equity,
                daily_realized_loss,
                weekly_realized_loss,
                consecutive_losses,
                cooldown_until,
                day_key,
                week_key,
                json.dumps(last_rejection_codes),
                last_rejection_time,
                _now_utc(),
            ),
        )
        self._conn.commit()

    def save_decision(self, decision: RiskDecision) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO risk_decisions
               (run_id, candle_time, signal_type, approved, rejection_codes,
                rejection_reasons, quantity, risk_budget, risk_per_unit,
                estimated_entry_fee, estimated_exit_fee, estimated_entry_slippage,
                estimated_exit_slippage, entry_price, stop_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision.run_id,
                decision.candle_time.isoformat(),
                decision.signal_type,
                1 if decision.approved else 0,
                json.dumps(decision.rejection_codes),
                json.dumps(decision.rejection_reasons),
                decision.quantity,
                decision.risk_budget,
                decision.risk_per_unit,
                decision.estimated_entry_fee,
                decision.estimated_exit_fee,
                decision.estimated_entry_slippage,
                decision.estimated_exit_slippage,
                decision.entry_price,
                decision.stop_price,
            ),
        )
        self._conn.commit()

    def load_state(
        self,
        run_id: str,
        default_equity: float = 10_000.0,
    ) -> dict[str, Any]:
        """Load risk counters; returns defaults when no prior state exists."""
        row = self._conn.execute("SELECT * FROM risk_state WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return {
                "equity": default_equity,
                "daily_realized_loss": 0.0,
                "weekly_realized_loss": 0.0,
                "consecutive_losses": 0,
                "cooldown_until": None,
                "day_key": None,
                "week_key": None,
                "last_rejection_codes": [],
                "last_rejection_time": None,
            }
        return {
            "equity": row["equity"],
            "daily_realized_loss": row["daily_realized_loss"],
            "weekly_realized_loss": row["weekly_realized_loss"],
            "consecutive_losses": row["consecutive_losses"],
            "cooldown_until": row["cooldown_until"],
            "day_key": row["day_key"],
            "week_key": row["week_key"],
            "last_rejection_codes": json.loads(row["last_rejection_codes"] or "[]"),
            "last_rejection_time": row["last_rejection_time"],
        }

    def load_decisions(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM risk_decisions WHERE run_id=? ORDER BY candle_time",
            (run_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["rejection_codes"] = json.loads(r["rejection_codes"] or "[]")
            r["rejection_reasons"] = json.loads(r["rejection_reasons"] or "[]")
        return rows

    def count_decisions(self, run_id: str) -> tuple[int, int]:
        """Return (approved_count, rejected_count)."""
        cur = self._conn.execute(
            """SELECT
                 SUM(CASE WHEN approved=1 THEN 1 ELSE 0 END) AS approved,
                 SUM(CASE WHEN approved=0 THEN 1 ELSE 0 END) AS rejected
               FROM risk_decisions WHERE run_id=?""",
            (run_id,),
        )
        row = cur.fetchone()
        return (int(row["approved"] or 0), int(row["rejected"] or 0))

    def snapshot(
        self,
        run_id: str,
        equity: float,
        open_positions: int,
        max_open_positions: int,
        now: datetime,
        daily_limit: float = 0.0,
        weekly_limit: float = 0.0,
    ) -> RiskStateSnapshot:
        """Build a human/CLI-renderable snapshot of risk state."""
        st = self.load_state(run_id, default_equity=equity)
        ks = self.load_kill_switch(run_id)
        cooldown_until = st["cooldown_until"]
        cooldown_active = cooldown_until is not None and now < datetime.fromisoformat(
            cooldown_until
        )
        return RiskStateSnapshot(
            run_id=run_id,
            equity=equity,
            daily_realized_loss=st["daily_realized_loss"],
            daily_limit=daily_limit,
            weekly_realized_loss=st["weekly_realized_loss"],
            weekly_limit=weekly_limit,
            consecutive_losses=st["consecutive_losses"],
            cooldown_until=cooldown_until,
            cooldown_active=cooldown_active,
            kill_switch=ks,
            open_positions=open_positions,
            max_open_positions=max_open_positions,
            last_rejection_codes=st["last_rejection_codes"],
            last_rejection_time=st["last_rejection_time"],
        )
