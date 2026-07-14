"""Tests for LiveCandleCollector — finalization logic, in-progress tracking, and deduplication."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from xauusdt.collectors.live_candles import LiveCandleCollector
from xauusdt.exchange.models import Candle


def _make_candle(
    symbol: str = "XAUUSDT_UMCBL",
    granularity: str = "5m",
    hour: int = 14,
    minute: int = 0,
) -> Candle:
    return Candle(
        symbol=symbol,
        granularity=granularity,
        open_time=datetime(2024, 6, 15, hour, minute, tzinfo=UTC),
        open=3500.0,
        high=3510.0,
        low=3490.0,
        close=3505.0,
        volume=100.0,
        quote_volume=350500.0,
    )


def _make_ws_message(action: str, granularity: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a raw WebSocket message dict for candle events."""
    return {
        "arg": {"channel": granularity, "instId": "XAUUSDT_UMCBL", "period": granularity},
        "action": action,
        "data": [data],
    }


def _ts(hour: int, minute: int) -> str:
    """Convert hour:minute on 2024-06-15 to milliseconds timestamp."""
    # Base: 2024-06-15 00:00:00 UTC in milliseconds
    base = 1718409600000  # 2024-06-15T00:00:00Z
    minutes_offset = hour * 60 + minute
    return str(base + minutes_offset * 60000)


class TestLiveCandleCollectorFinalization:
    """Test that candles are finalized only when a newer interval begins."""

    @pytest.mark.asyncio
    async def test_snapshot_stores_in_progress(self) -> None:
        """A snapshot message stores a candle as in-progress, not finalized."""
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_repo.upsert_many = AsyncMock(return_value=0)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        ws_msg = _make_ws_message(
            "snapshot",
            "5m",
            {
                "ts": _ts(14, 0),
                "ow": "1950.50",
                "h": "1955.00",
                "l": "1948.00",
                "c": "1953.20",
                "vol": "1200.5",
                "volQuote": "2340000.00",
            },
        )
        await collector._on_message("candle_snapshot", "5m", ws_msg["data"][0])

        assert len(collector._in_progress) == 1
        assert mock_repo.upsert_many.call_count == 0

    @pytest.mark.asyncio
    async def test_update_updates_in_progress(self) -> None:
        """A subsequent update for the same interval updates in-progress candle."""
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_repo.upsert_many = AsyncMock(return_value=0)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        msg1 = _make_ws_message(
            "snapshot",
            "5m",
            {
                "ts": _ts(14, 0),
                "ow": "1950.50",
                "h": "1955.00",
                "l": "1948.00",
                "c": "1953.20",
                "vol": "1200",
                "volQuote": "2340000",
            },
        )
        msg2 = _make_ws_message(
            "update",
            "5m",
            {
                "ts": _ts(14, 0),
                "ow": "1950.50",
                "h": "1960.00",
                "l": "1947.00",
                "c": "1958.00",
                "vol": "1300",
                "volQuote": "2500000",
            },
        )
        await collector._on_message("candle_snapshot", "5m", msg1["data"][0])
        await collector._on_message("candle_update", "5m", msg2["data"][0])

        assert len(collector._in_progress) == 1
        candle = list(collector._in_progress.values())[0].candle
        assert candle.high == 1960.0
        assert candle.close == 1958.0
        assert mock_repo.upsert_many.call_count == 0

    @pytest.mark.asyncio
    async def test_new_interval_finalizes_previous(self) -> None:
        """When a new interval starts, the previous one is persisted."""
        mock_client = MagicMock()
        mock_repo = MagicMock()
        persisted: list[list[Candle]] = []

        async def _track(candles: list[Candle]) -> int:
            persisted.append(candles)
            return len(candles)

        mock_repo.upsert_many = AsyncMock(side_effect=_track)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        # First candle snapshot for 14:00 interval
        msg_1400 = _make_ws_message(
            "snapshot",
            "5m",
            {
                "ts": _ts(14, 0),
                "ow": "1950.50",
                "h": "1955.00",
                "l": "1948.00",
                "c": "1953.20",
                "vol": "100",
                "volQuote": "350000",
            },
        )
        # Second candle snapshot for 14:05 interval (triggers finalize of 14:00)
        msg_1405 = _make_ws_message(
            "snapshot",
            "5m",
            {
                "ts": _ts(14, 5),
                "ow": "1953.20",
                "h": "1958.00",
                "l": "1951.00",
                "c": "1956.00",
                "vol": "110",
                "volQuote": "370000",
            },
        )
        await collector._on_message("candle_snapshot", "5m", msg_1400["data"][0])
        await collector._on_message("candle_update", "5m", msg_1405["data"][0])

        # First candle should be persisted, second in-progress
        assert mock_repo.upsert_many.call_count == 1
        finalized = persisted[0]
        assert len(finalized) == 1
        assert finalized[0].open_time == datetime(2024, 6, 15, 14, 0, tzinfo=UTC)
        assert len(collector._in_progress) == 1

    @pytest.mark.asyncio
    async def test_no_finalize_on_same_interval(self) -> None:
        """Multiple updates for the same interval should not trigger finalize."""
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_repo.upsert_many = AsyncMock(return_value=0)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        for i in range(5):
            msg = _make_ws_message(
                "update",
                "5m",
                {
                    "ts": _ts(14, 0),
                    "ow": "1950.50",
                    "h": f"{1950 + i}",
                    "l": "1948.00",
                    "c": f"{1950 + i}",
                    "vol": "100",
                    "volQuote": "350000",
                },
            )
            await collector._on_message("candle_update", "5m", msg["data"][0])

        assert mock_repo.upsert_many.call_count == 0


class TestNormalizeCandle:
    """Test candle normalization from raw WebSocket data."""

    def test_normalizes_valid_payload(self) -> None:
        raw = {
            "ts": _ts(14, 0),
            "ow": "1950.50",
            "h": "1955.00",
            "l": "1948.00",
            "c": "1953.20",
            "vol": "1200.5",
            "volQuote": "2340000.00",
        }
        candle = LiveCandleCollector._normalize_candle(raw, "5m")
        assert candle is not None
        assert candle.symbol == "XAUUSDT_UMCBL"
        assert candle.granularity == "5m"
        assert candle.open_time.tzinfo is not None
        assert candle.open == 1950.5
        assert candle.high == 1955.0
        assert candle.low == 1948.0
        assert candle.close == 1953.2
        assert candle.volume == 1200.5
        assert candle.quote_volume == 2340000.0

    def test_returns_none_on_missing_ts(self) -> None:
        raw: dict[str, Any] = {"ow": "1950.50", "h": "1955.00"}
        candle = LiveCandleCollector._normalize_candle(raw, "5m")
        assert candle is None

    def test_returns_none_on_invalid_ts(self) -> None:
        raw = {"ts": "not-a-number", "ow": "1950.50"}
        candle = LiveCandleCollector._normalize_candle(raw, "15m")
        assert candle is None


class TestPruning:
    """Test in-progress candle pruning."""

    @pytest.mark.asyncio
    async def test_prunes_excess_in_progress(self) -> None:
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_repo.upsert_many = AsyncMock(return_value=0)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        # Simulate snapshots for 5 different 5m intervals
        for i in range(5):
            minute = 10 + i * 5
            msg = _make_ws_message(
                "snapshot",
                "5m",
                {
                    "ts": _ts(14, minute),
                    "ow": "1950.50",
                    "h": "1955.00",
                    "l": "1948.00",
                    "c": "1953.20",
                    "vol": "100",
                    "volQuote": "350000",
                },
            )
            await collector._on_message("candle_update", "5m", msg["data"][0])

        # Pruning should keep only MAX_IN_PROGRESS (2) most recent
        assert len(collector._in_progress) <= 2


class TestIdempotency:
    """Test that duplicate finalization does not create duplicates."""

    @pytest.mark.asyncio
    async def test_duplicate_candle_updates_not_duplicated_in_storage(self) -> None:
        mock_client = MagicMock()
        mock_repo = MagicMock()
        persisted: list[list[Candle]] = []

        async def _track(candles: list[Candle]) -> int:
            persisted.append(candles)
            return len(candles)

        mock_repo.upsert_many = AsyncMock(side_effect=_track)

        collector = LiveCandleCollector(client=mock_client, repository=mock_repo)

        # Multiple updates to 14:00 candle, then 14:05 arrives
        for i in range(3):
            msg = _make_ws_message(
                "update",
                "5m",
                {
                    "ts": _ts(14, 0),
                    "ow": "1950.50",
                    "h": f"{1950 + i}",
                    "l": "1948.00",
                    "c": f"{1950 + i}",
                    "vol": "100",
                    "volQuote": "350000",
                },
            )
            await collector._on_message("candle_update", "5m", msg["data"][0])

        msg_next = _make_ws_message(
            "update",
            "5m",
            {
                "ts": _ts(14, 5),
                "ow": "1955.00",
                "h": "1960.00",
                "l": "1953.00",
                "c": "1958.00",
                "vol": "110",
                "volQuote": "370000",
            },
        )
        await collector._on_message("candle_update", "5m", msg_next["data"][0])

        # Only ONE batch should be persisted with ONE candle (deduplicated)
        assert mock_repo.upsert_many.call_count == 1
        assert len(persisted[0]) == 1
