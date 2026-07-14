"""Typed models for Bitget exchange API responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class BitgetApiResponse(BaseModel):
    """Standard Bitget API response wrapper."""

    code: str
    msg: str
    data: Any = None
    request_time: str | None = Field(None, alias="requestTime")


class ContractInfo(BaseModel):
    """Bitget futures contract metadata (minimum fields)."""

    symbol: str = Field(alias="symbol")
    product_type: str = Field(alias="productType", default="")
    base_coin: str = Field(alias="baseCoin", default="")
    quote_coin: str = Field(alias="quoteCoin", default="")
    size: str = Field(alias="size", default="")
    min_trade_amount: str = Field(alias="minTradeAmount", default="")
    price_place: str = Field(alias="pricePlace", default="")
    volume_place: str = Field(alias="volumePlace", default="")

    @property
    def contract_size(self) -> float:
        return float(self.size) if self.size else 0.0


@dataclass
class Contract:
    """Simplified contract domain model."""

    symbol: str
    base_coin: str
    quote_coin: str
    contract_size: float
    min_trade_amount: float
    price_precision: int
    volume_precision: int


def to_contract(info: ContractInfo) -> Contract:
    """Map raw ContractInfo to simplified Contract domain model."""
    return Contract(
        symbol=info.symbol,
        base_coin=info.base_coin,
        quote_coin=info.quote_coin,
        contract_size=info.contract_size,
        min_trade_amount=float(info.min_trade_amount) if info.min_trade_amount else 0.0,
        price_precision=int(info.price_place) if info.price_place else 0,
        volume_precision=int(info.volume_place) if info.volume_place else 0,
    )
