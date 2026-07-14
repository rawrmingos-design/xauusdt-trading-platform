"""Bitget public REST client for futures market data."""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from xauusdt.exchange.exceptions import (
    BitgetError,
    BitgetRateLimitError,
    BitgetRequestError,
    BitgetServerError,
)
from xauusdt.exchange.models import BitgetApiResponse, Contract, ContractInfo, to_contract

log = structlog.get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception should be retried."""
    if isinstance(exc, BitgetServerError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.NetworkError):
        return True
    return False


DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3


class BitgetPublicClient:
    """Async public REST client for Bitget futures API.

    Usage::

        client = BitgetPublicClient(base_url="https://api.bitget.com")
        async with client:
            contracts = await client.get_contracts()
    """

    def __init__(
        self,
        base_url: str = "https://api.bitget.com",
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BitgetPublicClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )
        return self

    async def __aexit__(self, *exc_args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Send an HTTP request with retry and error handling."""
        if not self._client:
            raise RuntimeError("Client not opened; use 'async with'")

        url = self._base_url + path

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                try:
                    response = await self._client.request(method, path, **kwargs)
                except httpx.TimeoutException:
                    log.warning(
                        "bitget_request_timeout",
                        url=url,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    raise
                except httpx.NetworkError:
                    log.warning(
                        "bitget_network_error", url=url, attempt=attempt.retry_state.attempt_number
                    )
                    raise

                log.info(
                    "bitget_request",
                    method=method,
                    url=url,
                    status=response.status_code,
                    attempt=attempt.retry_state.attempt_number,
                )

                if response.status_code == 429:
                    raise BitgetRateLimitError(f"Rate limited: {response.text}")
                if response.status_code >= 500:
                    raise BitgetServerError(f"Server error {response.status_code}: {response.text}")
                if response.status_code >= 400:
                    raise BitgetRequestError(
                        f"Request error {response.status_code}: {response.text}"
                    )

                parsed: dict[str, Any] = response.json()
                api_resp = BitgetApiResponse(**parsed)

                if api_resp.code != "00000":
                    raise BitgetRequestError(f"API error code={api_resp.code} msg={api_resp.msg}")

                return parsed

        # Should not reach here
        raise BitgetError("Unexpected error in request loop")

    async def get_contracts(self, product_type: str = "UMCBL") -> list[Contract]:
        """Retrieve futures contract list.

        Args:
            product_type: UMCBL (USDT-M perpetual), CMCBL (coin-M), etc.

        Returns:
            List of simplified Contract domain models.
        """
        resp = await self._request(
            "GET",
            "/api/mix/v1/market/contracts",
            params={"productType": product_type},
        )
        contracts = BitgetApiResponse(**resp)
        infos = [ContractInfo(**item) for item in contracts.data] if contracts.data else []

        return [to_contract(info) for info in infos]

    async def get_contract(self, symbol: str) -> Contract | None:
        """Retrieve a single contract by symbol."""
        contracts = await self.get_contracts()
        for c in contracts:
            if c.symbol == symbol:
                return c
        return None
