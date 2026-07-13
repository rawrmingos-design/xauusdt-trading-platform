"""Tests for Bitget exchange exceptions."""

from xauusdt.exchange.exceptions import (
    BitgetAuthError,
    BitgetError,
    BitgetRateLimitError,
    BitgetRequestError,
    BitgetServerError,
)


class TestBitgetExceptions:
    def test_base_exception(self) -> None:
        assert issubclass(BitgetError, Exception)

    def test_auth_error(self) -> None:
        assert issubclass(BitgetAuthError, BitgetError)

    def test_rate_limit_error(self) -> None:
        err = BitgetRateLimitError("too fast")
        assert str(err) == "too fast"

    def test_server_error(self) -> None:
        err = BitgetServerError("5xx")
        assert str(err) == "5xx"

    def test_request_error(self) -> None:
        err = BitgetRequestError("bad request")
        assert str(err) == "bad request"
