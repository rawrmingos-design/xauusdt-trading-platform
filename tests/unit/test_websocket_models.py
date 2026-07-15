"""Tests for WebSocket models and Bitget candlestick message parsing."""

from __future__ import annotations

from datetime import UTC, datetime

from xauusdt.exchange.websocket_models import (
    WsCandleStickSnapshot,
    WsCandleStickUpdate,
    WsPingMessage,
    WsSubscribeMessage,
)


class TestWsPingMessage:
    """Test ping message serialization."""

    def test_creates_ping(self) -> None:
        msg = WsPingMessage(arg={"instType": "USDT-FUTURE"})
        assert msg.type == "ping"
        assert msg.arg["instType"] == "USDT-FUTURE"

    def test_serializes_to_json(self) -> None:
        msg = WsPingMessage(arg={"instType": "USDT-FUTURE"})
        data = msg.model_dump()
        assert data["type"] == "ping"
        assert data["arg"] == {"instType": "USDT-FUTURE"}


class TestWsSubscribeMessage:
    """Test subscription message serialization."""

    def test_creates_candle_subscription(self) -> None:
        msg = WsSubscribeMessage(
            arg={
                "instType": "USDT-FUTURE",
                "channel": "candle",
                "instId": "XAU-USDT-SWAP",
                "period": "5m",
            }
        )
        assert msg.type == "subscribe"
        assert msg.arg["instId"] == "XAU-USDT-SWAP"
        assert msg.arg["period"] == "5m"

    def test_serializes_correctly(self) -> None:
        msg = WsSubscribeMessage(
            arg={
                "instType": "USDT-FUTURE",
                "channel": "candle",
                "instId": "XAU-USDT-SWAP",
                "period": "1H",
            }
        )
        data = msg.model_dump()
        assert data["type"] == "subscribe"


class TestWsCandleStickSnapshot:
    """Test candlestick snapshot message parsing."""

    def test_parses_snapshot(self) -> None:
        raw = {
            "arg": {"instId": "XAU-USDT-SWAP", "period": "5m"},
            "action": "snapshot",
            "data": [
                {
                    "ts": "1686800000000",
                    "ow": "1950.50",
                    "h": "1955.00",
                    "l": "1948.00",
                    "c": "1953.20",
                    "vol": "1200.5",
                    "volQuote": "2340000.00",
                }
            ],
        }
        msg = WsCandleStickSnapshot.model_validate(raw)
        assert msg.action == "snapshot"
        assert msg.arg["period"] == "5m"
        assert len(msg.data) == 1
        assert msg.data[0]["ts"] == "1686800000000"

    def test_parses_update(self) -> None:
        raw = {
            "arg": {"instId": "XAU-USDT-SWAP", "period": "15m"},
            "action": "update",
            "data": [
                {
                    "ts": "1686800000000",
                    "ow": "1950.50",
                    "h": "1955.00",
                    "l": "1948.00",
                    "c": "1953.20",
                    "vol": "1200.5",
                    "volQuote": "2340000.00",
                }
            ],
        }
        msg = WsCandleStickUpdate.model_validate(raw)
        assert msg.action == "update"


class TestParseCandleTimestamp:
    """Test timestamp conversion from Bitget milliseconds."""

    def test_parses_epoch_0(self) -> None:
        from xauusdt.exchange.websocket import parse_candle_to_timestamp_sync  # noqa: PLC0415

        dt = parse_candle_to_timestamp_sync("0")
        assert dt == datetime(1970, 1, 1, tzinfo=UTC)

    def test_parses_known_timestamp(self) -> None:
        from xauusdt.exchange.websocket import parse_candle_to_timestamp_sync  # noqa: PLC0415

        # 1718446800000 ms = 2024-06-15 10:20:00 UTC
        dt = parse_candle_to_timestamp_sync("1718446800000")
        assert dt == datetime(2024, 6, 15, 10, 20, tzinfo=UTC)

    def test_parses_milliseconds(self) -> None:
        from xauusdt.exchange.websocket import parse_candle_to_timestamp_sync  # noqa: PLC0415

        dt = parse_candle_to_timestamp_sync("1718446800000")
        assert dt.hour == 10
        assert dt.minute == 20

    def test_returns_aware_datetime(self) -> None:
        from xauusdt.exchange.websocket import parse_candle_to_timestamp_sync  # noqa: PLC0415

        dt = parse_candle_to_timestamp_sync("1686800000000")
        assert dt.tzinfo is not None
