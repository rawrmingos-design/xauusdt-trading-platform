"""Live WebSocket candle collector for Bitget Futures XAUUSDT.

Subscribes to the candlestick channel, tracks in-progress candles,
finalizes them when a newer candle arrives, and persists completed
candles through CandleRepository with idempotent upsert.

Finalization logic:
- Receive WebSocket candle update
- Normalize to Candle
- Store as in-progress state
- When a newer candle open_time appears, previous candle is finalized
- Persist finalized candle with upsert_many()

This approach uses "next candle started" as the finalization signal,
which is more deterministic than close_time <= now.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from xauusdt.exchange.models import Candle
from xauusdt.exchange.websocket import BitgetWebSocketClient, parse_candle_to_timestamp_sync
from xauusdt.storage.candle_repository import CandleRepository

logger = logging.getLogger(__name__)

# Supported granularities for live collection
LIVE_GRANULARITIES = {"5m", "15m", "1H", "4H"}
# Maximum in-progress candles tracked per symbol/granularity
MAX_IN_PROGRESS = 2


@dataclass
class _InProgressCandle:
    """A candle that is still being updated by the exchange."""

    candle: Candle
    last_update: datetime


class LiveCandleCollector:
    """Live WebSocket candle collector for Bitget Futures.

    Manages candle lifecycle:
    - Receives snapshot + update messages from WebSocket
    - Tracks in-progress candles
    - Finalizes candles when a newer interval begins
    - Persists finalized candles through repository
    """

    def __init__(
        self,
        client: BitgetWebSocketClient,
        repository: CandleRepository,
        symbol: str = "XAU-USDT-SWAP",
        granularities: set[str] | None = None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._symbol = symbol
        self._granularities = granularities or LIVE_GRANULARITIES
        # in_progress tracks active candles: key = (symbol, granularity, open_time)
        self._in_progress: dict[tuple[str, str, str], _InProgressCandle] = {}
        # Finalized candles waiting to be batch-persisted
        self._finalized: list[Candle] = []
        self._running = False

    async def start(self) -> None:
        """Start the live candle collector."""
        self._running = True
        await self._client.connect()
        await self._client.start_keepalive()

        # Subscribe to all granularities
        for granularity in self._granularities:
            await self._client.subscribe_candlestick(
                symbol=self._symbol,
                granularity=granularity,
                inst_type="USDT-FUTURE",
            )

        # Start the receive loop
        await self._client.receive_loop(self._on_message)

    async def stop(self) -> None:
        """Gracefully stop the collector."""
        logger.info("Stopping live candle collector ...")
        self._running = False
        await self._client.disconnect()

    async def _on_message(
        self,
        event_type: str,
        channel: str,
        data: Any,
    ) -> None:
        """Handle incoming WebSocket messages."""
        if channel not in self._granularities:
            return

        if event_type == "candle_snapshot":
            await self._handle_snapshot(channel, data)
        elif event_type == "candle_update":
            await self._handle_update(channel, data)

    async def _handle_snapshot(
        self,
        granularity: str,
        raw_data: dict[str, Any],
    ) -> None:
        """Handle WebSocket candlestick snapshot (initial state)."""
        candle = self._normalize_candle(raw_data, granularity)
        if candle is None:
            return

        key = (self._symbol, granularity, candle.open_time.isoformat())
        self._in_progress[key] = _InProgressCandle(
            candle=candle,
            last_update=candle.open_time,
        )
        logger.debug("Snapshot for %s %s at %s", self._symbol, granularity, candle.open_time)

    async def _handle_update(
        self,
        granularity: str,
        raw_data: dict[str, Any],
    ) -> None:
        """Handle WebSocket candlestick update (real-time change)."""
        candle = self._normalize_candle(raw_data, granularity)
        if candle is None:
            return

        key = (self._symbol, granularity, candle.open_time.isoformat())

        # Check if this is a NEW candle interval (indicates previous one is finalized)
        existing_keys = [
            k for k in self._in_progress if k[0] == self._symbol and k[1] == granularity
        ]
        existing_times = sorted(k[2] for k in existing_keys)

        new_open_time = candle.open_time.isoformat()
        if new_open_time not in existing_times:
            # This is a brand new candle — finalize all older ones
            await self._finalize_old_candles(granularity, new_open_time)

        # Update or create the in-progress candle
        if key in self._in_progress:
            self._in_progress[key].candle = candle
            self._in_progress[key].last_update = candle.open_time
        else:
            self._in_progress[key] = _InProgressCandle(
                candle=candle,
                last_update=candle.open_time,
            )

        # Prune old in-progress candles (keep only current + recent)
        self._prune_in_progress()

    async def _finalize_old_candles(
        self,
        granularity: str,
        new_open_time_iso: str,
    ) -> None:
        """Finalize candles that ended before the new candle started."""
        to_finalize: list[Candle] = []
        stale_keys: list[tuple[str, str, str]] = []

        for key in self._in_progress:
            if key[0] != self._symbol or key[1] != granularity:
                continue
            # If the candle's open_time is before the new candle, it's finalized
            if key[2] < new_open_time_iso:
                to_finalize.append(self._in_progress[key].candle)
                stale_keys.append(key)

        # Remove finalized candles from in-progress
        for key in stale_keys:
            del self._in_progress[key]

        if not to_finalize:
            return

        # Deduplicate by open_time (same candle may appear multiple times)
        seen: set[str] = set()
        unique_finalized: list[Candle] = []
        for c in to_finalize:
            k = c.open_time.isoformat()
            if k not in seen:
                seen.add(k)
                unique_finalized.append(c)

        if not unique_finalized:
            return

        # Persist finalized candles
        count = await self._repository.upsert_many(unique_finalized)
        logger.info(
            "Finalized %d candle(s) for %s %s, stored %d",
            len(unique_finalized),
            self._symbol,
            granularity,
            count,
        )

    def _prune_in_progress(self) -> None:
        """Remove in-progress candles that are too far behind the latest."""
        if len(self._in_progress) <= MAX_IN_PROGRESS:
            return

        # Sort by open_time, remove oldest
        sorted_keys = sorted(
            self._in_progress.keys(),
            key=lambda k: k[2],
        )
        # Keep the MAX_IN_PROGRESS most recent
        prune_count = len(sorted_keys) - MAX_IN_PROGRESS
        for key in sorted_keys[:prune_count]:
            del self._in_progress[key]
            logger.debug("Pruned in-progress candle: %s", key)

    @staticmethod
    def _normalize_candle(
        raw_data: dict[str, Any],
        granularity: str,
    ) -> Candle | None:
        """Convert a raw WebSocket candle payload to a domain Candle model.

        Bitget candlestick fields (all timestamps in milliseconds):
        - ow: open price
        - h: high price
        - l: low price
        - c: close price
        - vol: volume
        - volQuote: quote volume (USDT)
        - ts: candle open timestamp
        """
        try:
            ts_str = raw_data.get("ts", "")
            open_time = parse_candle_to_timestamp_sync(ts_str)

            return Candle(
                symbol="XAU-USDT-SWAP",
                granularity=granularity,
                open_time=open_time,
                open=float(raw_data.get("ow", 0)),
                high=float(raw_data.get("h", 0)),
                low=float(raw_data.get("l", 0)),
                close=float(raw_data.get("c", 0)),
                volume=float(raw_data.get("vol", 0)),
                quote_volume=float(raw_data.get("volQuote", 0)),
            )
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            logger.error("Failed to normalize candle: %s — data: %s", exc, raw_data)
            return None
