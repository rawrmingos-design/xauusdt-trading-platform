"""OKX exchange client — public market data API.

Adapts OKX REST API responses to our normalized Candle model.
No API keys required for public endpoints.

API Docs: https://www.okx.com/docs-v5/en/#overview
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from xauusdt.exchange.exceptions import BitgetRequestError
from xauusdt.exchange.models import Candle

log = logging.getLogger(__name__)


class OKXClient:
    """OKX public market data client.

    Maps OKX response format to our normalized Candle model:
    - OKX: ['ts','o','h','l','c','vol','volCcy','volCcyQuote','confirm']
    - Normalized: Candle(symbol, granularity, open_time, open, high, low, close, volume, quote_volume)
    """

    BASE_URL = "https://www.okx.com"
    SYMBOL_MAP: dict[str, str] = {
        "XAUUSDT_UMCBL": "XAU-USDT-SWAP",
    }
    GRANULARITY_MAP: dict[str, str] = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1H": "60m",
        "2H": "120m",
        "4H": "240m",
        "1D": "1D",
        "1W": "1W",
    }
    SECOND_MAP: dict[str, int] = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1H": 3600,
        "2H": 7200,
        "4H": 14400,
        "1D": 86400,
        "1W": 604800,
    }
    MAX_LIMIT = 100

    def __init__(self, timeout: float = 10.0, max_retries: int = 3) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> OKXClient:
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(self._timeout),
        )
        return self

    async def __aexit__(self, *exc_args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Send HTTP request with retry logic."""
        if not self._client:
            raise RuntimeError("Client not opened; use 'async with'")

        @retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async def _do_request() -> dict[str, Any]:
            if not self._client:
                raise RuntimeError("Client not opened")
            resp = await self._client.get(path, params=params)
            log.info(
                "okx_request path=%s status=%d",
                path,
                resp.status_code,
            )

            if resp.status_code == 429:
                raise BitgetRequestError("Rate limited")

            if resp.status_code != 200:
                raise BitgetRequestError(f"OKX error {resp.status_code}: {resp.text[:200]}")

            result: dict[str, Any] = resp.json()
            code = str(result.get("code", ""))
            if code != "0":
                raise BitgetRequestError(f"OKX error {code}: {result.get('msg', '')}")

            return result

        return await _do_request()

    async def fetch_candles(
        self,
        symbol: str = "XAUUSDT_UMCBL",
        granularity: str = "15m",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = MAX_LIMIT,
    ) -> list[Candle]:
        """Fetch historical candles.

        Args:
            symbol: Normalized symbol (XAUUSDT_UMCBL).
            granularity: Candle size (1m, 5m, 15m, 30m, 1H, 4H, 1D).
            start_time: If set, use as 'after' param (oldest allowed ts).
            end_time: Ignored — OKX uses 'after' for pagination only.
            limit: Number of candles per request (1-100).

        Returns:
            List of normalized Candle objects (newest first).
        """
        okx_symbol = self.SYMBOL_MAP.get(symbol, symbol)
        okx_gran = self.GRANULARITY_MAP.get(granularity, granularity)

        params: dict[str, str] = {
            "instId": okx_symbol,
            "bar": okx_gran,
            "limit": str(min(limit, self.MAX_LIMIT)),
        }

        # OKX only uses 'after' for pagination.
        # When start_time is provided, it becomes the 'after' cursor
        # (fetch candles older than this timestamp).
        if start_time:
            params["after"] = str(int(start_time.timestamp() * 1000))

        data = await self._request("/api/v5/market/candles", params)
        raw_candles = data["data"]

        candles = []
        for raw in raw_candles:
            # OKX: ["ts","o","h","l","c","vol","volCcy","volCcyQuote","confirm"]
            ts, o, h, low, c, vol, *_ = raw
            open_time = datetime.fromtimestamp(int(ts) / 1000, tz=UTC)
            candle = Candle(
                symbol=symbol,
                granularity=granularity,
                open_time=open_time,
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
                volume=float(vol),
                quote_volume=float(vol) * float(c),  # estimate if volCcyQuote not reliable
            )
            candles.append(candle)

        return candles

    async def fetch_candles_paginated(
        self,
        symbol: str = "XAUUSDT_UMCBL",
        granularity: str = "15m",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> AsyncIterator[Candle]:
        """Fetch candles with automatic pagination.

        Yields Candle objects one at a time.
        Uses OKX 'after'/'before' pagination via timestamps.
        """
        current_end = end_time

        while True:
            kwargs: dict[str, Any] = {
                "symbol": symbol,
                "granularity": granularity,
                "end_time": current_end,
                "limit": self.MAX_LIMIT,
            }
            if start_time:
                kwargs["start_time"] = start_time

            candles = await self.fetch_candles(**kwargs)

            if not candles:
                break

            for candle in candles:
                yield candle

            if len(candles) < self.MAX_LIMIT:
                break

            first_candle = candles[0]
            current_end = first_candle.open_time

            if start_time and candles[0].open_time <= start_time:
                break


def _is_retryable(exception: BaseException) -> bool:
    """Retry on transient network errors."""
    return isinstance(exception, (httpx.TimeoutException, httpx.NetworkError))
