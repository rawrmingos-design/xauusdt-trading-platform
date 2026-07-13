"""Tests for Bitget public REST client using mocked HTTP responses."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.exceptions import (
    BitgetRateLimitError,
    BitgetRequestError,
    BitgetServerError,
)

CONTRACTS_RESPONSE = {
    "code": "00000",
    "msg": "success",
    "data": [
        {
            "symbol": "XAUUSDT_UMCBL",
            "productType": "UMCBL",
            "baseCoin": "XAU",
            "quoteCoin": "USDT",
            "size": "0.1",
            "minTradeAmount": "0.001",
            "pricePlace": "1",
            "volumePlace": "3",
        },
        {
            "symbol": "BTCUSDT_UMCBL",
            "productType": "UMCBL",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "size": "0.001",
            "minTradeAmount": "0.001",
            "pricePlace": "2",
            "volumePlace": "3",
        },
    ],
}


@pytest.fixture
def client() -> BitgetPublicClient:
    return BitgetPublicClient(base_url="https://fake.bitget.com")


class TestClientInit:
    def test_default_timeout(self) -> None:
        c = BitgetPublicClient()
        assert c._timeout == 30

    def test_custom_base_url(self) -> None:
        c = BitgetPublicClient(base_url="https://custom.api.com")
        assert c._base_url == "https://custom.api.com"


class TestClientContextManager:
    async def test_context_manager_creates_client(self) -> None:
        async with BitgetPublicClient(base_url="https://fake.bitget.com") as c:
            assert c._client is not None

    async def test_context_manager_closes_client(self) -> None:
        c = BitgetPublicClient(base_url="https://fake.bitget.com")
        async with c:
            pass
        assert c._client is None

    async def test_request_before_open_raises(self) -> None:
        c = BitgetPublicClient(base_url="https://fake.bitget.com")
        with pytest.raises(RuntimeError, match="not opened"):
            await c._request("GET", "/test")


class TestRequestRetries:
    async def test_retries_on_500(self, client: BitgetPublicClient) -> None:
        """Should retry on 5xx and raise BitgetServerError after exhausting retries."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.side_effect = [
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(503, text="Service Unavailable"),
        ]
        client._client = mock
        client._max_retries = 2

        with pytest.raises(BitgetServerError, match="503"):
            await client._request("GET", "/test")

        assert mock.request.call_count == 2  # max_retries attempts

    async def test_retries_on_timeout(self, client: BitgetPublicClient) -> None:
        """Should retry on httpx.TimeoutException."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.side_effect = [
            httpx.TimeoutException("timeout"),
            httpx.Response(200, json={"code": "00000", "msg": "ok"}),
        ]
        client._client = mock
        client._max_retries = 2

        resp = await client._request("GET", "/test")
        assert resp["code"] == "00000"

    async def test_no_retry_on_400(self, client: BitgetPublicClient) -> None:
        """Should NOT retry on 4xx (non-429)."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(400, text="Bad Request")
        client._client = mock
        client._max_retries = 3

        with pytest.raises(BitgetRequestError, match="400"):
            await client._request("GET", "/test")

        assert mock.request.call_count == 1

    async def test_rate_limit(self, client: BitgetPublicClient) -> None:
        """Should raise BitgetRateLimitError on 429."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(429, text="Too Many Requests")
        client._client = mock

        with pytest.raises(BitgetRateLimitError, match="Rate limited"):
            await client._request("GET", "/test")

        assert mock.request.call_count == 1  # no retry on 429


class TestGetContracts:
    async def test_get_contracts_returns_list(self, client: BitgetPublicClient) -> None:
        """Should parse Contract domain objects from the API response."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json=CONTRACTS_RESPONSE)
        client._client = mock

        contracts = await client.get_contracts()

        assert len(contracts) == 2
        assert contracts[0].symbol == "XAUUSDT_UMCBL"
        assert contracts[0].contract_size == 0.1
        assert contracts[1].symbol == "BTCUSDT_UMCBL"

    async def test_get_contracts_empty_response(self, client: BitgetPublicClient) -> None:
        """Should return empty list when API returns no data."""
        resp = {"code": "00000", "msg": "success", "data": []}
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json=resp)
        client._client = mock

        contracts = await client.get_contracts()
        assert contracts == []

    async def test_get_contracts_api_error(self, client: BitgetPublicClient) -> None:
        """Should raise BitgetRequestError on non-zero API code."""
        resp = {"code": "40001", "msg": "invalid parameter"}
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json=resp)
        client._client = mock

        with pytest.raises(BitgetRequestError, match="40001"):
            await client.get_contracts()


class TestGetContract:
    async def test_get_contract_by_symbol(self, client: BitgetPublicClient) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json=CONTRACTS_RESPONSE)
        client._client = mock

        contract = await client.get_contract("XAUUSDT_UMCBL")
        assert contract is not None
        assert contract.symbol == "XAUUSDT_UMCBL"
        assert contract.contract_size == 0.1

    async def test_get_contract_not_found(self, client: BitgetPublicClient) -> None:
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json=CONTRACTS_RESPONSE)
        client._client = mock

        contract = await client.get_contract("DOGEUSDT_UMCBL")
        assert contract is None


class TestLogging:
    async def test_request_logged(self, client: BitgetPublicClient) -> None:
        """Verify logging doesn't crash — structural assertion."""
        mock = AsyncMock(spec=httpx.AsyncClient)
        mock.request.return_value = httpx.Response(200, json={"code": "00000", "msg": "ok"})
        client._client = mock

        with patch("xauusdt.exchange.client.log.info") as mock_log:
            await client._request("GET", "/test")
            mock_log.assert_called_once()
            args, kwargs = mock_log.call_args
            assert kwargs.get("method") == "GET"
