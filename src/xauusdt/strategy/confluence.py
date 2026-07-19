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

    # Engine limits (if applied)
    sl_atr_multiplier: float = 1.5
    risk_reward_ratio: float = 2.0

    # Improved Exit Model (PROJECT-STRATEGY-003)
    improved_exit: bool = False  # Set to True to enable partial TP and break-even SL
    partial_tp_ratio: float = 0.5  # Fraction of position to close on partial TP
    partial_tp_rr: float = 1.0  # R-multiple target for partial TP and BE trigger

    # Sensitivity/Ablation Parameters (defaults match v1 spec)
    swing_lookback: int = 10
    weight_ema: float = 20.0
    weight_price_ema: float = 10.0
    weight_adx: float = 15.0
    weight_structure: float = 20.0
    weight_swing: float = 10.0
    weight_atr: float = 10.0
    weight_candle: float = 5.0

    # V2 quality filters (all disabled in v1, configurable for v2)
    adx_rising: bool = False  # Require ADX to be increasing (trend strengthening)
    ema_slope_alignment: bool = False  # Require EMA(50) slope aligned with trade direction

    # V3 Experimental Filters (PROJECT-STRATEGY-004)
    v3_active: bool = False
    v3_reject_toxic_score: bool = False  # Reject scores in [75, 84]
    v3_toxic_score_min: float = 75.0
    v3_toxic_score_max: float = 84.0
    v3_min_adx: float = 15.0  # Filter out dead markets (ADX < 15)
    v3_max_adx: float = 40.0  # Filter out late trend whipsaws (ADX > 40)
    v3_long_bias_penalty: float = 0.0  # Penalty to LONG score to reflect V1 diagnostic bias

    # Version label for report identification
    version: str = "v1"

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
    """Confluence scoring strategy (v1/v2/v3 via config).

    Combines multiple technical indicators into a weighted score
    for BUY and SELL directions. Returns signals based on thresholds.

    V3 experimental entry-quality filters (PROJECT-STRATEGY-004) are opt-in
    via ``ConfluenceConfig.v3_active`` and do not alter v1/v2 defaults.
    """

    def __init__(self, config: ConfluenceConfig | None = None) -> None:
        self._config = config or ConfluenceConfig()
        self._last_score: ScoreResult = ScoreResult(0.0, 0.0)
        self._history: list[Candle] = []
        self._prev_adx: float | None = None
        self._prev_ema_9: float | None = None
        # Structured rejection reasons for v3 diagnostics (last evaluation only)
        self._last_rejection_reasons: list[str] = []

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
        signal = self._decide(score, position, features)
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

        # Cache previous candle features for V2 filters
        if len(features_list) >= 2 and features_list[-2] is not None:
            self._prev_adx = (
                features_list[-2].adx_14.adx_value if features_list[-2].adx_14.valid else None
            )
            self._prev_ema_9 = (
                features_list[-2].ema_9.ema_value if features_list[-2].ema_9.valid else None
            )
        else:
            self._prev_adx = None
            self._prev_ema_9 = None

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

    def _decide(
        self,
        score: ScoreResult,
        position: BacktestPosition | None,
        features: CandleFeatures,
    ) -> Signal:
        """Decide signal based on scores, thresholds, and quality filters."""
        self._last_rejection_reasons = []
        gap = abs(score.buy_score - score.sell_score)

        # If we have a position, we only look for close signals
        if position is not None:
            # If opposite score exceeds ours significantly, consider exit
            if position.side.name == "LONG" and score.sell_score > score.buy_score + 10:
                return Signal.SELL  # Close long
            if position.side.name == "SHORT" and score.buy_score > score.sell_score + 10:
                return Signal.BUY  # Close short
            return Signal.HOLD

        # Apply optional long-side score penalty (v3 direction bias)
        buy_score = score.buy_score
        if self._config.v3_active and self._config.v3_long_bias_penalty > 0:
            buy_score = score.buy_score - self._config.v3_long_bias_penalty

        # No position: evaluate entry with V2 then V3 quality filters
        if buy_score >= self._config.min_score and gap >= self._config.min_score_gap:
            if not self._apply_v2_quality(score, features, "buy"):
                self._last_rejection_reasons.append("v2_quality_failed:buy")
                return Signal.HOLD
            if not self._apply_v3_quality(buy_score, features, "buy"):
                return Signal.HOLD
            return Signal.BUY
        if score.sell_score >= self._config.min_score and gap >= self._config.min_score_gap:
            if not self._apply_v2_quality(score, features, "sell"):
                self._last_rejection_reasons.append("v2_quality_failed:sell")
                return Signal.HOLD
            if not self._apply_v3_quality(score.sell_score, features, "sell"):
                return Signal.HOLD
            return Signal.SELL

        return Signal.HOLD

    def _apply_v2_quality(self, score: ScoreResult, features: CandleFeatures, side: str) -> bool:
        """Apply Strategy V2 quality filters.

        Returns True if the signal passes all configured v2 filters, False to reject.
        """
        # ADX rising filter: requires ADX to be strictly greater than the previous candle's ADX
        if self._config.adx_rising and features.adx_14.valid:
            if self._prev_adx is None or features.adx_14.adx_value <= self._prev_adx:
                return False

        # EMA slope alignment filter
        if self._config.ema_slope_alignment and features.ema_9.valid:
            if self._prev_ema_9 is None:
                return False

            slope_rising = features.ema_9.ema_value > self._prev_ema_9
            if side == "buy" and not slope_rising:
                return False
            if side == "sell" and slope_rising:
                return False

        return True

    def _apply_v3_quality(
        self, effective_score: float, features: CandleFeatures, side: str
    ) -> bool:
        """Apply experimental V3 entry-quality filters (PROJECT-STRATEGY-004).

        Evidence basis: BACKTEST-010 entry diagnostics.
        Returns True if the signal passes all active v3 filters.
        Rejection reasons are recorded in ``_last_rejection_reasons``.
        """
        if not self._config.v3_active:
            return True

        reasons: list[str] = []

        # F1: Toxic score zone rejection (experimental — possible late-entry proxy)
        if self._config.v3_reject_toxic_score:
            lo = self._config.v3_toxic_score_min
            hi = self._config.v3_toxic_score_max
            if lo <= effective_score <= hi:
                reasons.append(f"v3_toxic_score_zone:{effective_score:.1f}_in_[{lo:.0f},{hi:.0f}]")

        # F2/F4: ADX band filter
        if features.adx_14.valid:
            adx = features.adx_14.adx_value
            if adx < self._config.v3_min_adx:
                reasons.append(f"v3_adx_below_min:{adx:.1f}<{self._config.v3_min_adx:.0f}")
            if adx > self._config.v3_max_adx:
                reasons.append(f"v3_adx_above_max:{adx:.1f}>{self._config.v3_max_adx:.0f}")

        # F3: Optional long-side block when penalty pushes below threshold is
        # handled via score adjustment in _decide. Additional explicit long skip:
        if side == "buy" and self._config.v3_long_bias_penalty >= 100.0:
            # Extreme penalty acts as hard disable for LONG entries
            reasons.append("v3_long_entries_disabled")

        if reasons:
            self._last_rejection_reasons.extend(reasons)
            return False
        return True

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

    def get_last_rejection_reasons(self) -> list[str]:
        """Return structured rejection reasons from the last entry evaluation.

        Empty when the last candle produced a trade signal or was not an entry
        candidate. Used by diagnostics and unit tests for v3 filters.
        """
        return list(self._last_rejection_reasons)


def make_v3_config(**overrides: Any) -> ConfluenceConfig:
    """Factory for experimental ConfluenceStrategy v3 config.

    Enables BACKTEST-010 evidence-based entry filters without changing v1/v2
    defaults. All filters remain configurable; toxic-zone rejection is
    experimental (may proxy late-entry rather than true score quality).

    Default v3 profile (when not overridden):
    - v3_active=True
    - toxic score zone rejection [75, 84]
    - ADX band [15, 40]
    - long bias penalty = 0 (disabled; enable via override if desired)
    - improved_exit=True (compatible with STRATEGY-003)
    - version=\"v3_experimental\"
    """
    defaults: dict[str, Any] = {
        "version": "v3_experimental",
        "v3_active": True,
        "v3_reject_toxic_score": True,
        "v3_toxic_score_min": 75.0,
        "v3_toxic_score_max": 84.0,
        "v3_min_adx": 15.0,
        "v3_max_adx": 40.0,
        "v3_long_bias_penalty": 0.0,
        "improved_exit": True,
        "ema_fast_period": 50,
        "ema_slow_period": 200,
        "min_score": 65.0,
        "sl_atr_multiplier": 2.0,
        "risk_reward_ratio": 2.0,
    }
    defaults.update(overrides)
    return ConfluenceConfig(**defaults)
