import pytest

from xauusdt.features.engine import calc_ema


def test_calc_ema_regression():
    # If the bug was (values[i] - prev) * mult + prev * mult
    # Then for prices = [4800, 4801, 4802], M = 0.5
    # SMA = 4800.0 (period 1)
    # EMA2 = (4801 - 4800) * 0.5 + 4800 * 0.5 = 2400.5 (BUG)
    # Correct EMA2 = (4801 - 4800) * 0.5 + 4800 = 4800.5

    values = [4800.0, 4801.0, 4802.0]
    # Period 2 -> M = 2/3 = 0.6666
    ema = calc_ema(values, period=2)

    assert ema[0] is None
    assert ema[1] == pytest.approx(4800.5) # SMA of first 2 elements
    # EMA3 = (4802 - 4800.5) * 0.6666 + 4800.5 = 4801.5
    assert ema[2] == pytest.approx(4801.5)

    # Regression test for large values and periods where the bug caused extreme drops
    closes = [4800.0] * 100
    ema_50 = calc_ema(closes, period=50)
    # For a flat line, EMA must equal the line value exactly
    assert ema_50[-1] == pytest.approx(4800.0)

    # The buggy code would return 4800 * (2/51) = 188.23!
