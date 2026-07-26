"""Unit tests for market regime classifier and V3 regime gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from xauusdt.backtest.models import Signal
from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import (
    ConfluenceConfig,
    ConfluenceStrategy,
    ScoreResult,
    make_v3_candidate_config,
    make_v3_candidate_regime_gated_config,
    make_v3_config,
)
from xauusdt.strategy.regime import MarketRegime, RegimeConfig, classify_regime


def _trend_path(
    n: int = 120, start: float = 100.0, step: float = 0.5
) -> tuple[list[float], list[float], list[float]]:
    closes = [start + i * step for i in range(n)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    return closes, highs, lows


def _chop_path(
    n: int = 120, start: float = 100.0, amplitude: float = 3.0
) -> tuple[list[float], list[float], list[float]]:
    """Wide oscillating path: low efficiency, high range."""
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for i in range(n):
        # alternate up/down swings
        phase = (i % 8) - 4
        c = start + phase * amplitude * 0.5
        closes.append(c)
        highs.append(c + amplitude * 0.4)
        lows.append(c - amplitude * 0.4)
    return closes, highs, lows


def test_classify_trend_path():
    closes, highs, lows = _trend_path()
    snap = classify_regime(closes, highs, lows, RegimeConfig(lookback=96))
    assert snap.label == MarketRegime.TREND
    assert snap.efficiency_ratio > 0.5


def test_classify_chop_path():
    closes, highs, lows = _chop_path()
    snap = classify_regime(
        closes, highs, lows, RegimeConfig(lookback=96, efficiency_max=0.30, range_pct_min=0.02)
    )
    assert snap.label == MarketRegime.RANGE_CHOP
    assert snap.efficiency_ratio < 0.30


def test_classify_unknown_when_insufficient_history():
    closes, highs, lows = _trend_path(n=10)
    snap = classify_regime(closes, highs, lows, RegimeConfig(lookback=96))
    assert snap.label == MarketRegime.UNKNOWN


def test_v1_defaults_regime_gate_off():
    cfg = ConfluenceConfig()
    assert cfg.v3_active is False
    assert cfg.v3_regime_gate is False
    assert cfg.version == "v1"


def test_default_v3_experimental_regime_gate_off():
    cfg = make_v3_config()
    assert cfg.v3_active is True
    assert cfg.v3_regime_gate is False
    assert cfg.version == "v3_experimental"


def test_v3_candidate_regime_gate_off_by_default():
    cfg = make_v3_candidate_config()
    assert cfg.version == "v3_candidate"
    assert cfg.v3_regime_gate is False
    assert cfg.v3_long_bias_penalty == 5.0
    assert cfg.v3_max_adx == 45.0


def test_v3_candidate_regime_gated_factory():
    cfg = make_v3_candidate_regime_gated_config()
    assert cfg.version == "v3_candidate_regime_gated"
    assert cfg.v3_active is True
    assert cfg.v3_regime_gate is True
    assert cfg.v3_block_range_chop is True
    assert cfg.v3_long_bias_penalty == 5.0
    assert cfg.v3_max_adx == 45.0
    assert cfg.v3_reject_toxic_score is False
    assert cfg.improved_exit is True


def _fake_features(*, adx: float = 25.0):
    return SimpleNamespace(
        adx_14=SimpleNamespace(valid=True, adx_value=adx),
        ema_9=SimpleNamespace(valid=True, ema_value=101.0),
        ema_21=SimpleNamespace(valid=True, ema_value=100.0),
        structure=SimpleNamespace(valid=True),
    )


def _candle(
    i: int = 0, close: float = 100.0, high: float | None = None, low: float | None = None
) -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i),
        open=close,
        high=high if high is not None else close + 1.0,
        low=low if low is not None else close - 1.0,
        close=close,
        volume=10.0,
    )


def test_regime_gate_blocks_entry_in_chop(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_regime_gated_config())
    # Seed choppy history so classifier has enough bars
    closes, highs, lows = _chop_path(n=120)
    for i, (c, h, lo) in enumerate(zip(closes, highs, lows, strict=True)):
        strategy._history.append(_candle(i=i, close=c, high=h, low=lo))

    monkeypatch.setattr(strategy, "_compute_features", lambda: _fake_features(adx=25.0))
    monkeypatch.setattr(
        strategy,
        "_calculate_scores",
        lambda candle, features: ScoreResult(
            buy_score=10.0, sell_score=90.0, buy_reasons=["x"], sell_reasons=["y"]
        ),
    )
    signal = strategy.on_candle(_candle(i=120, close=closes[-1]), None)
    assert signal == Signal.HOLD
    reasons = strategy.get_last_rejection_reasons()
    assert any("v3_regime_range_chop" in r for r in reasons)
    snap = strategy.get_last_regime()
    assert snap is not None
    assert snap.label == MarketRegime.RANGE_CHOP


def test_regime_gate_allows_entry_in_trend(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_regime_gated_config())
    closes, highs, lows = _trend_path(n=120)
    for i, (c, h, lo) in enumerate(zip(closes, highs, lows, strict=True)):
        strategy._history.append(_candle(i=i, close=c, high=h, low=lo))

    monkeypatch.setattr(strategy, "_compute_features", lambda: _fake_features(adx=25.0))
    monkeypatch.setattr(
        strategy,
        "_calculate_scores",
        lambda candle, features: ScoreResult(
            buy_score=10.0, sell_score=90.0, buy_reasons=["x"], sell_reasons=["y"]
        ),
    )
    signal = strategy.on_candle(_candle(i=120, close=closes[-1]), None)
    assert signal == Signal.SELL
    assert strategy.get_last_rejection_reasons() == []


def test_ungated_candidate_still_enters_in_chop(monkeypatch):
    """Baseline v3_candidate must remain unchanged (gate off)."""
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    closes, highs, lows = _chop_path(n=120)
    for i, (c, h, lo) in enumerate(zip(closes, highs, lows, strict=True)):
        strategy._history.append(_candle(i=i, close=c, high=h, low=lo))

    monkeypatch.setattr(strategy, "_compute_features", lambda: _fake_features(adx=25.0))
    monkeypatch.setattr(
        strategy,
        "_calculate_scores",
        lambda candle, features: ScoreResult(
            buy_score=10.0, sell_score=90.0, buy_reasons=["x"], sell_reasons=["y"]
        ),
    )
    signal = strategy.on_candle(_candle(i=120, close=closes[-1]), None)
    assert signal == Signal.SELL
