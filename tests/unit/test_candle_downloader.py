"""Tests for Bitget historical candle downloader functionality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.models import Candle

FIXED_NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


def _make_candle(
    symbol: str = "XAUUSDT_UMCBL",
    granularity: str = "5m",
    open_time: datetime | None = None,
    open_: float = 3500.0,
    high: float = 3510.0,
    low: float = 3490.0,
    close: float = 3505.0,
    volume: float = 100.0,
    quote_volume: float = 350000.0,
) -> Candle:
    return Candle(
        symbol=symbol,
        granularity=granularity,
        open_time=open_time or FIXED_NOW,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=quote_volume,
    )


class TestValidateGranularity:
    def test_valid_granularities(self) -> None:
        for g in ["1m", "5m", "15m", "30m", "1H", "4H", "1D"]:
            BitgetPublicClient._validate_granularity(g)  # no raise

    def test_invalid_granularity_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported granularity"):
            BitgetPublicClient._validate_granularity("2m")

    def test_empty_granularity_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported granularity"):
            BitgetPublicClient._validate_granularity("")


class TestParseCandle:
    def test_basic_parse(self) -> None:
        row = [
            "1740000000000",
            "3500.0",
            "3510.0",
            "3490.0",
            "3505.0",
            "100.0",
            "350000.0",
        ]
        candle = BitgetPublicClient._parse_candle("XAUUSDT_UMCBL", "5m", row)
        assert candle.symbol == "XAUUSDT_UMCBL"
        assert candle.granularity == "5m"
        assert candle.open_time == datetime(2025, 2, 19, 21, 20, tzinfo=UTC)
        assert candle.open == 3500.0
        assert candle.high == 3510.0
        assert candle.low == 3490.0
        assert candle.close == 3505.0
        assert candle.volume == 100.0
        assert candle.quote_volume == 350000.0

    def test_parse_without_quote_volume(self) -> None:
        row = ["1740000000000", "3500.0", "3510.0", "3490.0", "3505.0", "100.0"]
        candle = BitgetPublicClient._parse_candle("XAUUSDT_UMCBL", "5m", row)
        assert candle.quote_volume == 0.0

    def test_open_time_is_utc(self) -> None:
        row = ["1740000000000", "3500.0", "3510.0", "3490.0", "3505.0", "100.0"]
        candle = BitgetPublicClient._parse_candle("XAUUSDT_UMCBL", "5m", row)
        assert candle.open_time.tzinfo is UTC


class TestGetHistoryCandles:
    async def test_basic_response(self) -> None:
        rows = [
            ["1740000000000", "3500.0", "3510.0", "3490.0", "3505.0", "100.0", "350000.0"],
            ["1740000300000", "3505.0", "3520.0", "3500.0", "3515.0", "120.0", "421800.0"],
        ]
        resp = {"code": "00000", "msg": "success", "data": rows}
        async with BitgetPublicClient() as c:
            with patch.object(c._client, "request", new_callable=AsyncMock) as m:
                import httpx

                m.return_value = httpx.Response(200, json=resp)
                candles = await c.get_history_candles("XAUUSDT_UMCBL", "5m", limit=200)
                assert len(candles) == 2
                assert candles[0].open == 3500.0
                assert candles[1].open == 3505.0

    async def test_empty_data_returns_empty(self) -> None:
        resp = {"code": "00000", "msg": "success", "data": []}
        async with BitgetPublicClient() as c:
            with patch.object(c._client, "request", new_callable=AsyncMock) as m:
                import httpx

                m.return_value = httpx.Response(200, json=resp)
                candles = await c.get_history_candles("XAUUSDT_UMCBL", "5m")
                assert candles == []

    async def test_invalid_granularity(self) -> None:
        async with BitgetPublicClient() as c:
            with pytest.raises(ValueError, match="Unsupported granularity"):
                await c.get_history_candles("XAUUSDT_UMCBL", "2m")

    async def test_limit_clamped_to_200(self) -> None:
        rows = [["1740000000000", "3500.0", "3510.0", "3490.0", "3505.0", "100.0", "350000.0"]]
        resp = {"code": "00000", "msg": "success", "data": rows}
        async with BitgetPublicClient() as c:
            with patch.object(c._client, "request", new_callable=AsyncMock) as m:
                import httpx

                m.return_value = httpx.Response(200, json=resp)
                await c.get_history_candles("XAUUSDT_UMCBL", "5m", limit=999)
                m.assert_called_once()
                _, kwargs = m.call_args
                assert kwargs["params"]["limit"] == 200

    async def test_api_error_raises(self) -> None:
        resp = {"code": "40001", "msg": "invalid symbol"}
        async with BitgetPublicClient() as c:
            with patch.object(c._client, "request", new_callable=AsyncMock) as m:
                import httpx

                m.return_value = httpx.Response(200, json=resp)
                with pytest.raises(Exception, match="API error code=40001"):
                    await c.get_history_candles("XAUUSDT_UMCBL", "5m")


class TestCandleSorting:
    def test_sort_ascending_by_open_time(self) -> None:
        t3 = datetime(2026, 7, 14, 12, 15, tzinfo=UTC)
        t1 = datetime(2026, 7, 14, 12, 5, tzinfo=UTC)
        t2 = datetime(2026, 7, 14, 12, 10, tzinfo=UTC)
        candles = [_make_candle(open_time=t) for t in [t3, t1, t2]]
        assert sorted(candles, key=lambda c: c.open_time) == [
            _make_candle(open_time=t1),
            _make_candle(open_time=t2),
            _make_candle(open_time=t3),
        ]


class TestCandleDeduplication:
    def test_deduplicate_by_open_time(self) -> None:
        t = datetime(2026, 7, 14, 12, 5, tzinfo=UTC)
        dup = [_make_candle(open_time=t), _make_candle(open_time=t)]
        seen = set()
        unique = []
        for c in dup:
            key = (c.symbol, c.granularity, c.open_time)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        assert len(unique) == 1


class TestGapDetection:
    def test_missing_interval_detected(self) -> None:
        t1 = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
        t2 = datetime(2026, 7, 14, 12, 20, tzinfo=UTC)  # gap of 20m for 5m granularity
        candles = [_make_candle(open_time=t1), _make_candle(open_time=t2)]
        # Sort to ensure detection works on sorted list
        candles = sorted(candles, key=lambda c: c.open_time)
        interval = timedelta(minutes=5)
        gaps = []
        for i in range(1, len(candles)):
            diff = candles[i].open_time - candles[i - 1].open_time
            if diff > interval:
                gaps.append((candles[i - 1].open_time, candles[i].open_time))
        assert len(gaps) == 1
        assert gaps[0] == (t1, t2)
