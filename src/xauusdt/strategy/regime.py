"""Deterministic market regime classifier for entry gating.

Evidence basis: PROJECT-BACKTEST-013.
W2 failure was a high-range, low-efficiency (chop) path where both sides
lost and the short edge collapsed. Direction label alone did not separate
windows (all were DOWN ~-5.5%).

This module is pure and side-effect free. No LLM. No live API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MarketRegime(StrEnum):
    """Coarse regime labels used by the optional V3 entry gate."""

    TREND = "TREND"
    RANGE_CHOP = "RANGE_CHOP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeSnapshot:
    """Explainable snapshot of the current market regime."""

    label: MarketRegime
    efficiency_ratio: float
    range_pct: float
    avg_candle_range_pct: float
    lookback: int
    net_return_pct: float

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "label": self.label.value,
            "efficiency_ratio": self.efficiency_ratio,
            "range_pct": self.range_pct,
            "avg_candle_range_pct": self.avg_candle_range_pct,
            "lookback": self.lookback,
            "net_return_pct": self.net_return_pct,
        }


@dataclass(frozen=True)
class RegimeConfig:
    """Thresholds for RANGE_CHOP detection.

    RANGE_CHOP when:
      efficiency_ratio < efficiency_max
      AND range_pct >= range_pct_min
      AND avg_candle_range_pct >= avg_range_pct_min

    Defaults are conservative research values informed by BACKTEST-013,
    not production-optimized.
    """

    lookback: int = 96  # 24h on 15m candles
    efficiency_max: float = 0.30
    range_pct_min: float = 0.035  # 3.5% high-low span over lookback
    avg_range_pct_min: float = 0.0012  # ~0.12% avg candle range / price


def classify_regime(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    config: RegimeConfig | None = None,
) -> RegimeSnapshot:
    """Classify regime from closed price path (no lookahead).

    Uses only the last ``lookback`` bars ending at the most recent close.
    Requires at least ``lookback`` samples; otherwise returns UNKNOWN.
    """
    cfg = config or RegimeConfig()
    n = len(closes)
    if n < cfg.lookback or len(highs) != n or len(lows) != n:
        return RegimeSnapshot(
            label=MarketRegime.UNKNOWN,
            efficiency_ratio=0.0,
            range_pct=0.0,
            avg_candle_range_pct=0.0,
            lookback=cfg.lookback,
            net_return_pct=0.0,
        )

    start = n - cfg.lookback
    window_closes = closes[start:]
    window_highs = highs[start:]
    window_lows = lows[start:]

    first = window_closes[0]
    last = window_closes[-1]
    if first <= 0:
        return RegimeSnapshot(
            label=MarketRegime.UNKNOWN,
            efficiency_ratio=0.0,
            range_pct=0.0,
            avg_candle_range_pct=0.0,
            lookback=cfg.lookback,
            net_return_pct=0.0,
        )

    path = 0.0
    for i in range(1, len(window_closes)):
        path += abs(window_closes[i] - window_closes[i - 1])

    net = abs(last - first)
    efficiency = (net / path) if path > 0 else 0.0
    hi = max(window_highs)
    lo = min(window_lows)
    range_pct = (hi - lo) / first
    avg_range = sum(h - lo for h, lo in zip(window_highs, window_lows, strict=True)) / len(
        window_highs
    )
    avg_range_pct = avg_range / first
    net_return_pct = (last - first) / first * 100.0

    is_chop = (
        efficiency < cfg.efficiency_max
        and range_pct >= cfg.range_pct_min
        and avg_range_pct >= cfg.avg_range_pct_min
    )
    label = MarketRegime.RANGE_CHOP if is_chop else MarketRegime.TREND

    return RegimeSnapshot(
        label=label,
        efficiency_ratio=efficiency,
        range_pct=range_pct,
        avg_candle_range_pct=avg_range_pct,
        lookback=cfg.lookback,
        net_return_pct=net_return_pct,
    )
