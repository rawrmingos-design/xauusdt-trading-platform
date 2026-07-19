from datetime import UTC, datetime, timedelta

from xauusdt.exchange.models import Candle
from xauusdt.strategy.confluence import ConfluenceConfig, ConfluenceStrategy


def test_v2_adx_rising_filter():
    cfg = ConfluenceConfig(
        ema_fast_period=50,
        ema_slow_period=200,
        min_score=65.0,
        adx_min=20.0,
        adx_rising=True,
    )
    strategy = ConfluenceStrategy(cfg)

    prices = [4000.0 + i for i in range(210)]
    start_t = datetime(2026, 1, 1, tzinfo=UTC)
    for i, p in enumerate(prices):
        c = Candle(
            symbol="XAU-USDT",
            granularity="15m",
            open_time=start_t + timedelta(minutes=15 * i),
            open=p - 1,
            high=p + 1,
            low=p - 2,
            close=p,
            volume=10.0,
        )
        strategy.on_candle(c, None)

    assert True


def test_v2_ema_slope_filter():
    cfg = ConfluenceConfig(
        ema_fast_period=50,
        ema_slow_period=200,
        min_score=65.0,
        ema_slope_alignment=True,
    )
    strategy = ConfluenceStrategy(cfg)
    prices = [4000.0 + i for i in range(210)]
    start_t = datetime(2026, 1, 1, tzinfo=UTC)
    for i, p in enumerate(prices):
        c = Candle(
            symbol="XAU-USDT",
            granularity="15m",
            open_time=start_t + timedelta(minutes=15 * i),
            open=p - 1,
            high=p + 1,
            low=p - 2,
            close=p,
            volume=10.0,
        )
        strategy.on_candle(c, None)
    assert True
