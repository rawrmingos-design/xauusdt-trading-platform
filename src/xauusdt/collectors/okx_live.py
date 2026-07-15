"""Live candle collector for OKX XAU-USDT-SWAP.

Runs a persistent WebSocket connection to OKX public API,
streams candlestick updates, and persists them to the database.
"""

from __future__ import annotations

import asyncio
import logging

from xauusdt.exchange.okx_websocket import OKXWebSocketClient
from xauusdt.storage.candle_repository import CandleRepository

logger = logging.getLogger(__name__)

OKX_SYMBOL = "XAU-USDT-SWAP"
GRANULARITY = "15m"
MAX_RETRIES = 3
RETRY_DELAY = 5


class OKXLiveCollector:
    """Persistent OKX candle WebSocket collector."""

    def __init__(
        self,
        repository: CandleRepository,
        symbol: str = OKX_SYMBOL,
        granularity: str = GRANULARITY,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._repository = repository
        self._symbol = symbol
        self._granularity = granularity
        self._max_retries = max_retries

    async def run(self) -> None:
        """Start the live collector loop."""
        retry_count = 0

        while retry_count < self._max_retries:
            try:
                await self._run_single_session()
                retry_count = 0  # Reset on successful session start
            except Exception as exc:
                retry_count += 1
                logger.warning(
                    "OKX collector session failed (attempt %d/%d): %s",
                    retry_count,
                    self._max_retries,
                    exc,
                )
                if retry_count < self._max_retries:
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    logger.error("OKX collector exhausted retries. Giving up.")
                    raise

    async def _run_single_session(self) -> None:
        """Run one continuous session: connect, subscribe, receive."""
        async with OKXWebSocketClient() as client:
            await client.start_keepalive()
            await client.subscribe_candlestick(
                symbol=self._symbol,
                granularity=self._granularity,
            )
            logger.info(
                "OKX live collector started: %s %s",
                self._symbol,
                self._granularity,
            )
            await client.receive_loop(self._on_candle)

    async def _on_candle(self, candle) -> None:  # type: ignore[no-untyped-def]
        """Handle incoming candle from OKX WebSocket."""
        logger.debug(
            "OKX candle: %s O=%s H=%s L=%s C=%s V=%s",
            candle.open_time,
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
        )
        # Persist to database
        await self._repository.upsert_many([candle])
