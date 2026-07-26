"""Unit tests for STRATEGY-007 context filters (s7a/s7b/s7c)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from xauusdt.backtest.models import Signal
from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import (
    ConfluenceConfig,
    ConfluenceStrategy,
    ScoreResult,
    make_v3_candidate_config,
    make_v3_candidate_s7a_config,
    make_v3_candidate_s7b_config,
    make_v3_candidate_s7c_config,
    make_v3_config,
)


def _fake_features(*, adx: float = 25.0, ema9: float = 101.0, ema21: float = 100.0):
    return SimpleNamespace(
        adx_14=SimpleNamespace(valid=True, adx_value=adx),
        ema_9=SimpleNamespace(valid=True, ema_value=ema9),
        ema_21=SimpleNamespace(valid=True, ema_value=ema21),
        structure=SimpleNamespace(valid=True),
    )


def _candle() -> Candle:
    return Candle(
        symbol="XAU-USDT-SWAP",
        granularity="15m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )


def _force_entry(
    strategy: ConfluenceStrategy,
    monkeypatch,
    *,
    adx: float,
    buy: float,
    sell: float,
    ema9: float = 101.0,
    ema21: float = 100.0,
) -> None:
    monkeypatch.setattr(
        strategy, "_compute_features", lambda: _fake_features(adx=adx, ema9=ema9, ema21=ema21)
    )
    monkeypatch.setattr(
        strategy,
        "_calculate_scores",
        lambda candle, features: ScoreResult(
            buy_score=buy, sell_score=sell, buy_reasons=["x"], sell_reasons=["y"]
        ),
    )


def test_baseline_v3_candidate_s7_flags_off():
    cfg = make_v3_candidate_config()
    assert cfg.version == "v3_candidate"
    assert cfg.v3_reject_toxic_score is False
    assert cfg.v3_block_long_ema_up is False
    assert cfg.v3_long_bias_penalty == 5.0
    assert cfg.v3_max_adx == 45.0


def test_v1_and_default_v3_block_long_ema_up_off():
    assert ConfluenceConfig().v3_block_long_ema_up is False
    assert make_v3_config().v3_block_long_ema_up is False


def test_s7a_factory_enables_toxic_only():
    cfg = make_v3_candidate_s7a_config()
    assert cfg.version == "v3_candidate_s7a"
    assert cfg.v3_reject_toxic_score is True
    assert cfg.v3_toxic_score_min == 75.0
    assert cfg.v3_toxic_score_max == 84.0
    assert cfg.v3_block_long_ema_up is False
    assert cfg.v3_long_bias_penalty == 5.0
    assert cfg.v3_max_adx == 45.0


def test_s7b_factory_enables_long_ema_up_only():
    cfg = make_v3_candidate_s7b_config()
    assert cfg.version == "v3_candidate_s7b"
    assert cfg.v3_reject_toxic_score is False
    assert cfg.v3_block_long_ema_up is True


def test_s7c_factory_enables_both():
    cfg = make_v3_candidate_s7c_config()
    assert cfg.version == "v3_candidate_s7c"
    assert cfg.v3_reject_toxic_score is True
    assert cfg.v3_block_long_ema_up is True


def test_s7a_rejects_toxic_sell(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7a_config())
    # sell 80 in toxic zone; gap ok; adx ok
    _force_entry(strategy, monkeypatch, adx=25.0, buy=10.0, sell=80.0, ema9=99.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_toxic_score_zone" in r for r in strategy.get_last_rejection_reasons())


def test_s7a_allows_non_toxic_sell(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7a_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=10.0, sell=90.0, ema9=99.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.SELL
    assert strategy.get_last_rejection_reasons() == []


def test_baseline_allows_toxic_sell(monkeypatch):
    """v3_candidate keeps toxic reject OFF (STRATEGY-005)."""
    strategy = ConfluenceStrategy(make_v3_candidate_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=10.0, sell=80.0, ema9=99.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.SELL


def test_s7b_blocks_long_when_ema_up(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7b_config())
    # buy effective = 90 - 5 = 85 >= 65; ema9 > ema21 → block
    _force_entry(strategy, monkeypatch, adx=25.0, buy=90.0, sell=10.0, ema9=101.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_block_long_ema_up" in r for r in strategy.get_last_rejection_reasons())


def test_s7b_allows_long_when_ema_down(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7b_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=90.0, sell=10.0, ema9=99.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.BUY
    assert strategy.get_last_rejection_reasons() == []


def test_s7b_does_not_block_short_when_ema_up(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7b_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=10.0, sell=90.0, ema9=101.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.SELL


def test_s7c_blocks_long_ema_up_even_outside_toxic(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7c_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=95.0, sell=10.0, ema9=101.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_block_long_ema_up" in r for r in strategy.get_last_rejection_reasons())


def test_s7c_blocks_toxic_sell(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_candidate_s7c_config())
    _force_entry(strategy, monkeypatch, adx=25.0, buy=10.0, sell=80.0, ema9=99.0, ema21=100.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_toxic_score_zone" in r for r in strategy.get_last_rejection_reasons())
