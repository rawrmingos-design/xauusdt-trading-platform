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

CREATE TABLE IF NOT EXISTS paper_positions (
    run_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    entry_candle_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    stop_loss_price REAL NOT NULL,
    take_profit_price REAL NOT NULL,
    partial_tp_price REAL,
    partial_tp_ratio REAL NOT NULL,
    is_partial_closed INTEGER NOT NULL DEFAULT 0,
    max_mfe_price REAL NOT NULL DEFAULT 0,
    max_mae_price REAL NOT NULL DEFAULT 0,
    balance_snapshot REAL NOT NULL,
    peak_balance_snapshot REAL NOT NULL,
    max_drawdown_snapshot REAL NOT NULL DEFAULT 0,
    pending_parent_pnl TEXT NOT NULL DEFAULT '{}',
    last_processed_candle TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_run ON paper_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_run ON paper_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_exits_run ON paper_exits(run_id);
CREATE INDEX IF NOT EXISTS idx_positions_run ON paper_positions(run_id);
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
        pcols = {r[1] for r in self._conn.execute("PRAGMA table_info(paper_positions)")}
        if "pending_parent_pnl" not in pcols:
            self._conn.execute(
                "ALTER TABLE paper_positions ADD COLUMN pending_parent_pnl TEXT NOT NULL DEFAULT '{}'"
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

    def append_records(self, result: PaperRunResult) -> None:
        """Append signals/orders/exits for a continuous batch (no run replace)."""
        with self._conn:
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

    # ------------------------------------------------------ position state

    def save_position(self, state: dict[str, Any]) -> None:
        """Upsert the live state row (equity counters + open position if any).

        When no position is open, `side` is the empty string sentinel and
        position fields are zeroed; equity counters are always persisted.
        """
        side = state.get("side") or ""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO paper_positions
                  (run_id, symbol, entry_candle_time, entry_price, side, quantity,
                   stop_loss_price, take_profit_price, partial_tp_price,
                   partial_tp_ratio, is_partial_closed, max_mfe_price, max_mae_price,
                   balance_snapshot, peak_balance_snapshot, max_drawdown_snapshot,
                   pending_parent_pnl, last_processed_candle, updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    state["run_id"],
                    state.get("symbol", "XAU-USDT-SWAP"),
                    state.get("entry_candle_time", ""),
                    state.get("entry_price", 0.0),
                    side,
                    state.get("quantity", 0.0),
                    state.get("stop_loss_price", 0.0),
                    state.get("take_profit_price", 0.0),
                    state.get("partial_tp_price"),
                    state.get("partial_tp_ratio", 0.0),
                    1 if state.get("is_partial_closed") else 0,
                    state.get("max_mfe_price", 0.0),
                    state.get("max_mae_price", 0.0),
                    state.get("balance_snapshot", 0.0),
                    state.get("peak_balance_snapshot", 0.0),
                    state.get("max_drawdown_snapshot", 0.0),
                    state.get("pending_parent_pnl", "{}"),
                    state.get("last_processed_candle"),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_position(self, run_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM paper_positions WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def delete_position(self, run_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM paper_positions WHERE run_id = ?", (run_id,))

    def save_run_meta(
        self,
        run_id: str,
        balance: float,
        peak_balance: float,
        max_drawdown: float,
        max_drawdown_pct: float,
        candles_processed: int,
        last_processed_candle: str | None,
    ) -> None:
        """Upsert run-level continuity counters without touching signals/orders."""
        with self._conn:
            self._conn.execute(
                """UPDATE paper_runs
                   SET final_balance = ?, max_drawdown = ?, max_drawdown_pct = ?,
                       candles_processed = ?
                   WHERE run_id = ?""",
                (
                    balance,
                    max_drawdown,
                    max_drawdown_pct,
                    candles_processed,
                    run_id,
                ),
            )

    def ensure_run(
        self,
        run_id: str,
        mode: str,
        strategy_version: str,
        config_hash: str,
        commit_sha: str,
        initial_balance: float,
    ) -> None:
        """Insert a run row if absent (first cycle of a continuous run)."""
        with self._conn:
            self._conn.execute(
                """INSERT OR IGNORE INTO paper_runs
                   (run_id, mode, strategy_version, config_hash, commit_sha,
                    candles_processed, initial_balance, final_balance, total_pnl,
                    max_drawdown, max_drawdown_pct, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    mode,
                    strategy_version,
                    config_hash,
                    commit_sha,
                    0,
                    initial_balance,
                    initial_balance,
                    0.0,
                    0.0,
                    0.0,
                    datetime.now(UTC).isoformat(),
                ),
            )
