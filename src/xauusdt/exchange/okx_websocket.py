"""OKX WebSocket client for public market data.

Handles candlestick channel subscriptions via OKX public WebSocket API.
No API keys required.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from websockets.client import connect  # type: ignore[attr-defined]
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from xauusdt.exchange.models import Candle

logger = logging.getLogger(__name__)

WS_OKX_ENDPOINT = "wss://ws.okx.com:8443/ws/v5/public"

# Ping interval in seconds
PING_INTERVAL = 30
# Reconnect backoff parameters
RECONNECT_MIN_DELAY = 2
RECONNECT_MAX_DELAY = 60
RECONNECT_BACKOFF_FACTOR = 2


class OKXWebSocketClient:
    """OKX public WebSocket client for candlestick data.

    Subscribes to the OKX public candlestick channel and dispatches
    parsed Candle objects to registered handlers.
    """

    def __init__(self, endpoint: str = WS_OKX_ENDPOINT) -> None:
        self._endpoint = endpoint
        self._ws = None
        self._handler: Callable[[Candle], Awaitable[None]] | None = None
        self._connected = False
        self._running = False
        self._ping_task: asyncio.Task[None] | None = None
        self._reconnect_delay = RECONNECT_MIN_DELAY
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a WebSocket connection to the OKX public endpoint."""
        async with self._connect_lock:
            if self._ws is not None and self._ws.open:
                return
            logger.info("Connecting to OKX WebSocket %s ...", self._endpoint)
            self._ws = await connect(self._endpoint)
            self._connected = True
            self._running = True
            logger.info("Connected to OKX WebSocket")

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
            self._ping_task = None
        if self._ws is not None and self._ws.open:
            await self._ws.close()
            self._ws = None
        self._connected = False

    async def __aenter__(self) -> OKXWebSocketClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    async def start_keepalive(self) -> None:
        """Start the ping keepalive loop (OKX public uses raw 'ping' string)."""
        self._running = True
        self._ping_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Send 'ping' text message at regular intervals."""
        while self._running:
            await asyncio.sleep(PING_INTERVAL)
            if self._connected and self._ws is not None and self._ws.open:
                try:
                    await self._ws.send("ping")
                except Exception as exc:
                    logger.warning("OKX ping failed: %s", exc)
                    break

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def subscribe_candlestick(
        self,
        symbol: str = "XAU-USDT-SWAP",
        granularity: str = "15m",
    ) -> None:
        """Subscribe to the candlestick channel for a symbol."""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")

        arg = {"channel": "candle", "instId": symbol, "bar": granularity}
        msg = {"op": "subscribe", "args": [arg]}
        await self._ws.send(json.dumps(msg))
        logger.info("Subscribed to OKX candle %s %s", symbol, granularity)

    # ------------------------------------------------------------------
    # Message receive
    # ------------------------------------------------------------------

    async def receive_loop(self, handler: Callable[[Candle], Awaitable[None]]) -> None:
        """Start the message receive loop.

        Args:
            handler: Async callback that receives a Candle object.
        """
        self._handler = handler
        self._running = True
        self._reconnect_delay = RECONNECT_MIN_DELAY

        while self._running:
            try:
                if not self._connected or self._ws is None:
                    await self.connect()

                if self._ws is not None and self._ws.open:
                    await self._handle_message_loop()
            except (ConnectionClosedError, ConnectionClosedOK):
                logger.warning("OKX WebSocket closed, reconnecting in %.0fs", self._reconnect_delay)
                self._connected = False
                await self._wait_for_reconnect()
            except Exception as exc:
                logger.error("OKX receive loop error: %s", exc)
                self._connected = False
                await self._wait_for_reconnect()

    async def _handle_message_loop(self) -> None:
        """Read and parse messages from the WebSocket connection."""
        if self._ws is None:
            return
        async for raw_message in self._ws:
            if not self._running:
                return
            try:
                message = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Failed to parse OKX WS message: %s", exc)
                continue

            if raw_message == "pong":
                continue

            await self._dispatch_message(message)

    async def _dispatch_message(self, raw: dict[str, Any]) -> None:
        """Parse an OKX WebSocket message and dispatch to the candle handler."""
        event = raw.get("event", "")
        arg = raw.get("arg", {})
        channel = arg.get("channel", "") if arg else ""

        # Subscribe confirmation
        if event == "subscribe":
            logger.info("OKX subscription confirmed: %s", arg)
            return

        # Handle error
        if event == "error":
            logger.error("OKX WS error: %s", raw.get("msg", "unknown"))
            return

        # Candle data
        if channel == "candle" and "data" in raw:
            data_list = raw["data"]
            for item in data_list:
                try:
                    candle = _parse_okx_candle(item)
                    if candle and self._handler:
                        await self._handler(candle)
                except Exception as exc:
                    logger.error("Failed to parse OKX candle: %s", exc)

    # ------------------------------------------------------------------
    # Reconnect
    # ------------------------------------------------------------------

    async def _wait_for_reconnect(self) -> None:
        """Wait with exponential backoff before reconnecting."""
        if not self._running:
            return
        delay = min(self._reconnect_delay, RECONNECT_MAX_DELAY)
        logger.info("OKX reconnecting in %.0f seconds ...", delay)
        await asyncio.sleep(delay)
        self._reconnect_delay = min(
            self._reconnect_delay * RECONNECT_BACKOFF_FACTOR,
            RECONNECT_MAX_DELAY,
        )

    @property
    def is_connected(self) -> bool:
        """Return True if the WebSocket is currently connected."""
        return self._connected and self._ws is not None and self._ws.open


def _parse_okx_candle(raw: dict[str, Any]) -> Candle | None:
    """Parse a single OKX candle data dict from WebSocket.

    OKX WS candle format: {"ts": "1234", "o": "1.0", "h": "2.0", "l": "0.5", "c": "1.5", "vol": "100", ...}
    Also supports the REST format list for backward compatibility.
    """
    try:
        ts_str = ""
        if isinstance(raw, dict):
            ts_str = raw.get("ts", "")
            o, h, low, c, vol = raw["o"], raw["h"], raw["l"], raw["c"], raw["vol"]
            period = raw.get("bar", "15m")
        elif isinstance(raw, (list, tuple)):
            ts_str = str(raw[0])
            o, h, low, c, vol = raw[1], raw[2], raw[3], raw[4], raw[5]
            period = "15m"
        else:
            return None

        ts_ms = int(ts_str)
        open_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        return Candle(
            symbol="XAU-USDT-SWAP",
            granularity=period,
            open_time=open_time,
            open=float(o),
            high=float(h),
            low=float(low),
            close=float(c),
            volume=float(vol),
        )
    except (IndexError, ValueError, TypeError, KeyError):
        return None
