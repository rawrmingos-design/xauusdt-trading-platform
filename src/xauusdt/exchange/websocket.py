"""Bitget Futures WebSocket client for public market data.

Handles:
- Connection lifecycle (connect, disconnect)
- Auto-ping keepalive (15s interval)
- Candlestick channel subscription for USDT-FUTURES
- Message dispatch to registered handlers
- Reconnect with exponential backoff
- Stale connection detection
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
from websockets.typing import Subprotocol

from xauusdt.exchange.websocket_models import (
    WsCandleStickSnapshot,
    WsCandleStickUpdate,
    WsPingMessage,
    WsSubscribeMessage,
)

logger = logging.getLogger(__name__)

# Bitget Futures WebSocket public endpoints
WS_ENDPOINTS = [
    "wss://ws.bitget.com/v2/ws/future",
    "wss://ws-two.bitget.com/preview-future/real",
]

# Ping interval in seconds
PING_INTERVAL = 15
# Stale threshold: if no message received in this many seconds, reconnect
STALE_THRESHOLD = 90
# Reconnect backoff parameters
RECONNECT_MIN_DELAY = 2
RECONNECT_MAX_DELAY = 60
RECONNECT_BACKOFF_FACTOR = 2


class BitgetWebSocketClient:
    """Bitget Futures WebSocket client.

    Manages a single persistent connection with auto-reconnect.
    Supports registering handlers for different message types.
    """

    def __init__(
        self,
        endpoints: list[str] | None = None,
        ping_interval: float = PING_INTERVAL,
        stale_threshold: float = STALE_THRESHOLD,
        reconnect_min_delay: float = RECONNECT_MIN_DELAY,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY,
    ) -> None:
        self._endpoints = endpoints or WS_ENDPOINTS
        self._ping_interval = ping_interval
        self._stale_threshold = stale_threshold
        self._reconnect_min_delay = reconnect_min_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._ws: Any = None  # websockets WebSocketClientProtocol
        self._handler: Callable[[str, str, Any], Awaitable[None]] | None = None
        self._connected = False
        self._running = False
        self._ping_task: asyncio.Task[None] | None = None
        self._last_message_time: float | None = None
        self._reconnect_delay: float = reconnect_min_delay
        self._connect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a WebSocket connection to the first available endpoint."""
        async with self._connect_lock:
            if self._ws is not None and self._ws.open:
                return
            await self._do_connect()

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

    async def _do_connect(self) -> None:
        """Attempt to connect to the first available endpoint."""
        for endpoint in self._endpoints:
            try:
                logger.info("Connecting to %s ...", endpoint)
                self._ws = await connect(
                    endpoint,
                    subprotocols=[Subprotocol("v1")],
                    ping_interval=None,  # We handle pings manually
                    ping_timeout=10,
                    close_timeout=10,
                )
                self._connected = True
                self._running = True
                self._last_message_time = asyncio.get_event_loop().time()
                self._reconnect_delay = self._reconnect_min_delay
                logger.info("Connected to %s", endpoint)
                return
            except Exception as exc:
                logger.warning("Failed to connect to %s: %s", endpoint, exc)
                continue
        raise ConnectionError("All WebSocket endpoints failed")

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    async def start_keepalive(self) -> None:
        """Start the ping keepalive loop."""
        self._running = True
        self._ping_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Send ping frames at regular intervals."""
        while self._running:
            await asyncio.sleep(self._ping_interval)
            if self._connected and self._ws is not None and self._ws.open:
                try:
                    ping = WsPingMessage(arg={"instType": "USDT-FUTURE"})
                    await self._ws.send(json.dumps(ping.model_dump()))
                    logger.debug("Sent ping")
                except Exception as exc:
                    logger.warning("Ping failed: %s", exc)
                    break

    # ------------------------------------------------------------------
    # Stale detection
    # ------------------------------------------------------------------

    async def check_stale(self) -> bool:
        """Return True if the connection appears stale."""
        if self._last_message_time is None:
            return True
        elapsed = asyncio.get_event_loop().time() - self._last_message_time
        return elapsed > self._stale_threshold

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def subscribe_candlestick(
        self,
        symbol: str,
        granularity: str,
        inst_type: str = "USDT-FUTURE",
    ) -> None:
        """Subscribe to the candlestick channel for a symbol."""
        if not self._connected or self._ws is None:
            raise RuntimeError("Not connected")
        arg = {"instType": inst_type, "channel": "candle", "instId": symbol, "period": granularity}
        msg = WsSubscribeMessage(arg=arg)
        await self._ws.send(json.dumps(msg.model_dump()))

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def receive_loop(self, handler: Callable[[str, str, Any], Awaitable[None]]) -> None:
        """Start the message receive loop with the given handler.

        The handler is called as `handler(event_type, channel, data)` for each
        parsed message. The loop runs until the connection is closed or
        `disconnect()` is called.

        Args:
            handler: Async callback that receives event_type, channel, and data.
        """
        self._handler = handler
        self._running = True
        self._reconnect_delay = self._reconnect_min_delay

        while self._running:
            try:
                if not self._connected or self._ws is None:
                    await self._do_connect()

                if self._ws is not None and self._ws.open:
                    await self._handle_message_loop()
            except (ConnectionClosedError, ConnectionClosedOK):
                logger.warning(
                    "WebSocket connection closed, reconnecting in %.0fs", self._reconnect_delay
                )
                self._connected = False
                await self._wait_for_reconnect()
            except Exception as exc:
                logger.error("Receive loop error: %s", exc)
                self._connected = False
                await self._wait_for_reconnect()

    async def _handle_message_loop(self) -> None:
        """Read and dispatch messages from the WebSocket connection."""
        if self._ws is None:
            return
        async for raw_message in self._ws:
            if not self._running:
                return
            self._last_message_time = asyncio.get_event_loop().time()
            try:
                message = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Failed to parse message: %s", exc)
                continue
            await self._dispatch_message_async(message)

    def _dispatch_message(self, raw: dict[str, Any]) -> None:
        """Parse a raw message dict and dispatch to the handler."""
        ...

    async def _dispatch_message_async(self, raw: dict[str, Any]) -> None:
        """Async version of dispatch that can handle candle messages."""
        action = raw.get("action", "")
        arg = raw.get("arg", {})
        channel = arg.get("channel", "")
        data = raw.get("data")
        code = raw.get("code")

        if code is not None:
            self._handle_response(action, channel, code, raw.get("msg", ""), data)
            return

        if action == "error":
            logger.error("WebSocket error: %s", raw.get("msg", "unknown"))
            return

        if data is None or not data:
            return

        # Handle specific message types
        if channel == "candle" and action in ("snapshot", "update"):
            inst_id = arg.get("instId", "")
            period = arg.get("period", "")
            await self._handle_candle_message(action, inst_id, period, data)
        elif action == "pong":
            logger.debug("Received pong")

    def _handle_response(
        self,
        action: str,
        channel: str,
        code: int,
        msg: str | None,
        data: list[dict[str, Any]] | None,
    ) -> None:
        """Handle subscription response from the exchange."""
        if code == 0:
            logger.info("Subscription %s to %s succeeded", action, channel)
        else:
            logger.error("Subscription %s to %s failed: %s (code=%d)", action, channel, msg, code)

    async def _handle_candle_message(
        self,
        action: str,
        inst_id: str,
        period: str,
        data: list[dict[str, Any]],
    ) -> None:
        """Parse candlestick data and dispatch to the handler."""
        if self._handler is None:
            return
        for item in data:
            try:
                if action == "snapshot":
                    parsed: Any = WsCandleStickSnapshot(
                        arg={"instId": inst_id, "period": period},
                        action=action,
                        data=[item],
                    )
                else:
                    parsed = WsCandleStickUpdate(
                        arg={"instId": inst_id, "period": period},
                        action=action,
                        data=[item],
                    )
                event_type = "candle_snapshot" if action == "snapshot" else "candle_update"
                await self._handler(event_type, period, parsed.data[0])
            except Exception as exc:
                logger.error("Failed to parse candle message: %s", exc)

    # ------------------------------------------------------------------
    # Reconnect
    # ------------------------------------------------------------------

    async def _wait_for_reconnect(self) -> None:
        """Wait with exponential backoff before reconnecting."""
        if not self._running:
            return
        delay = min(self._reconnect_delay, self._reconnect_max_delay)
        logger.info("Reconnecting in %.0f seconds ...", delay)
        await asyncio.sleep(delay)
        self._reconnect_delay = min(
            self._reconnect_delay * RECONNECT_BACKOFF_FACTOR,
            self._reconnect_max_delay,
        )

    @property
    def is_connected(self) -> bool:
        """Return True if the WebSocket is currently connected and open."""
        return self._connected and self._ws is not None and self._ws.open


def parse_candle_to_timestamp_sync(ts_str: str) -> datetime:
    """Parse a Bitget candle timestamp (milliseconds since epoch) to timezone-aware UTC datetime."""
    ms = int(ts_str)
    return datetime.fromtimestamp(ms / 1000, tz=UTC)
