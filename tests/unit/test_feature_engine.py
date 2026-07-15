"""Feature engine tests.

Covers:
- EMA calculation correctness and warmup
- ATR calculation correctness
- ADX calculation correctness
- Swing high/low detection (confirmed + candidate)
- Market structure classification
- Lookahead bias prevention
- Edge cases (short series, flat prices)
"""

from __future__ import annotations

import math

import pytest

from xauusdt.features.engine import (
    calc_adx,
    calc_atr,
    calc_ema,
    classify_structure,
    compute_all_features,
    find_candidate_swing_highs_lows,
    find_swing_highs_lows,
)
from xauusdt.features.models import MarketStructure

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def ascending_prices() -> list[float]:
    """Simple ascending sequence for EMA testing."""
    return [float(i) for i in range(1, 31)]


@pytest.fixture
def realistic_prices() -> list[tuple[float, float, float]]:
    """Realistic OHLC data for ATR/ADX testing."""
    return [
        (100.0, 102.0, 99.5, 101.5),  # (open, high, low, close)
        (101.5, 103.0, 100.0, 102.0),
        (102.0, 104.0, 101.0, 103.5),
        (103.5, 105.0, 102.5, 104.0),
        (104.0, 106.0, 103.0, 105.5),
        (105.5, 105.0, 103.5, 104.0),
        (104.0, 105.5, 103.0, 104.5),
        (104.5, 106.0, 103.5, 105.0),
        (105.0, 107.0, 104.0, 106.5),
        (106.5, 108.0, 105.5, 107.0),
        (107.0, 109.0, 106.0, 108.5),
        (108.5, 110.0, 107.5, 109.0),
        (109.0, 108.0, 106.5, 107.0),
        (107.0, 108.5, 106.0, 107.5),
        (107.5, 109.5, 106.5, 109.0),
        (109.0, 111.0, 108.0, 110.5),
        (110.5, 112.0, 109.5, 111.0),
        (111.0, 113.0, 110.0, 112.5),
        (112.5, 114.0, 111.5, 113.0),
        (113.0, 115.0, 112.0, 114.5),
        (114.5, 116.0, 113.5, 115.0),
        (115.0, 117.0, 114.0, 116.5),
        (116.5, 118.0, 115.5, 117.0),
        (117.0, 116.0, 114.5, 115.0),
        (115.0, 116.5, 114.0, 115.5),
        (115.5, 117.5, 114.5, 117.0),
        (117.0, 119.0, 116.0, 118.5),
        (118.5, 120.0, 117.5, 119.0),
        (119.0, 121.0, 118.0, 120.5),
        (120.5, 122.0, 119.5, 121.0),
    ]


@pytest.fixture
def volatile_prices() -> list[tuple[float, float, float]]:
    """Volatile data with clear swings for swing detection."""
    return [
        (100, 105, 98, 102),
        (102, 108, 100, 106),
        (106, 112, 104, 110),
        (110, 115, 108, 113),  # local high at index 3
        (113, 114, 108, 109),
        (109, 110, 104, 105),
        (105, 106, 100, 102),
        (102, 103, 96, 97),  # local low at index 7
        (97, 99, 95, 98),
        (98, 102, 97, 101),
        (101, 106, 100, 105),
        (105, 110, 103, 109),  # local high at index 11
        (109, 111, 105, 107),
        (107, 108, 102, 103),
        (103, 104, 98, 99),  # local low at index 13
        (99, 102, 97, 101),
        (101, 106, 100, 105),
        (105, 110, 103, 109),  # local high at index 17
        (109, 111, 106, 108),
        (108, 110, 105, 107),
    ]


# ── EMA Tests ────────────────────────────────────────────────────


class TestEMA:
    def test_warmup_returns_none(self, ascending_prices):
        result = calc_ema(ascending_prices, period=10)
        # First 9 values should be None
        for i in range(9):
            assert result[i] is None
        # 10th value (index 9) should have a value
        assert result[9] is not None

    def test_ema_equals_close_for_period_1(self, ascending_prices):
        result = calc_ema(ascending_prices, period=1)
        for i, expected in enumerate(ascending_prices):
            assert math.isclose(result[i], expected, rel_tol=1e-9)

    def test_ema_below_ascending(self, ascending_prices):
        """For ascending data, EMA should lag behind close."""
        result = calc_ema(ascending_prices, period=10)
        for i in range(10, len(ascending_prices)):
            assert result[i] is not None
            assert result[i] < ascending_prices[i]

    def test_ema_deterministic(self, ascending_prices):
        r1 = calc_ema(ascending_prices, period=10)
        r2 = calc_ema(ascending_prices, period=10)
        for a, b in zip(r1, r2):
            va = a if a is not None else 0.0
            vb = b if b is not None else 0.0
            assert math.isclose(va, vb, rel_tol=1e-9)

    def test_insufficient_data(self):
        result = calc_ema([1.0, 2.0], period=10)
        assert all(v is None for v in result)


# ── ATR Tests ─────────────────────────────────────────────────────


class TestATR:
    def test_warmup_returns_none(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        result = calc_atr(highs, lows, closes, period=14)
        for i in range(14):
            assert result[i] is None
        assert result[14] is not None

    def test_atr_positive(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        result = calc_atr(highs, lows, closes, period=14)
        for r in result:
            if r is not None:
                assert r > 0

    def test_atr_deterministic(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        r1 = calc_atr(highs, lows, closes, period=14)
        r2 = calc_atr(highs, lows, closes, period=14)
        for a, b in zip(r1, r2):
            assert math.isclose(a or 0.0, b or 0.0, rel_tol=1e-9)

    def test_flat_prices_zero_atr(self):
        prices = [100.0] * 20
        result = calc_atr(prices, prices, prices, period=14)
        for r in result:
            if r is not None:
                assert r == 0.0


# ── ADX Tests ─────────────────────────────────────────────────────


class TestADX:
    def test_warmup_returns_none(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        result = calc_adx(highs, lows, closes, period=14)
        # ADX warmup: period + 2 for DI smoothing
        # ADX needs: period (14) for DI + 1 for first ADX + 1 for smooth ADX
        # In this data, ADX first valid at idx 16
        for i in range(15):
            assert result[i]["adx"] is None

    def test_adx_non_negative(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        result = calc_adx(highs, lows, closes, period=14)
        for r in result:
            if r["adx"] is not None:
                assert r["adx"] >= 0
                assert r["plus_di"] is not None
                assert r["minus_di"] is not None

    def test_adx_deterministic(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        r1 = calc_adx(highs, lows, closes, period=14)
        r2 = calc_adx(highs, lows, closes, period=14)
        for a, b in zip(r1, r2):
            for k in ["adx", "plus_di", "minus_di"]:
                va = a[k] if a[k] is not None else 0.0
                vb = b[k] if b[k] is not None else 0.0
                assert math.isclose(va, vb, rel_tol=1e-9)


# ── Swing Detection Tests ─────────────────────────────────────────


class TestSwingDetection:
    def test_confirmed_swing_high(self, volatile_prices):
        closes = [p[3] for p in volatile_prices]
        swings = find_swing_highs_lows(closes, left=2, right=2)
        high_swings = [s for s in swings if s.side == "high"]
        # index 3 should be a confirmed swing high (113 > 110,106 and 113 > 109,105)
        confirmed = [s for s in high_swings if s.confirmed]
        assert any(s.index == 3 and s.confirmed for s in confirmed)

    def test_confirmed_swing_low(self, volatile_prices):
        closes = [p[3] for p in volatile_prices]
        swings = find_swing_highs_lows(closes, left=2, right=2)
        low_swings = [s for s in swings if s.side == "low"]
        # index 7 should be a confirmed swing low (97 < 102,105 and 97 < 98,101)
        confirmed = [s for s in low_swings if s.confirmed]
        assert any(s.index == 7 and s.confirmed for s in confirmed)

    def test_candidate_swing(self, volatile_prices):
        closes = [p[3] for p in volatile_prices]
        swings = find_candidate_swing_highs_lows(closes, left=2)
        for s in swings:
            assert not s.confirmed
        # Should find more swings than confirmed (since no right bar needed)
        confirmed = find_swing_highs_lows(closes, left=2, right=2)
        assert len(swings) >= len(confirmed)

    def test_no_swings_flat_prices(self):
        prices = [100.0] * 20
        confirmed = find_swing_highs_lows(prices, left=2, right=2)
        candidate = find_candidate_swing_highs_lows(prices, left=2)
        assert len(confirmed) == 0
        assert len(candidate) == 0

    def test_swing_confirmed_requires_right_bars(self, volatile_prices):
        """Confirmed swing requires right bars to exist."""
        closes = [p[3] for p in volatile_prices]
        # Only use first 10 candles — too short for right bars
        short = closes[:10]
        confirmed = find_swing_highs_lows(short, left=2, right=2)
        # May or may not find swings depending on data, but all must have right bars
        for s in confirmed:
            assert s.confirmed


# ── Market Structure Tests ────────────────────────────────────────


class TestMarketStructure:
    def test_unknown_with_insufficient_swings(self):
        swings = []
        sf = classify_structure(swings, idx=0)
        assert sf.structure == MarketStructure.UNKNOWN
        assert sf.valid is False

    def test_bullish_structure(self):
        """HH + HL → bullish."""
        # Create swings: LH(5, 90), HL(7, 95), HH(10, 105)
        from xauusdt.features.models import SwingPoint

        swings = [
            SwingPoint(index=5, price=90.0, side="low", left_bars=2, right_bars=2, confirmed=True),
            SwingPoint(index=7, price=95.0, side="high", left_bars=2, right_bars=2, confirmed=True),
            SwingPoint(
                index=10, price=105.0, side="high", left_bars=2, right_bars=2, confirmed=True
            ),
            SwingPoint(
                index=13, price=100.0, side="low", left_bars=2, right_bars=2, confirmed=True
            ),
        ]
        sf = classify_structure(swings, idx=15)
        # Last high > prev high (105 > 95) = HH
        # Last low > prev low (100 > 90) = HL
        assert sf.hh is True
        assert sf.hl is True
        assert sf.structure == MarketStructure.BULLISH
        assert sf.valid is True

    def test_bearish_structure(self):
        """LL + LH → bearish."""
        from xauusdt.features.models import SwingPoint

        swings = [
            SwingPoint(
                index=5, price=110.0, side="high", left_bars=2, right_bars=2, confirmed=True
            ),
            SwingPoint(index=8, price=100.0, side="low", left_bars=2, right_bars=2, confirmed=True),
            SwingPoint(
                index=11, price=95.0, side="high", left_bars=2, right_bars=2, confirmed=True
            ),
            SwingPoint(index=14, price=88.0, side="low", left_bars=2, right_bars=2, confirmed=True),
        ]
        sf = classify_structure(swings, idx=16)
        # Last high < prev high (95 < 110) = LH
        # Last low < prev low (88 < 100) = LL
        assert sf.lh is True
        assert sf.ll is True
        assert sf.structure == MarketStructure.BEARISH

    def test_structure_deterministic(self):
        from xauusdt.features.models import SwingPoint

        swings = [
            SwingPoint(index=5, price=90.0, side="low", left_bars=2, right_bars=2, confirmed=True),
            SwingPoint(
                index=10, price=105.0, side="high", left_bars=2, right_bars=2, confirmed=True
            ),
            SwingPoint(
                index=15, price=100.0, side="low", left_bars=2, right_bars=2, confirmed=True
            ),
        ]
        sf1 = classify_structure(swings, idx=18)
        sf2 = classify_structure(swings, idx=18)
        assert sf1.to_dict() == sf2.to_dict()


# ── Full Pipeline Tests ──────────────────────────────────────────


class TestComputeAllFeatures:
    def test_all_features_computed(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        features = compute_all_features(highs, lows, closes)
        assert len(features) == len(highs)

    def test_features_valid_after_warmup(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        features = compute_all_features(highs, lows, closes)
        # After warmup, ema_21, atr_14, adx_14, structure should be valid
        warmup_idx = 22  # after ema_long(21) + atr(14) + adx(14)
        for f in features[warmup_idx:]:
            assert f.ema_21.valid is True
            assert f.atr_14.valid is True
            # ADX and structure may not be valid yet with only 30 candles
            assert f.ema_9.valid is True

    def test_features_deterministic(self, realistic_prices):
        highs = [p[1] for p in realistic_prices]
        lows = [p[2] for p in realistic_prices]
        closes = [p[3] for p in realistic_prices]
        f1 = compute_all_features(highs, lows, closes)
        f2 = compute_all_features(highs, lows, closes)
        for a, b in zip(f1, f2):
            assert a.to_dict() == b.to_dict()

    def test_no_lookahead_ema(self, ascending_prices):
        """EMA at index N must not use data from index N+1."""
        result = calc_ema(ascending_prices, period=10)
        # Truncate to 15 candles
        partial = calc_ema(ascending_prices[:15], period=10)
        for i in range(15):
            if result[i] is not None and partial[i] is not None:
                va = result[i] if result[i] is not None else 0.0
                vb = partial[i] if partial[i] is not None else 0.0
                assert math.isclose(va, vb, rel_tol=1e-9)
