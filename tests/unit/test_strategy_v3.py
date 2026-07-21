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
    make_v3_config,
)


def _fake_features(*, adx: float = 20.0):
    return SimpleNamespace(
        adx_14=SimpleNamespace(valid=True, adx_value=adx),
        ema_9=SimpleNamespace(valid=True, ema_value=101.0),
        ema_21=SimpleNamespace(valid=True, ema_value=100.0),
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


def _force_entry_eval(
    strategy: ConfluenceStrategy, monkeypatch, *, adx: float, buy: float, sell: float
):
    monkeypatch.setattr(strategy, "_compute_features", lambda: _fake_features(adx=adx))
    monkeypatch.setattr(
        strategy,
        "_calculate_scores",
        lambda candle, features: ScoreResult(
            buy_score=buy, sell_score=sell, buy_reasons=["x"], sell_reasons=["y"]
        ),
    )


def test_make_v3_config_defaults():
    cfg = make_v3_config()
    assert cfg.v3_active is True
    assert cfg.v3_reject_toxic_score is True
    assert cfg.v3_toxic_score_min == 75.0
    assert cfg.v3_toxic_score_max == 84.0
    assert cfg.v3_min_adx == 15.0
    assert cfg.v3_max_adx == 40.0
    assert cfg.version == "v3_experimental"


def test_v3_toxic_zone_rejects_buy(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_config())
    _force_entry_eval(strategy, monkeypatch, adx=20.0, buy=80.0, sell=10.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_toxic_score_zone" in r for r in strategy.get_last_rejection_reasons())


def test_v3_adx_min_rejects_buy(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_config(v3_reject_toxic_score=False))
    _force_entry_eval(strategy, monkeypatch, adx=12.0, buy=90.0, sell=10.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_adx_below_min" in r for r in strategy.get_last_rejection_reasons())


def test_v3_adx_max_rejects_sell(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_config(v3_reject_toxic_score=False))
    _force_entry_eval(strategy, monkeypatch, adx=45.0, buy=10.0, sell=90.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert any("v3_adx_above_max" in r for r in strategy.get_last_rejection_reasons())


def test_v3_long_direction_penalty_blocks_long(monkeypatch):
    strategy = ConfluenceStrategy(
        make_v3_config(v3_reject_toxic_score=False, v3_long_bias_penalty=100.0)
    )
    _force_entry_eval(strategy, monkeypatch, adx=20.0, buy=90.0, sell=10.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD


def test_v3_short_still_allowed_when_long_penalty_enabled(monkeypatch):
    strategy = ConfluenceStrategy(
        make_v3_config(v3_reject_toxic_score=False, v3_long_bias_penalty=100.0)
    )
    _force_entry_eval(strategy, monkeypatch, adx=20.0, buy=10.0, sell=90.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.SELL
    assert strategy.get_last_rejection_reasons() == []


def test_v1_defaults_unchanged_and_no_v3_active():
    cfg = ConfluenceConfig()
    assert cfg.v3_active is False
    assert cfg.v3_reject_toxic_score is False
    assert cfg.min_score == 65.0
    assert cfg.adx_rising is False
    assert cfg.ema_slope_alignment is False


def test_v2_filters_still_configurable_without_v3():
    cfg = ConfluenceConfig(adx_rising=True, ema_slope_alignment=True, version="v2")
    strategy = ConfluenceStrategy(cfg)
    assert strategy._config.v3_active is False
    assert strategy._config.adx_rising is True
    assert strategy._config.ema_slope_alignment is True


def test_v3_allows_clean_buy_when_filters_pass(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_config(v3_reject_toxic_score=False))
    _force_entry_eval(strategy, monkeypatch, adx=25.0, buy=90.0, sell=10.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.BUY
    assert strategy.get_last_rejection_reasons() == []


def test_v3_allows_hold_when_below_threshold_without_rejections(monkeypatch):
    strategy = ConfluenceStrategy(make_v3_config(v3_reject_toxic_score=False))
    _force_entry_eval(strategy, monkeypatch, adx=25.0, buy=60.0, sell=10.0)
    signal = strategy.on_candle(_candle(), None)
    assert signal == Signal.HOLD
    assert strategy.get_last_rejection_reasons() == []


def test_v3_can_be_instantiated_explicitly():
    strategy = ConfluenceStrategy(make_v3_config())
    assert strategy._config.version == "v3_experimental"
    assert strategy._config.v3_active is True
    assert strategy._config.improved_exit is True


def test_v3_candidate_config_matches_bt012_spec():
    cfg = make_v3_candidate_config()
    assert cfg.version == "v3_candidate"
    assert cfg.v3_active is True
    assert cfg.v3_reject_toxic_score is False
    assert cfg.v3_max_adx == 45.0
    assert cfg.v3_long_bias_penalty == 5.0
    assert cfg.improved_exit is True
    assert cfg.min_score == 65.0
    assert cfg.min_score_gap == 15.0
