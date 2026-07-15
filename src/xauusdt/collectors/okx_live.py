"""Live candle collector for OKX XAU-USDT-SWAP.

Polls OKX REST candlestick endpoint at granularity intervals,
detects new candles, and persists them to the database.

No WebSocket candle support on OKX public endpoint — REST polling
is the only way to get live OHLCV data.
"""

from __future__ import annotations

import asyncio
import logging

from xauusdt.exchange.okx_client import OKXClient
from xauusdt.storage.candle_repository import CandleRepository

log = logging.getLogger(__name__)


class OKXLiveCollector:
    """Polls OKX REST for new candles and persists them."""

    def __init__(
        self,
        repository: CandleRepository,
        symbol: str = "XAU-USDT-SWAP",
        granularity: str = "15m",
        limit: int = 5,
        min_poll_interval: int = 120,
    ) -> None:
        self._repository = repository
        self._symbol = symbol
        self._granularity = granularity
        self._limit = limit
        self._running = False
        self._min_poll_interval = min_poll_interval

    async def run(self, max_cycles: int | None = None) -> None:
        """Main loop: poll for new candles every granularity interval."""
        self._running = True
        log.info(
            "OKXLiveCollector started: symbol=%s granularity=%s min_poll=%ds",
            self._symbol,
            self._granularity,
            self._min_poll_interval,
        )

        last_ts: float = 0.0
        cycle = 0

        while self._running:
            if max_cycles and cycle >= max_cycles:
                log.info("Reached max cycles (%d). Stopping.", max_cycles)
                break
            try:
                async with OKXClient() as client:
                    candles = await client.fetch_candles(
                        symbol=self._symbol,
                        granularity=self._granularity,
                        limit=self._limit,
                    )

                if not candles:
                    log.debug("No candles returned from OKX")
                    await asyncio.sleep(self._poll_interval())
                    continue

                # Check if we got new data
                newest_ts = candles[0].open_time.timestamp()
                if newest_ts <= last_ts:
                    log.debug(
                        "No new candles since last poll (newest_ts=%.0f, last_ts=%.0f)",
                        newest_ts,
                        last_ts,
                    )
                    await asyncio.sleep(self._poll_interval())
                    continue

                last_ts = newest_ts

                # Persist
                persisted = 0
                for candle in candles:
                    await self._repository.upsert_many([candle])
                    persisted += 1
                    log.info(
                        "Candle persisted: %s open=%.2f high=%.2f low=%.2f close=%.2f vol=%s",
                        candle.open_time.isoformat(),
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                    )

                log.info(
                    "Collected %d new candles (newest: %s)",
                    persisted,
                    candles[0].open_time.isoformat(),
                )

            except asyncio.CancelledError:
                log.info("OKXLiveCollector cancelled")
                raise
            except Exception:
                log.exception("Error in poll cycle")

            await asyncio.sleep(self._poll_interval())

    def stop(self) -> None:
        """Signal the collector to stop."""
        self._running = False

    def _poll_interval(self) -> int:
        """Return sleep seconds between polls."""
        intervals: dict[str, int] = {
            "1m": 50,
            "5m": 120,
            "15m": 300,
            "30m": 1700,
            "1H": 3500,
            "4H": 14000,
            "1D": 85000,
        }
        return max(intervals.get(self._granularity, 800), self._min_poll_interval)
