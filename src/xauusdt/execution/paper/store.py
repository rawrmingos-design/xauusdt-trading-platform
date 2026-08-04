"""Persistence for shadow/paper harness (PROJECT-PAPER-001).

Stores run metadata, signals, orders, and exits in a local SQLite DB so
results survive process restarts. Idempotency: a run with the same
(run_id) is not re-applied; candle timestamps already recorded are
skipped by the runner when resuming (see runner.py).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.execution.paper.models import (
    PaperRunResult,
)

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    candles_processed INTEGER NOT NULL DEFAULT 0,
    initial_balance REAL NOT NULL,
    final_balance REAL NOT NULL,
    total_pnl REAL NOT NULL,
    max_drawdown REAL NOT NULL,
    max_drawdown_pct REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    side TEXT NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'HOLD',
    executed INTEGER NOT NULL DEFAULT 0,
    buy_score REAL NOT NULL,
    sell_score REAL NOT NULL,
    entry_price REAL NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    rejected INTEGER NOT NULL DEFAULT 0,
    rejection_reasons TEXT NOT NULL DEFAULT '[]',
    UNIQUE(run_id, candle_time)
);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candle_time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL,
    raw_price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'FILLED'
);

CREATE TABLE IF NOT EXISTS paper_exits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    entry_candle_time TEXT NOT NULL,
    exit_candle_time TEXT NOT NULL,
    side TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    pnl REAL NOT NULL,
    fee REAL NOT NULL,
    is_partial INTEGER NOT NULL DEFAULT 0,
    slippage_cost REAL NOT NULL,
    sl_distance REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_run ON paper_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_run ON paper_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_exits_run ON paper_exits(run_id);
"""


class PaperStore:
    """SQLite-backed persistence for paper/shadow runs."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".hermes" / "xauusdt_paper" / "paper_runs.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(paper_signals)")}
        if "signal_type" not in cols:
            self._conn.execute(
                "ALTER TABLE paper_signals ADD COLUMN signal_type TEXT NOT NULL DEFAULT 'HOLD'"
            )
        if "executed" not in cols:
            self._conn.execute(
                "ALTER TABLE paper_signals ADD COLUMN executed INTEGER NOT NULL DEFAULT 0"
            )

    # ------------------------------------------------------------- helpers

    def _run_exists(self, run_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM paper_runs WHERE run_id = ?", (run_id,))
        return cur.fetchone() is not None

    def get_processed_candle_times(self, run_id: str) -> set[str]:
        """Candle times already recorded for this run (idempotent resume)."""
        cur = self._conn.execute(
            "SELECT candle_time FROM paper_signals WHERE run_id = ?", (run_id,)
        )
        return {row["candle_time"] for row in cur.fetchall()}

    def run_exists(self, run_id: str) -> bool:
        return self._run_exists(run_id)

    # ------------------------------------------------------------ writes

    def save_run(self, result: PaperRunResult) -> None:
        """Persist a complete run. Overwrites existing same run_id."""
        now = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO paper_runs
                   (run_id, mode, strategy_version, config_hash, commit_sha,
                    candles_processed, initial_balance, final_balance, total_pnl,
                    max_drawdown, max_drawdown_pct, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    result.run_id,
                    result.mode.value,
                    result.strategy_version,
                    result.config_hash,
                    result.commit_sha,
                    result.candles_processed,
                    result.initial_balance,
                    result.final_balance,
                    result.total_pnl,
                    result.max_drawdown,
                    result.max_drawdown_pct,
                    now,
                ),
            )
            for s in result.signals:
                self._conn.execute(
                    """INSERT OR REPLACE INTO paper_signals
                      (run_id, candle_time, side, signal_type, executed, buy_score, sell_score, entry_price,
                       strategy_version, config_hash, commit_sha, rejected, rejection_reasons)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        s.run_id,
                        s.candle_time.isoformat(),
                        s.side.value,
                        s.signal_type,
                        1 if s.executed else 0,
                        s.buy_score,
                        s.sell_score,
                        s.entry_price,
                        s.strategy_version,
                        s.config_hash,
                        s.commit_sha,
                        1 if s.rejected else 0,
                        json.dumps(s.rejection_reasons),
                    ),
                )
            for o in result.orders:
                self._conn.execute(
                    """INSERT OR REPLACE INTO paper_orders
                       (run_id, candle_time, symbol, side, order_type, price, raw_price,
                        quantity, fee, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        o.run_id,
                        o.candle_time.isoformat(),
                        o.symbol,
                        o.side.value,
                        o.order_type,
                        o.price,
                        o.raw_price,
                        o.quantity,
                        o.fee,
                        o.status,
                    ),
                )
            for e in result.exits:
                self._conn.execute(
                    """INSERT OR REPLACE INTO paper_exits
                       (run_id, entry_candle_time, exit_candle_time, side, exit_reason,
                        entry_price, exit_price, quantity, pnl, fee, is_partial,
                        slippage_cost, sl_distance)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        e.run_id,
                        e.entry_candle_time.isoformat(),
                        e.exit_candle_time.isoformat(),
                        e.side.value,
                        e.exit_reason.value,
                        e.entry_price,
                        e.exit_price,
                        e.quantity,
                        e.pnl,
                        e.fee,
                        1 if e.is_partial else 0,
                        e.slippage_cost,
                        e.sl_distance,
                    ),
                )

    # ------------------------------------------------------------ reads

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """SELECT run_id, mode, strategy_version, config_hash, commit_sha,
                      candles_processed, initial_balance, final_balance, total_pnl,
                      max_drawdown, max_drawdown_pct, created_at
               FROM paper_runs ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM paper_runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_signals(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM paper_signals WHERE run_id = ? ORDER BY candle_time", (run_id,)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_exits(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM paper_exits WHERE run_id = ? ORDER BY exit_candle_time", (run_id,)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_orders(self, run_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM paper_orders WHERE run_id = ? ORDER BY candle_time", (run_id,)
        )
        return [dict(row) for row in cur.fetchall()]
