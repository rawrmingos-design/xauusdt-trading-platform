"""Historical backfill for OKX XAU-USDT-SWAP candles.

Downloads historical OHLCV candles via OKXClient,
detects gaps in the timeline, and persists through CandleRepository
with idempotent upsert. Supports dry-run mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from xauusdt.exchange.models import Candle
from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository

OKX_BACKFILL_SYMBOL = "XAU-USDT-SWAP"
OKX_BACKFILL_GRANULARITIES = {"5m", "15m", "1H", "4H"}
OKX_MAX_PAGE_CANDLES = 100


@dataclass
class OKXBackfillResult:
    """Structured summary of an OKX backfill run."""

    symbol: str
    granularity: str
    start_time: datetime
    end_time: datetime
    downloaded_count: int = 0
    stored_count: int = 0
    gap_count: int = 0
    dry_run: bool = False
    status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "exchange": "okx",
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
        return d


def _okx_validate_granularity(granularity: str) -> None:
    if granularity not in OKX_BACKFILL_GRANULARITIES:
        raise ValueError(
            f"Unsupported OKX backfill granularity {granularity!r}. "
            f"Allowed: {sorted(OKX_BACKFILL_GRANULARITIES)}"
        )


def _get_granularity_seconds(granularity: str) -> int:
    return {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400}.get(granularity, 900)


async def _download_okx_all(
    client: OKXClient,
    repository: CandleRepository,
    granularity: str,
    start_time: datetime,
    end_time: datetime,
    dry_run: bool = False,
) -> OKXBackfillResult:
    """Download and store OKX candles for the given range.

    OKX API returns candles in descending order (newest first).
    Pagination: use `after` (timestamp threshold) to fetch candles OLDER than threshold.
    We start from end_time and paginate backward.
    """
    _okx_validate_granularity(granularity)
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")

    seconds = _get_granularity_seconds(granularity)
    expected_count = int((end_time - start_time).total_seconds() / seconds)

    result = OKXBackfillResult(
        symbol=OKX_BACKFILL_SYMBOL,
        granularity=granularity,
        start_time=start_time,
        end_time=end_time,
        dry_run=dry_run,
    )

    # OKX pagination: after = fetch candles OLDER than this timestamp
    # Start from end_time, paginate backward
    cursor = end_time
    all_candles: list[Candle] = []
    seen_keys: set[str] = set()

    while cursor > start_time:
        candles = await client.fetch_candles(
            symbol=OKX_BACKFILL_SYMBOL,
            granularity=granularity,
            start_time=cursor,  # OKX 'after' = oldest allowed
            limit=OKX_MAX_PAGE_CANDLES,
        )

        if not candles:
            break

        # Filter within range and deduplicate
        for c in candles:
            if c.open_time < start_time:
                continue
            if c.open_time >= cursor:
                continue  # skip candles already fetched
            key = c.open_time.isoformat()
            if key not in seen_keys:
                seen_keys.add(key)
                all_candles.append(c)

        # OKX returns newest first. The last candle is the oldest in this batch.
        if candles:
            oldest_in_batch = min(c.open_time for c in candles)
            if oldest_in_batch >= cursor:
                break  # no progress, stop
            cursor = oldest_in_batch
        else:
            break

        # Safety
        if len(all_candles) >= expected_count * 2:
            break

        # Respect OKX rate limits (20 req / 2s for public endpoints)
        await asyncio.sleep(0.15)

    # Sort ascending (oldest first)
    all_candles.sort(key=lambda c: c.open_time)
    result.downloaded_count = len(all_candles)

    # Gap detection
    expected_times: set[str] = set()
    current = start_time
    while current < end_time:
        expected_times.add(current.isoformat())
        current += timedelta(seconds=seconds)

    actual_times: set[str] = {c.open_time.isoformat() for c in all_candles}
    missing = expected_times - actual_times
    result.gap_count = len(missing)

    if result.gap_count > 0:
        result.status = "completed_with_gaps"
    else:
        result.status = "success"

    if not dry_run and all_candles:
        result.stored_count = await repository.upsert_many(all_candles)

    return result
