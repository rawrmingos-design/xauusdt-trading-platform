"""Tests for historical backfill service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from xauusdt.collectors import (
    BACKFILL_SYMBOL,
    BackfillGap,
    BackfillResult,
    HistoricalBackfillService,
    _detect_gaps,
    _expected_candle_count,
    _validate_granularity,
    next_time,
)
from xauusdt.exchange.models import Candle


def _make_candle(open_time: datetime | None = None) -> Candle:
    return Candle(
        symbol=BACKFILL_SYMBOL,
        granularity="15m",
        open_time=open_time or datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        open=3500.0,
        high=3510.0,
        low=3490.0,
        close=3505.0,
        volume=100.0,
        quote_volume=350500.0,
    )


def test_expected_candle_count_15m() -> None:
    """One hour of 15m candles = 4 intervals."""
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
    assert _expected_candle_count(start, end, "15m") == 4


def test_expected_candle_count_1h() -> None:
    """24 hours of 1H candles = 24 intervals."""
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)
    assert _expected_candle_count(start, end, "1H") == 24


def test_expected_candle_count_zero() -> None:
    """Same start and end = 0 intervals."""
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    assert _expected_candle_count(start, start, "5m") == 0


def test_next_time() -> None:
    """n=0 returns base, n=1 adds one granularity interval."""
    base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    assert next_time(base, "15m", 0) == base
    assert next_time(base, "15m", 1) == datetime(2025, 1, 1, 0, 15, tzinfo=UTC)
    assert next_time(base, "1H", 2) == datetime(2025, 1, 1, 2, 0, tzinfo=UTC)


class TestDetectGaps:
    """Tests for gap detection logic."""

    def test_no_gaps_continuous_candles(self) -> None:
        candles = [
            _make_candle(next_time(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), "15m", i))
            for i in range(4)
        ]
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        gaps = _detect_gaps(candles, start, end, "15m")
        assert gaps == []

    def test_gap_in_middle(self) -> None:
        """Missing candle at open_time 00:30."""
        candles = [
            _make_candle(next_time(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), "15m", 0)),
            _make_candle(next_time(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), "15m", 1)),
            # 00:30 is missing
            _make_candle(next_time(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), "15m", 3)),
        ]
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        gaps = _detect_gaps(candles, start, end, "15m")
        assert len(gaps) == 1
        assert gaps[0].missing_open_time == datetime(2025, 1, 1, 0, 30, tzinfo=UTC)

    def test_all_gaps_no_candles(self) -> None:
        candles: list[Candle] = []
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        gaps = _detect_gaps(candles, start, end, "15m")
        assert len(gaps) == 4

    def test_candles_outside_range_ignored(self) -> None:
        """Candle with open_time outside range should not count."""
        early = _make_candle(datetime(2024, 12, 31, 23, 0, tzinfo=UTC))
        candles = [early]
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        gaps = _detect_gaps(candles, start, end, "15m")
        assert len(gaps) == 4

    def test_candle_at_end_excluded(self) -> None:
        """Candle at exact end_time is excluded (range is [start, end))."""
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        end_candle = _make_candle(end)
        candles = [end_candle]
        gaps = _detect_gaps(candles, datetime(2025, 1, 1, 0, 0, tzinfo=UTC), end, "15m")
        assert len(gaps) == 4  # end candle is outside [start, end)


class TestValidateGranularity:
    """Tests for granularity validation."""

    @pytest.mark.parametrize("gran", ["5m", "15m", "1H", "4H"])
    def test_valid_granularities(self, gran: str) -> None:
        _validate_granularity(gran)  # Should not raise

    def test_invalid_granularity(self) -> None:
        with pytest.raises(ValueError, match="Unsupported backfill granularity"):
            _validate_granularity("30m")

    def test_completely_invalid_granularity(self) -> None:
        with pytest.raises(ValueError, match="Unsupported backfill granularity"):
            _validate_granularity("1W")


class TestBackfillResult:
    """Tests for BackfillResult serialization."""

    def test_to_dict_success(self) -> None:
        result = BackfillResult(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
            downloaded_count=96,
            stored_count=96,
            gap_count=0,
            dry_run=False,
            status="success",
        )
        d = result.to_dict()
        assert d["symbol"] == "XAU-USDT-SWAP"
        assert d["status"] == "success"
        assert d["downloaded_count"] == 96
        assert "gaps" not in d

    def test_to_dict_with_gaps(self) -> None:
        gaps = [BackfillGap(missing_open_time=datetime(2025, 1, 1, 8, 15, tzinfo=UTC))]
        result = BackfillResult(
            symbol="XAU-USDT-SWAP",
            granularity="15m",
            start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 1, 2, 0, 0, tzinfo=UTC),
            downloaded_count=48,
            stored_count=48,
            gap_count=2,
            gaps=gaps,
            status="completed_with_gaps",
        )
        d = result.to_dict()
        assert d["status"] == "completed_with_gaps"
        assert d["gap_count"] == 2
        assert "gaps" in d
        assert len(d["gaps"]) == 1
        assert d["gaps"][0]["missing_open_time"] == "2025-01-01T08:15:00+00:00"

    def test_to_dict_dry_run(self) -> None:
        result = BackfillResult(
            symbol="XAU-USDT-SWAP",
            granularity="5m",
            start_time=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end_time=datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
            dry_run=True,
        )
        assert result.to_dict()["dry_run"] is True


@pytest.fixture()
def mock_repository() -> MagicMock:
    repo = MagicMock()
    repo.upsert_many = AsyncMock(return_value=96)
    return repo


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    return client


class TestHistoricalBackfillService:
    """Tests for HistoricalBackfillService integration."""

    @pytest.mark.asyncio
    async def test_successful_backfill_no_gaps(self, mock_repository: MagicMock) -> None:
        """Backfill downloads candles with no gaps — status success."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        candles = [_make_candle(next_time(start, "15m", i)) for i in range(4)]

        mock_repository.upsert_many = AsyncMock(return_value=len(candles))
        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=candles)

        service = HistoricalBackfillService(mock_client, mock_repository)
        result = await service.run("15m", start, end)

        assert result.status == "success"
        assert result.downloaded_count == 4
        assert result.gap_count == 0
        assert result.stored_count == 4
        assert not result.dry_run
        mock_repository.upsert_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_with_gaps(self, mock_repository: MagicMock) -> None:
        """Backfill downloads candles with gaps — status completed_with_gaps."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        # Only download candles at 00:00 and 00:30 (skip 00:15 and 00:45)
        partial_candles = [
            _make_candle(next_time(start, "15m", 0)),
            _make_candle(next_time(start, "15m", 2)),
        ]

        mock_repository.upsert_many = AsyncMock(return_value=len(partial_candles))
        mock_client = MagicMock()

        # Mock returns partial candles only on first call, then empty to stop pagination
        call_count = [0]

        async def mock_get_history(*args: Any, **kwargs: Any) -> list[Candle]:
            call_count[0] += 1
            if call_count[0] == 1:
                return partial_candles
            return []

        mock_client.get_history_candles = mock_get_history

        service = HistoricalBackfillService(mock_client, mock_repository)
        result = await service.run("15m", start, end)

        assert result.status == "completed_with_gaps"
        assert result.downloaded_count == 2
        assert result.gap_count == 2
        assert result.stored_count == 2

    @pytest.mark.asyncio
    async def test_dry_run_no_persistence(self, mock_repository: MagicMock) -> None:
        """Dry-run downloads candles but does NOT persist to repository."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        candles = [_make_candle(next_time(start, "15m", i)) for i in range(4)]

        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=candles)

        service = HistoricalBackfillService(mock_client, mock_repository)
        result = await service.run("15m", start, end, dry_run=True)

        assert result.status == "success"
        assert result.downloaded_count == 4
        assert result.stored_count == 0
        assert result.dry_run is True
        mock_repository.upsert_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_granularity_raises(self, mock_repository: MagicMock) -> None:
        """Invalid granularity raises ValueError before any API call."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)

        mock_client = MagicMock()

        service = HistoricalBackfillService(mock_client, mock_repository)
        with pytest.raises(ValueError, match="Unsupported backfill granularity"):
            await service.run("1W", start, end)

        mock_client.get_history_candles.assert_not_called()

    @pytest.mark.asyncio
    async def test_end_before_start_raises(self, mock_repository: MagicMock) -> None:
        """End time before start time raises ValueError."""
        start = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)

        mock_client = MagicMock()
        service = HistoricalBackfillService(mock_client, mock_repository)
        with pytest.raises(ValueError, match="end_time must be after start_time"):
            await service.run("15m", start, end)

    @pytest.mark.asyncio
    async def test_empty_api_result(self, mock_repository: MagicMock) -> None:
        """Bitget returns empty list — all intervals reported as gaps."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)

        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=[])

        service = HistoricalBackfillService(mock_client, mock_repository)
        result = await service.run("15m", start, end)

        assert result.status == "completed_with_gaps"
        assert result.downloaded_count == 0
        assert result.gap_count == 4  # 4 intervals for 1H at 15m

    @pytest.mark.asyncio
    async def test_multiple_granularities(self, mock_repository: MagicMock) -> None:
        """Running backfill for multiple granularities works independently."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)

        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=[])

        service = HistoricalBackfillService(mock_client, mock_repository)

        # 5m
        result_5m = await service.run("5m", start, end)
        assert result_5m.granularity == "5m"

        # 1H
        mock_client.get_history_candles = AsyncMock(return_value=[])
        result_1h = await service.run("1H", start, end)
        assert result_1h.granularity == "1H"

    @pytest.mark.asyncio
    async def test_idempotent_rerun(self, mock_repository: MagicMock) -> None:
        """Rerunning backfill calls upsert_many again (idempotency delegated to repo)."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 1, 0, tzinfo=UTC)
        candles = [_make_candle(next_time(start, "15m", i)) for i in range(4)]

        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=candles)

        service = HistoricalBackfillService(mock_client, mock_repository)

        # First run
        await service.run("15m", start, end)
        assert mock_repository.upsert_many.call_count == 1

        # Second run — same candles
        await service.run("15m", start, end)
        assert mock_repository.upsert_many.call_count == 2

    @pytest.mark.asyncio
    async def test_result_includes_all_fields(self, mock_repository: MagicMock) -> None:
        """Result contains symbol, granularity, start/end time, and all counts."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 6, 0, tzinfo=UTC)
        candles = [_make_candle(next_time(start, "1H", i)) for i in range(6)]

        mock_repository.upsert_many = AsyncMock(return_value=len(candles))
        mock_client = MagicMock()
        mock_client.get_history_candles = AsyncMock(return_value=candles)

        service = HistoricalBackfillService(mock_client, mock_repository)
        result = await service.run("1H", start, end)

        assert result.symbol == BACKFILL_SYMBOL
        assert result.granularity == "1H"
        assert result.start_time == start
        assert result.end_time == end
        assert result.downloaded_count == 6
        assert result.stored_count == 6
        assert result.gap_count == 0
