"""Typed feature models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MarketStructure(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"
    UNKNOWN = "unknown"


@dataclass
class EMAFeature:
    """EMA value at a specific index."""

    ema_value: float
    period: int
    valid: bool  # False if insufficient history


@dataclass
class ATRFeature:
    """ATR value at a specific index."""

    atr_value: float
    period: int
    valid: bool


@dataclass
class ADXFeature:
    """ADX value at a specific index."""

    adx_value: float
    period: int
    plus_di: float
    minus_di: float
    valid: bool


@dataclass
class SwingPoint:
    """A detected swing high or low."""

    index: int
    price: float
    side: str  # "high" or "low"
    left_bars: int
    right_bars: int
    confirmed: bool  # True = confirmed, False = candidate


@dataclass
class StructureFeature:
    """Market structure classification at a specific index."""

    structure: MarketStructure
    hh: bool  # higher-high
    hl: bool  # higher-low
    lh: bool  # lower-high
    ll: bool  # lower-low
    swing_high: SwingPoint | None
    swing_low: SwingPoint | None
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure": self.structure.value,
            "hh": self.hh,
            "hl": self.hl,
            "lh": self.lh,
            "ll": self.ll,
            "swing_high": self.swing_high.index if self.swing_high else None,
            "swing_low": self.swing_low.index if self.swing_low else None,
            "valid": self.valid,
        }


@dataclass
class CandleFeatures:
    """All features for a single candle."""

    index: int
    ema_9: EMAFeature
    ema_21: EMAFeature
    atr_14: ATRFeature
    adx_14: ADXFeature
    structure: StructureFeature

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "ema_9": self.ema_9.ema_value if self.ema_9.valid else None,
            "ema_21": self.ema_21.ema_value if self.ema_21.valid else None,
            "atr_14": self.atr_14.atr_value if self.atr_14.valid else None,
            "adx_14": self.adx_14.adx_value if self.adx_14.valid else None,
            "plus_di": self.adx_14.plus_di if self.adx_14.valid else None,
            "minus_di": self.adx_14.minus_di if self.adx_14.valid else None,
            "structure": self.structure.to_dict(),
        }
