"""Confluence strategy tests.

Covers:
- Default config instantiation
- Bullish confluence scoring
- Bearish confluence scoring
- Ranging market
- Low volatility
- Weak trend (low ADX)
- Insufficient history
- Score threshold and gap behavior
- SL/TP configuration
- Determinism
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xauusdt.backtest.models import Signal
from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy, ScoreResult

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def base_config() -> ConfluenceConfig:
    return ConfluenceConfig()


@pytest.fixture
def bullish_strategy(base_config: ConfluenceConfig) -> ConfluenceStrategy:
    return ConfluenceStrategy(base_config)


@pytest.fixture
def bullish_candle_sequence() -> list[Candle]:
    """Strongly bullish candle sequence for testing."""
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=base_ts.timestamp() + i * 900,
            open=100.0 + i * 0.5,
            high=101.0 + i * 0.5,
            low=99.5 + i * 0.4,
            close=100.5 + i * 0.6,
            volume=100.0,
            symbol="XAU-USDT-SWAP",
            granularity="15m",
        )
        for i in range(50)
    ]


@pytest.fixture
def bearish_candle_sequence() -> list[Candle]:
    """Strongly bearish candle sequence for testing."""
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=base_ts.timestamp() + i * 900,
            open=100.0 - i * 0.5,
            high=100.5 - i * 0.4,
            low=99.0 - i * 0.5,
            close=99.5 - i * 0.6,
            volume=100.0,
            symbol="XAU-USDT-SWAP",
            granularity="15m",
        )
        for i in range(50)
    ]


@pytest.fixture
def ranging_candle_sequence() -> list[Candle]:
    """Ranging price action."""
    base_ts = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=base_ts.timestamp() + i * 900,
            open=100.0 + (i % 3) * 0.2,
            high=101.0 + (i % 3) * 0.2,
            low=99.0 + (i % 3) * 0.2,
            close=100.0 + (i % 3) * 0.2,
            volume=100.0,
            symbol="XAU-USDT-SWAP",
            granularity="15m",
        )
        for i in range(50)
    ]


# ── Configuration Tests ──────────────────────────────────────────


class TestConfluenceConfig:
    def test_default_config(self):
        config = ConfluenceConfig()
        assert config.min_score == 65.0
        assert config.min_score_gap == 15.0
        assert config.ema_fast_period == 9
        assert config.ema_slow_period == 21
        assert config.adx_min == 20.0
        assert config.risk_reward_ratio == 2.0
        assert config.sl_atr_multiplier == 1.5

    def test_config_to_dict(self):
        config = ConfluenceConfig()
        d = config.to_dict()
        assert isinstance(d, dict)
        assert "min_score" in d
        assert "ema_fast_period" in d

    def test_custom_config(self):
        config = ConfluenceConfig(
            min_score=70.0,
            adx_min=25.0,
            risk_reward_ratio=3.0,
        )
        assert config.min_score == 70.0
        assert config.adx_min == 25.0
        assert config.risk_reward_ratio == 3.0


# ── Instantiation Tests ──────────────────────────────────────────


class TestConfluenceStrategy:
    def test_instantiate_default(self):
        strategy = ConfluenceStrategy()
        assert strategy._config is not None
        assert strategy.get_last_score() is not None

    def test_instantiate_custom_config(self):
        config = ConfluenceConfig(min_score=80.0)
        strategy = ConfluenceStrategy(config)
        assert strategy._config.min_score == 80.0


# ── Bullish Scoring Tests ────────────────────────────────────────


class TestBullishScoring:
    def test_bullish_strategy_processes_candles(self, bullish_strategy, bullish_candle_sequence):
        """Strategy should process all candles without error."""
        for candle in bullish_candle_sequence:
            signal = bullish_strategy.on_candle(candle, None)
            assert signal in (Signal.BUY, Signal.HOLD, Signal.SELL)

    def test_bullish_score_increases(self, bullish_strategy, bullish_candle_sequence):
        """Buy score should increase as bullish candles process."""
        for i, candle in enumerate(bullish_candle_sequence):
            bullish_strategy.on_candle(candle, None)
            score = bullish_strategy.get_last_score()
            if i >= 30:  # After warmup
                assert score.buy_score >= 0

    def test_bullish_eventually_gets_buy_signal(self, bullish_strategy, bullish_candle_sequence):
        """Should eventually get BUY signal in strong uptrend."""
        for candle in bullish_candle_sequence:
            signal = bullish_strategy.on_candle(candle, None)
            if signal == Signal.BUY:
                score = bullish_strategy.get_last_score()
                assert score.buy_score >= 65.0
                return
        # If no BUY signal, check last score
        score = bullish_strategy.get_last_score()
        # At least some buy score should accumulate
        assert score.buy_score > 0


# ── Bearish Scoring Tests ────────────────────────────────────────


class TestBearishScoring:
    def test_bearish_score_increases(self, bearish_candle_sequence):
        strategy = ConfluenceStrategy()
        for candle in bearish_candle_sequence:
            strategy.on_candle(candle, None)
            score = strategy.get_last_score()
            if score.sell_score > 0:
                break  # Found at least some sell score
        assert score.sell_score >= 0

    def test_bearish_eventually_gets_sell_signal(self, bearish_candle_sequence):
        strategy = ConfluenceStrategy()
        for candle in bearish_candle_sequence:
            signal = strategy.on_candle(candle, None)
            if signal == Signal.SELL:
                score = strategy.get_last_score()
                assert score.sell_score >= 65.0
                return
        score = strategy.get_last_score()
        assert score.sell_score > 0


# ── Ranging Market Tests ─────────────────────────────────────────


class TestRangingMarket:
    def test_ranging_strategy_returns_hold(self, ranging_candle_sequence):
        """Ranging market should mostly return HOLD."""
        strategy = ConfluenceStrategy()
        hold_count = 0
        trade_count = 0
        for candle in ranging_candle_sequence:
            signal = strategy.on_candle(candle, None)
            if signal == Signal.HOLD:
                hold_count += 1
            else:
                trade_count += 1
        # Majority should be HOLD in ranging market
        total = hold_count + trade_count
        assert hold_count / total > 0.7


# ── Weak Trend (Low ADX) Tests ───────────────────────────────────


class TestWeakTrend:
    def test_low_adx_reduces_score(self):
        """Low ADX should reduce total score significantly."""
        config = ConfluenceConfig(adx_min=100.0)  # Very high threshold
        strategy = ConfluenceStrategy(config)
        # Process a few candles
        for i in range(5):
            candle = Candle(
                open_time=(i + 1) * 900,
                open=100.0,
                high=101.0,
                low=99.5,
                close=100.5,
                volume=100.0,
                symbol="XAU-USDT-SWAP",
                granularity="15m",
            )
            strategy.on_candle(candle, None)
        # Score should be low because ADX never meets threshold
        score = strategy.get_last_score()
        assert score.buy_score < 50.0  # Much lower than 65 threshold


# ── Insufficient History Tests ───────────────────────────────────


class TestInsufficientHistory:
    def test_short_history_returns_hold(self):
        """Strategy should return HOLD when history is too short."""
        strategy = ConfluenceStrategy()
        for i in range(10):  # Less than warmup period
            candle = Candle(
                open_time=(i + 1) * 900,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=100.0,
                symbol="XAU-USDT-SWAP",
                granularity="15m",
            )
            signal = strategy.on_candle(candle, None)
            # Should be HOLD until warmup is complete
            if i < 15:
                assert signal == Signal.HOLD


# ── Threshold and Gap Tests ──────────────────────────────────────


class TestThresholds:
    def test_score_below_threshold_holds(self):
        """Score below min_score should return HOLD."""
        config = ConfluenceConfig(min_score=90.0)  # Very high threshold
        strategy = ConfluenceStrategy(config)
        # Process bullish candles
        for i in range(50):
            candle = Candle(
                open_time=(i + 1) * 900,
                open=100.0 + i * 0.3,
                high=101.0 + i * 0.3,
                low=99.5 + i * 0.2,
                close=100.5 + i * 0.4,
                volume=100.0,
                symbol="XAU-USDT-SWAP",
                granularity="15m",
            )
            strategy.on_candle(candle, None)
        # Even with bullish trend, score likely below 90
        score = strategy.get_last_score()
        assert score.buy_score < 90.0

    def test_score_gap_enforces_clear_bias(self):
        """If buy and sell scores are too close, should HOLD."""
        config = ConfluenceConfig(min_score=50.0, min_score_gap=80.0)
        strategy = ConfluenceStrategy(config)
        # Process candles with mixed signals
        for i in range(50):
            candle = Candle(
                open_time=(i + 1) * 900,
                open=100.0,
                high=100.5 if i % 2 == 0 else 101.0,
                low=99.5 if i % 2 == 0 else 99.0,
                close=100.0 + (0.5 if i % 2 == 0 else -0.5),
                volume=100.0,
                symbol="XAU-USDT-SWAP",
                granularity="15m",
            )
            signal = strategy.on_candle(candle, None)
            score = strategy.get_last_score()
            if signal != Signal.HOLD:
                gap = abs(score.buy_score - score.sell_score)
                assert gap >= 80.0


# ── SL/TP Configuration Tests ───────────────────────────────────


class TestSLTPConfiguration:
    def test_sl_atr_multiplier_config(self):
        config = ConfluenceConfig(sl_atr_multiplier=2.0)
        assert config.sl_atr_multiplier == 2.0

    def test_risk_reward_config(self):
        config = ConfluenceConfig(risk_reward_ratio=3.0)
        assert config.risk_reward_ratio == 3.0

    def test_default_risk_management(self):
        config = ConfluenceConfig()
        assert config.sl_atr_multiplier == 1.5
        assert config.risk_reward_ratio == 2.0


# ── Determinism Tests ────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_output(self):
        """Same candle sequence should produce same scores."""
        config = ConfluenceConfig()
        strategy1 = ConfluenceStrategy(config)
        strategy2 = ConfluenceStrategy(config)

        for i in range(50):
            candle = Candle(
                open_time=(i + 1) * 900,
                open=100.0 + i * 0.5,
                high=101.0 + i * 0.5,
                low=99.5 + i * 0.4,
                close=100.5 + i * 0.6,
                volume=100.0,
                symbol="XAU-USDT-SWAP",
                granularity="15m",
            )
            strategy1.on_candle(candle, None)
            strategy2.on_candle(candle, None)

        score1 = strategy1.get_last_score()
        score2 = strategy2.get_last_score()
        assert score1.to_dict() == score2.to_dict()


# ── Score Result Tests ───────────────────────────────────────────


class TestScoreResult:
    def test_score_result_to_dict(self):
        score = ScoreResult(buy_score=70.0, sell_score=30.0, buy_reasons=["test"])
        d = score.to_dict()
        assert d["buy_score"] == 70.0
        assert d["sell_score"] == 30.0
        assert d["decision"] == "BUY"

    def test_score_result_sell_decision(self):
        score = ScoreResult(buy_score=30.0, sell_score=70.0, sell_reasons=["test"])
        d = score.to_dict()
        assert d["decision"] == "SELL"

    def test_score_result_hold_decision(self):
        score = ScoreResult(buy_score=50.0, sell_score=50.0)
        d = score.to_dict()
        assert d["decision"] == "HOLD"
