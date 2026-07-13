"""Exchange adapter placeholder."""

from xauusdt.exchange.client import BitgetPublicClient
from xauusdt.exchange.exceptions import (
    BitgetError,
    BitgetRateLimitError,
    BitgetRequestError,
    BitgetServerError,
)
from xauusdt.exchange.models import Contract, ContractInfo

__all__ = [
    "BitgetPublicClient",
    "BitgetError",
    "BitgetRateLimitError",
    "BitgetRequestError",
    "BitgetServerError",
    "Contract",
    "ContractInfo",
]
