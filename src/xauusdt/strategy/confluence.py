"""Confluence scoring strategy v1.

Deterministic, explainable strategy that combines:
- EMA trend bias (fast vs slow)
- ADX trend strength
- ATR volatility context
- Market structure classification
- Swing high/low context
- Candle direction

Scoring is additive and threshold-based:
- BUY when buy_score >= threshold AND score_gap >= gap_threshold
- SELL when sell_score >= threshold AND score_gap >= gap_threshold
- HOLD otherwise

Risk management:
- SL = ATR × multiplier (set via strategy on_candle, engine applies)
- TP = risk × reward_ratio distance from entry

No lookahead bias. No optimization. No ML.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from xauusdt.backtest.models import BacktestPosition, Signal
from xauusdt.exchange.models import Candle
from xauusdt.features.engine import (  # type: ignore[attr-defined]
    CandleFeatures,
    compute_all_features,
)
from xauusdt.features.models import MarketStructure

log = logging.getLogger(__name__)


@dataclass
class ConfluenceConfig:
    """Configuration for the confluence scoring strategy."""

    # Score thresholds
    min_score: float = 65.0
    min_score_gap: float = 15.0

    # EMA parameters
    ema_fast_period: int = 9
    ema_slow_period: int = 21

    # ADX threshold
    adx_min: float = 20.0

    # ATR volatility bounds (in price units, not ratio)
    atr_min: float = 0.0  # 0 = no minimum
    atr_max: float = 50.0  # cap at 50 price units

    # Risk management
    sl_atr_multiplier: float = 1.5
    risk_reward_ratio: float = 2.0

    # Sensitivity/Ablation Parameters (defaults match v1 spec)
    swing_lookback: int = 10
    weight_ema: float = 20.0
    weight_price_ema: float = 10.0
    weight_adx: float = 15.0
    weight_structure: float = 20.0
    weight_swing: float = 10.0
    weight_atr: float = 10.0
    weight_candle: float = 5.0

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass
class ScoreResult:
    """Score breakdown for a single candle."""

    buy_score: float
    sell_score: float
    buy_reasons: list[str] = field(default_factory=list)
    sell_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "buy_score": self.buy_score,
            "sell_score": self.sell_score,
            "buy_reasons": self.buy_reasons,
            "sell_reasons": self.sell_reasons,
            "decision": "BUY"
            if self.buy_score > self.sell_score
            else ("SELL" if self.sell_score > self.buy_score else "HOLD"),
        }


class ConfluenceStrategy:
    """Confluence scoring strategy v1.

    Combines multiple technical indicators into a weighted score
    for BUY and SELL directions. Returns signals based on thresholds.
    """

    def __init__(self, config: ConfluenceConfig | None = None) -> None:
        self._config = config or ConfluenceConfig()
        self._last_score: ScoreResult = ScoreResult(0.0, 0.0)
        self._history: list[Candle] = []

    def on_candle(self, candle: Candle, position: BacktestPosition | None) -> Signal:
        """Called on each candle.

        Computes confluence scores and returns signal based on thresholds.
        If in position: checks SL/TP context (engine handles actual execution).
        If no position: evaluates for entry signal.
        """
        # Add to history for feature calculation
        self._history.append(candle)

        # Compute features up to current index
        features = self._compute_features()
        if features is None:
            return Signal.HOLD

        # Check if we need to close position
        if position is not None:
            signal = self._check_exit(candle, position, features)
            if signal != Signal.HOLD:
                return signal

        # Calculate scores
        score = self._calculate_scores(candle, features)
        self._last_score = score

        # Decision logic
        signal = self._decide(score, position)
        return signal

    def _compute_features(self) -> CandleFeatures | None:
        """Compute features from candle history."""
        # Limit history to prevent O(N^2) slowdown during long backtests
        max_history = max(self._config.ema_slow_period * 2, 400)
        if len(self._history) > max_history:
            self._history = self._history[-max_history:]

        if len(self._history) < self._config.ema_slow_period + 10:
            return None

        cfg = self._config
        highs = [c.high for c in self._history]
        lows = [c.low for c in self._history]
        closes = [c.close for c in self._history]

        features_list = compute_all_features(
            highs,
            lows,
            closes,
            ema_short=cfg.ema_fast_period,
            ema_long=cfg.ema_slow_period,
            atr_period=14,
            adx_period=14,
            swing_left=2,
            swing_right=2,
        )

        features = features_list[-1]
        assert features is not None
        return features

    def _calculate_scores(self, candle: Candle, features: CandleFeatures) -> ScoreResult:
        """Calculate buy_score and sell_score with reasons."""
        buy_reasons: list[str] = []
        sell_reasons: list[str] = []

        # 1. EMA trend bias (max +20 each)
        if features.ema_9.valid and features.ema_21.valid:
            if features.ema_9.ema_value > features.ema_21.ema_value:
                buy_reasons.append("EMA fast > slow")
            if features.ema_9.ema_value < features.ema_21.ema_value:
                sell_reasons.append("EMA fast < slow")

        # 2. Price vs EMA slow (max +10 each)
        price = candle.close
        if features.ema_21.valid:
            if price > features.ema_21.ema_value:
                buy_reasons.append("Price above EMA slow")
            if price < features.ema_21.ema_value:
                sell_reasons.append("Price below EMA slow")

        # 3. ADX trend strength (max +15 each)
        if features.adx_14.valid:
            if features.adx_14.adx_value >= self._config.adx_min:
                buy_reasons.append(f"ADX strong ({features.adx_14.adx_value:.1f})")
                sell_reasons.append(f"ADX strong ({features.adx_14.adx_value:.1f})")

        # 4. Market structure (max +20 each)
        if features.structure.valid:
            if features.structure.structure == MarketStructure.BULLISH:
                buy_reasons.append("Structure bullish")
            elif features.structure.structure == MarketStructure.BEARISH:
                sell_reasons.append("Structure bearish")

        # 5. Swing context (max +weight_swing each)
        if (
            features.structure.swing_low
            and features.structure.swing_low.index
            >= len(self._history) - self._config.swing_lookback
        ):
            buy_reasons.append("Recent swing low support")
        if (
            features.structure.swing_high
            and features.structure.swing_high.index
            >= len(self._history) - self._config.swing_lookback
        ):
            sell_reasons.append("Recent swing high resistance")

        # 6. ATR volatility (max +10 each)
        if features.atr_14.valid:
            atr_val = features.atr_14.atr_value
            if self._config.atr_min <= atr_val <= self._config.atr_max:
                buy_reasons.append(f"ATR normal ({atr_val:.1f})")
                sell_reasons.append(f"ATR normal ({atr_val:.1f})")

        # 7. Candle direction (max +5 each)
        if candle.close > candle.open:
            buy_reasons.append("Bullish candle")
        elif candle.close < candle.open:
            sell_reasons.append("Bearish candle")

        # Calculate scores (weighted)
        buy_score = self._score_from_reasons(
            buy_reasons, len(self._history) > self._config.ema_slow_period
        )
        sell_score = self._score_from_reasons(
            sell_reasons, len(self._history) > self._config.ema_slow_period
        )

        return ScoreResult(
            buy_score=buy_score,
            sell_score=sell_score,
            buy_reasons=buy_reasons,
            sell_reasons=sell_reasons,
        )

    def _score_from_reasons(self, reasons: list[str], has_history: bool) -> float:
        """Calculate numeric score from reasons list.

        Each reason contributes a weighted score.
        Returns 0 if insufficient history.
        """
        if not has_history:
            return 0.0

        # Fixed weights mapping from config
        weights: dict[str, float] = {
            "EMA fast > slow": self._config.weight_ema,
            "EMA fast < slow": self._config.weight_ema,
            "Price above EMA slow": self._config.weight_price_ema,
            "Price below EMA slow": self._config.weight_price_ema,
            "Structure bullish": self._config.weight_structure,
            "Structure bearish": self._config.weight_structure,
            "Bullish candle": self._config.weight_candle,
            "Bearish candle": self._config.weight_candle,
        }
        score = 0.0
        for r in reasons:
            if "swing" in r.lower():
                score += self._config.weight_swing
            elif r.startswith("ADX strong"):
                score += self._config.weight_adx
            elif r.startswith("ATR normal"):
                score += self._config.weight_atr
            else:
                score += weights.get(r, 0.0)

        return min(score, 100.0)  # Cap at 100

    def _decide(self, score: ScoreResult, position: BacktestPosition | None) -> Signal:
        """Decide signal based on scores and thresholds."""
        gap = abs(score.buy_score - score.sell_score)

        # If we have a position, we only look for close signals
        if position is not None:
            # If opposite score exceeds ours significantly, consider exit
            if position.side.name == "LONG" and score.sell_score > score.buy_score + 10:
                return Signal.SELL  # Close long
            if position.side.name == "SHORT" and score.buy_score > score.sell_score + 10:
                return Signal.BUY  # Close short
            return Signal.HOLD

        # No position: evaluate entry
        if score.buy_score >= self._config.min_score and gap >= self._config.min_score_gap:
            return Signal.BUY
        if score.sell_score >= self._config.min_score and gap >= self._config.min_score_gap:
            return Signal.SELL

        return Signal.HOLD

    def _check_exit(
        self, candle: Candle, position: BacktestPosition, features: CandleFeatures
    ) -> Signal:
        """Check if we should exit current position.

        Returns SELL to close long, BUY to close short.
        """
        if position.side.name == "LONG":
            # Check if trend flipped to bearish
            if features.ema_9.valid and features.ema_21.valid:
                if features.ema_9.ema_value < features.ema_21.ema_value:
                    return Signal.SELL  # Close long
        else:
            if features.ema_9.valid and features.ema_21.valid:
                if features.ema_9.ema_value > features.ema_21.ema_value:
                    return Signal.BUY  # Close short

        return Signal.HOLD

    def get_last_score(self) -> ScoreResult:
        """Return the last calculated score for debugging/reporting."""
        return self._last_score
