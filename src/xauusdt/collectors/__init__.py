"""Historical backfill for Bitget XAUUSDT candles.

Downloads historical OHLCV candles via BitgetPublicClient,
detects gaps in the timeline, and persists through CandleRepository
with idempotent upsert. Supports dry-run mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.models import Candle
from xauusdt.storage.candle_repository import CandleRepository

BACKFILL_SYMBOL = "XAU-USDT-SWAP"
BACKFILL_GRANULARITIES = {"5m", "15m", "1H", "4H"}
GRANULARITY_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}
MAX_PAGE_CANDLES = 200


@dataclass
class BackfillGap:
    """A single missing candle interval in the timeline."""

    missing_open_time: datetime


@dataclass
class BackfillResult:
    """Structured summary of a backfill run."""

    symbol: str
    granularity: str
    start_time: datetime
    end_time: datetime
    downloaded_count: int = 0
    stored_count: int = 0
    gap_count: int = 0
    gaps: list[BackfillGap] = field(default_factory=list)
    dry_run: bool = False
    status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON logging / API response."""
        d = {
            "symbol": self.symbol,
            "granularity": self.granularity,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "downloaded_count": self.downloaded_count,
            "stored_count": self.stored_count,
            "gap_count": self.gap_count,
            "dry_run": self.dry_run,
            "status": self.status,
        }
        if self.gaps:
            d["gaps"] = [{"missing_open_time": g.missing_open_time.isoformat()} for g in self.gaps]
        return d


def _validate_granularity(granularity: str) -> None:
    """Raise ValueError if granularity is not in the allowed set for backfill."""
    if granularity not in BACKFILL_GRANULARITIES:
        raise ValueError(
            f"Unsupported backfill granularity {granularity!r}. Allowed: {sorted(BACKFILL_GRANULARITIES)}"
        )


def _expected_candle_count(start_time: datetime, end_time: datetime, granularity: str) -> int:
    """Return the number of candle intervals between start and end (inclusive of start, exclusive of end)."""
    seconds = GRANULARITY_SECONDS[granularity]
    delta = (end_time - start_time).total_seconds()
    count = int(delta / seconds)
    return max(count, 0)


def _detect_gaps(
    candles: list[Candle], start_time: datetime, end_time: datetime, granularity: str
) -> list[BackfillGap]:
    """Detect missing candle intervals in a sorted candle list."""
    if not candles:
        return [
            BackfillGap(missing_open_time=next_time(start_time, granularity, i))
            for i in range(_expected_candle_count(start_time, end_time, granularity))
        ]

    expected_set: set[datetime] = set()
    total = _expected_candle_count(start_time, end_time, granularity)
    for i in range(total):
        expected_set.add(next_time(start_time, granularity, i))

    actual_set: set[datetime] = set()
    for c in candles:
        if c.open_time >= start_time and c.open_time < end_time:
            actual_set.add(c.open_time)

    missing_times = sorted(expected_set - actual_set)
    return [BackfillGap(missing_open_time=t) for t in missing_times]


def next_time(base: datetime, granularity: str, n: int) -> datetime:
    """Return the open_time of the n-th candle interval after base."""
    seconds = GRANULARITY_SECONDS[granularity]
    return base + timedelta(seconds=seconds * n)


class HistoricalBackfillService:
    """Orchestrates downloading and persisting historical candles.

    Separated from BitgetPublicClient and CandleRepository - handles
    pagination, gap detection, and structured result reporting.
    """

    def __init__(self, client: BitgetPublicClient, repository: CandleRepository) -> None:
        self._client = client
        self._repository = repository

    async def run(
        self,
        granularity: str,
        start_time: datetime,
        end_time: datetime,
        dry_run: bool = False,
    ) -> BackfillResult:
        """Execute a single-granularity backfill.

        Args:
            granularity: Candle size (5m, 15m, 1H, 4H).
            start_time: Inclusive UTC start bound.
            end_time: Exclusive UTC end bound.
            dry_run: If True, download and validate only (no persistence).

        Returns:
            BackfillResult with download stats, gaps, and status.
        """
        _validate_granularity(granularity)
        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")

        result = BackfillResult(
            symbol=BACKFILL_SYMBOL,
            granularity=granularity,
            start_time=start_time,
            end_time=end_time,
            dry_run=dry_run,
        )

        all_candles = await self._download_all(granularity, start_time, end_time)
        result.downloaded_count = len(all_candles)

        gaps = _detect_gaps(all_candles, start_time, end_time, granularity)
        result.gap_count = len(gaps)
        result.gaps = gaps

        if gaps:
            result.status = "completed_with_gaps"
        else:
            result.status = "success"

        if not dry_run and all_candles:
            result.stored_count = await self._repository.upsert_many(all_candles)
        elif dry_run:
            result.stored_count = 0

        return result

    async def _download_all(
        self,
        granularity: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Candle]:
        """Paginate through Bitget API to download all candles for the range."""
        all_candles: list[Candle] = []
        current_start = start_time

        while current_start < end_time:
            chunk_end = min(current_start + timedelta(hours=24), end_time)
            page = await self._client.get_history_candles(
                symbol=BACKFILL_SYMBOL,
                granularity=granularity,
                limit=MAX_PAGE_CANDLES,
                start_time=current_start,
                end_time=chunk_end,
            )
            if not page:
                break
            all_candles.extend(page)

            # Advance start cursor past the latest candle we received
            if page:
                latest = max(c.open_time for c in page)
                next_ts = next_time(latest, granularity, 1)
                if next_ts <= current_start:
                    # API returned stale data; break to avoid infinite loop
                    break
                current_start = next_ts

            # Safety: don't download more than expected
            expected = _expected_candle_count(start_time, end_time, granularity)
            if len(all_candles) >= expected:
                break

        return sorted(all_candles, key=lambda c: c.open_time)
