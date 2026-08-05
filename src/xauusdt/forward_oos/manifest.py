"""PROJECT-FORWARD-OOS-001 — forward dataset manifest & data-quality audit.

Operational-only. Never computes or prints strategy performance metrics.
All cutoffs are UTC-aware. Deterministic: same stored candles produce the
same manifest (fingerprint is over the candle series, not wall-clock time).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from xauusdt.exchange.models import Candle

GRANULARITY_MINUTES = 15
EXPECTED_INTERVAL_MINUTES = 15

FORWARD_START = datetime(2026, 7, 16, tzinfo=UTC)
CHECKPOINT_60D = datetime(2026, 9, 13, tzinfo=UTC)
CHECKPOINT_90D = datetime(2026, 10, 13, tzinfo=UTC)


@dataclass
class CandleAudit:
    """Data-quality audit of the stored forward candle window."""

    run_id: str
    window_start: datetime
    window_end: datetime
    expected_count: int = 0
    actual_count: int = 0
    coverage_pct: float = 0.0
    gap_count: int = 0
    duplicate_count: int = 0
    first_candle_time: str = ""
    last_candle_time: str = ""
    out_of_window: int = 0
    fingerprint: str = ""
    manifest_json: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """True when the window is fully covered (no gaps, no dupes)."""
        return (
            self.gap_count == 0
            and self.duplicate_count == 0
            and self.coverage_pct >= 99.5
            and self.out_of_window == 0
        )


def _expected_candle_count(start: datetime, end: datetime) -> int:
    """Number of 15m candles in [start, end)."""
    diff_min = int((end - start).total_seconds() // 60)
    return diff_min // EXPECTED_INTERVAL_MINUTES


def audit_window(
    run_id: str,
    candles: list[Candle],
    start: datetime,
    end: datetime,
) -> CandleAudit:
    """Audit stored candles against the expected 15m grid in [start, end).

    Detects gaps (missing grid slots), duplicates (same open_time twice),
    and out-of-window rows. Fingerprint covers the full OHLCV series so the
    manifest is reproducible and tamper-evident.
    """
    aud = CandleAudit(run_id=run_id, window_start=start, window_end=end)
    aud.expected_count = _expected_candle_count(start, end)

    in_window = [c for c in candles if start <= c.open_time < end]
    aud.out_of_window = len(candles) - len(in_window)
    aud.actual_count = len(in_window)

    if in_window:
        aud.first_candle_time = min(c.open_time for c in in_window).isoformat()
        aud.last_candle_time = max(c.open_time for c in in_window).isoformat()

    seen: set[str] = set()
    grid: set[str] = set()
    t = start
    while t < end:
        grid.add(t.isoformat())
        t = _add_minutes(t, EXPECTED_INTERVAL_MINUTES)
    for c in in_window:
        iso = c.open_time.isoformat()
        if iso in seen:
            aud.duplicate_count += 1
        seen.add(iso)

    present = {c.open_time.isoformat() for c in in_window}
    aud.gap_count = len(grid - present)
    aud.coverage_pct = round(100.0 * len(present) / len(grid), 2) if grid else 0.0

    # fingerprint over sorted OHLCV — deterministic, wall-clock independent
    h = hashlib.sha256()
    for c in sorted(in_window, key=lambda c: c.open_time):
        h.update(
            b"|".join(
                (
                    c.open_time.isoformat().encode(),
                    repr(c.open).encode(),
                    repr(c.high).encode(),
                    repr(c.low).encode(),
                    repr(c.close).encode(),
                    repr(c.volume).encode(),
                )
            )
        )
    aud.fingerprint = h.hexdigest()

    aud.manifest_json = {
        "run_id": run_id,
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        "expected_candles": aud.expected_count,
        "actual_candles": aud.actual_count,
        "coverage_pct": aud.coverage_pct,
        "gaps": aud.gap_count,
        "duplicates": aud.duplicate_count,
        "out_of_window_rows": aud.out_of_window,
        "first_candle_time": aud.first_candle_time,
        "last_candle_time": aud.last_candle_time,
        "complete": aud.complete,
        "fingerprint": aud.fingerprint,
        "granularity_minutes": GRANULARITY_MINUTES,
    }
    return aud


def _add_minutes(t: datetime, minutes: int) -> datetime:
    """Add minutes without importing timedelta at module import time."""
    from datetime import timedelta

    return t + timedelta(minutes=minutes)


def load_manifest(path: str) -> dict[str, Any]:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"manifest {path!r} is not a JSON object")
    return data
