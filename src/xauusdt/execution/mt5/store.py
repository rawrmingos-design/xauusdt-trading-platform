"""MT5 execution intent store (PROJECT-MT5-002, Phase 3).

Persists order intents and their lifecycle state in SQLite so execution
survives process restarts. Implements the contract's *persist-before-send*
rule: the request_id (comment) and the intent row are written BEFORE any
``order_send()`` call leaves the process, and the monotonic seq counter
survives restarts (crash-mid-send recovery depends on it).

Schema is intentionally a separate namespace from the paper store — MT5
execution state must never mix with paper run data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xauusdt.execution.errors import ExecutionError
from xauusdt.execution.models import OrderKind, OrderSide, OrderState

log = logging.getLogger(__name__)


class IntentStoreError(ExecutionError):
    """Intent store failure (db locked, schema mismatch, ...)."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS mt5_intents (
    request_id TEXT PRIMARY KEY,      -- comment / idempotency key
    magic INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,               -- LONG | SHORT
    kind TEXT NOT NULL,               -- MARKET | LIMIT | STOP
    volume REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    state TEXT NOT NULL,              -- OrderState.value
    venue_order_id TEXT NOT NULL DEFAULT '',
    position_ticket TEXT NOT NULL DEFAULT '',
    deal_tickets TEXT NOT NULL DEFAULT '[]',   -- JSON list of ints
    filled_volume REAL NOT NULL DEFAULT 0.0,
    filled_price REAL NOT NULL DEFAULT 0.0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mt5_seq (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL
);
"""


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


class Mt5IntentStore:
    """SQLite-backed intent store with monotonic seq (persist-before-send)."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._ns: tuple[int, int] | None = None

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ seq

    def next_seq(self) -> int:
        """Allocate the next monotonic request sequence (thread-safe via txn).

        The seq is allocated and persisted in the SAME transaction that
        inserts the intent row (see ``create_intent``), so a crash between
        allocation and send still yields a valid, unique comment.
        """
        with self._conn:
            cur = self._conn.execute("SELECT seq FROM mt5_seq WHERE id = 1")
            row = cur.fetchone()
            seq = (row["seq"] + 1) if row else 1
            self._conn.execute(
                "INSERT INTO mt5_seq (id, seq) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq = excluded.seq",
                (seq,),
            )
            return seq

    # ------------------------------------------------------------- intents

    def create_intent(
        self,
        *,
        magic: int,
        symbol: str,
        side: OrderSide,
        kind: OrderKind,
        volume: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        run_id: str,
        dedupe: bool = True,
    ) -> str:
        """Persist-before-send: create the intent row + comment atomically.

        Returns the request_id (comment ``xauusdt-<run_id>-<seq>``). The row
        exists in the DB BEFORE the caller sends anything to the venue, so a
        crash mid-send leaves a recoverable record.

        ``dedupe=True`` (default): if an identical intent (same magic/symbol/
        side/kind/volume/sl/tp) is still active, return its existing
        request_id instead of allocating a new one — the idempotency key for
        a re-submitted intent is stable across calls (§4.3).
        """
        active = {
            OrderState.SUBMITTING.value,
            OrderState.SUBMITTED.value,
            OrderState.PARTIALLY_FILLED.value,
            OrderState.UNKNOWN_OUTCOME.value,
        }
        if dedupe:
            placeholders = ",".join("?" for _ in active)
            cur = self._conn.execute(
                f"SELECT request_id FROM mt5_intents "
                f"WHERE magic = ? AND symbol = ? AND side = ? AND kind = ? "
                f"AND ABS(volume - ?) < 1e-9 AND ABS(stop_loss - ?) < 1e-9 "
                f"AND ABS(take_profit - ?) < 1e-9 AND state IN ({placeholders}) "
                f"ORDER BY created_at LIMIT 1",
                (magic, symbol, side.value, kind.value, volume, stop_loss, take_profit, *active),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row["request_id"])

        now = _now_utc()
        with self._conn:
            seq = self.next_seq()
            request_id = f"xauusdt-{run_id}-{seq:08d}"
            self._conn.execute(
                "INSERT INTO mt5_intents (request_id, magic, symbol, side, kind, "
                "volume, entry_price, stop_loss, take_profit, state, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    magic,
                    symbol,
                    side.value,
                    kind.value,
                    volume,
                    entry_price,
                    stop_loss,
                    take_profit,
                    OrderState.SUBMITTING.value,
                    now,
                    now,
                ),
            )
            return request_id

    def update_state(
        self,
        request_id: str,
        state: OrderState,
        *,
        venue_order_id: str = "",
        position_ticket: str = "",
        deal_tickets: list[int] | None = None,
        filled_volume: float | None = None,
        filled_price: float | None = None,
        attempts: int | None = None,
    ) -> None:
        with self._conn:
            sets = ["state = ?", "updated_at = ?"]
            vals: list[object] = [state.value, _now_utc()]
            if venue_order_id:
                sets.append("venue_order_id = ?")
                vals.append(venue_order_id)
            if position_ticket:
                sets.append("position_ticket = ?")
                vals.append(position_ticket)
            if deal_tickets is not None:
                sets.append("deal_tickets = ?")
                vals.append(json.dumps(deal_tickets))
            if filled_volume is not None:
                sets.append("filled_volume = ?")
                vals.append(filled_volume)
            if filled_price is not None:
                sets.append("filled_price = ?")
                vals.append(filled_price)
            if attempts is not None:
                sets.append("attempts = ?")
                vals.append(attempts)
            vals.append(request_id)
            self._conn.execute(
                f"UPDATE mt5_intents SET {', '.join(sets)} WHERE request_id = ?",
                vals,
            )

    def get_intent(self, request_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM mt5_intents WHERE request_id = ?", (request_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def intents_in_states(self, states: list[OrderState]) -> list[dict[str, Any]]:
        names = [s.value for s in states]
        placeholders = ",".join("?" for _ in names)
        cur = self._conn.execute(
            f"SELECT * FROM mt5_intents WHERE state IN ({placeholders}) ORDER BY created_at",
            names,
        )
        return [dict(r) for r in cur.fetchall()]

    def latest_seq(self) -> int:
        cur = self._conn.execute("SELECT seq FROM mt5_seq WHERE id = 1")
        row = cur.fetchone()
        return row["seq"] if row else 0

    def magic_namespace(self) -> tuple[int, int]:
        """Return (base, span) of our magic namespace for adoption filtering.

        Prefers the explicitly configured block (``set_magic_namespace``);
        otherwise derives the minimal block covering all stored magics.
        """
        if self._ns is not None:
            return self._ns
        magics = {r["magic"] for r in self._conn.execute("SELECT DISTINCT magic FROM mt5_intents")}
        if not magics:
            return (0, 0)
        base = min(magics)
        span = max(magics) - base + 1
        return (base, span)

    def set_magic_namespace(self, base: int, span: int) -> None:
        """Configure the magic namespace block [base, base+span).

        Used by the adapter at connect time (contract §4.4) so adoption
        filtering is deterministic even before any intent exists.
        """
        if span <= 0:
            raise ValueError("span must be > 0")
        self._ns = (base, span)
