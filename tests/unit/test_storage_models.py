"""Tests for candle storage models."""

from datetime import UTC, datetime

from xauusdt.exchange.models import Candle
from xauusdt.storage.models import CandleOrm


class TestCandleOrmFromCandle:
    def test_basic_conversion(self) -> None:
        candle = Candle(
            symbol="XAUUSDT_UMCBL",
            granularity="5m",
            open_time=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
            open=3500.0,
            high=3510.0,
            low=3490.0,
            close=3505.0,
            volume=100.0,
            quote_volume=350000.0,
        )
        orm = CandleOrm.from_candle(candle)
        assert orm.symbol == "XAUUSDT_UMCBL"
        assert orm.granularity == "5m"
        assert orm.open_time == candle.open_time
        assert orm.close_time == candle.close_time
        assert orm.open_price == 3500.0
        assert orm.high == 3510.0
        assert orm.low == 3490.0
        assert orm.close == 3505.0
        assert orm.volume == 100.0
        assert orm.quote_volume == 350000.0

    def test_defaults(self) -> None:
        candle = Candle(
            symbol="XAUUSDT_UMCBL",
            granularity="1H",
            open_time=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
            open=3500.0,
            high=3510.0,
            low=3490.0,
            close=3505.0,
            volume=100.0,
        )
        orm = CandleOrm.from_candle(candle)
        assert orm.quote_volume == 0.0
