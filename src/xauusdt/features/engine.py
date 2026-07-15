"""Core technical feature calculations.

All functions accept lists of OHLC float values and indices.
No pandas. No lookahead bias.
"""

from __future__ import annotations

from .models import (
    ADXFeature,
    ATRFeature,
    CandleFeatures,
    EMAFeature,
    MarketStructure,
    StructureFeature,
    SwingPoint,
)


def calc_ema(values: list[float], period: int) -> list[float | None]:
    """Calculate EMA for all indices.

    Returns list where index < period-1 is None (insufficient data).
    No lookahead: EMA[i] only uses values[0..i].
    """
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * len(values)
    multiplier = 2.0 / (period + 1)

    # Seed with SMA of first period
    sma = sum(values[:period]) / period
    result[period - 1] = sma

    # Calculate EMA forward
    for i in range(period, len(values)):
        prev = result[i - 1] if result[i - 1] is not None else sma
        if prev is not None:
            result[i] = (values[i] - prev) * multiplier + prev * multiplier

    return result


def calc_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float | None]:
    """Calculate ATR.

    True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
    ATR = SMA of TR over period.
    No lookahead.
    """
    n = len(highs)
    if n < period + 1:
        return [None] * n

    result: list[float | None] = [None] * n

    # Calculate TR for each candle
    tr: list[float] = [0.0] * n
    tr[0] = highs[0] - lows[0]

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # Seed ATR with SMA of first period TR
    atr_seed = sum(tr[1 : period + 1]) / period
    result[period] = atr_seed

    # Smoothed ATR
    for i in range(period + 1, n):
        prev_atr = result[i - 1] if result[i - 1] is not None else atr_seed
        result[i] = (prev_atr * (period - 1) + tr[i]) / period  # type: ignore[operator]

    return result


def calc_adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int,
) -> list[dict[str, float | None]]:
    """Calculate ADX with +DI and -DI.

    Returns list of dicts with keys: adx, plus_di, minus_di.
    Values are None until sufficient data.
    """
    n = len(highs)
    # Need period+1 candles for DMI calculation
    if n < period + 1:
        return [{"adx": None, "plus_di": None, "minus_di": None}] * n

    result: list[dict[str, float | None]] = [
        {"adx": None, "plus_di": None, "minus_di": None} for _ in range(n)
    ]

    # Calculate DM
    up_move = [0.0] * n
    down_move = [0.0] * n

    for i in range(1, n):
        hm = highs[i] - highs[i - 1]
        lm = lows[i - 1] - lows[i]
        up_move[i] = hm if (hm > lm and hm > 0) else 0.0
        down_move[i] = lm if (lm > hm and lm > 0) else 0.0

    # Smoothed DM
    atr = calc_atr(highs, lows, closes, period)

    # Seed with SMA
    plus_dm = sum(up_move[1 : period + 1]) / period
    minus_dm = sum(down_move[1 : period + 1]) / period
    smoothed_atr = atr[period] if atr[period] is not None else 0.0

    if smoothed_atr is not None and smoothed_atr > 0:
        _sa = smoothed_atr
        result[period] = {
            "adx": None,  # ADX needs another period of smoothing
            "plus_di": (plus_dm / _sa) * 100,
            "minus_di": (minus_dm / _sa) * 100,
        }

    # Smooth DMI forward
    prev_pd = plus_dm
    prev_md = minus_dm
    prev_sa = smoothed_atr

    for i in range(period + 1, n):
        new_pd = (prev_pd * (period - 1) + up_move[i]) / period
        new_md = (prev_md * (period - 1) + down_move[i]) / period
        new_sa = ((prev_sa or 0.0) * (period - 1) + (atr[i] or 0.0)) / period

        if new_sa > 0:
            plus_di = (new_pd / new_sa) * 100
            minus_di = (new_md / new_sa) * 100
            dx = (
                (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
                if (plus_di + minus_di) > 0
                else 0.0
            )
            result[i]["plus_di"] = plus_di
            result[i]["minus_di"] = minus_di

            # Smooth ADX
            if i > period + 1 and result[i - 1]["adx"] is not None:
                prev_adx_val = result[i - 1]["adx"]
                result[i]["adx"] = (prev_adx_val * (period - 1) + dx) / period  # type: ignore[operator]
            elif i == period + 1:
                # First ADX value
                result[i]["adx"] = dx

        prev_pd = new_pd
        prev_md = new_md
        prev_sa = new_sa

    return result


def find_swing_highs_lows(values: list[float], left: int, right: int) -> list[SwingPoint]:
    """Find swing highs and lows.

    Confirmed mode: swing at index i needs i-right to exist.
    Returns swing points where price at i is extreme vs neighbors.

    Swing high: value[i] > value[i-left..i-1] AND value[i] > value[i+1..i+right]
    Swing low: value[i] < value[i-left..i-1] AND value[i] < value[i+1..i+right]
    """
    swings: list[SwingPoint] = []

    # Confirmed swings: i must have left AND right neighbors
    for i in range(left, len(values) - right):
        is_high = True
        is_low = True

        # Check left side
        for j in range(i - left, i):
            if values[j] >= values[i]:
                is_high = False
            if values[j] <= values[i]:
                is_low = False

        if not is_high and not is_low:
            continue

        # Check right side (confirmed = must look forward)
        for j in range(i + 1, i + 1 + right):
            if j >= len(values):
                break
            if values[j] >= values[i]:
                is_high = False
            if values[j] <= values[i]:
                is_low = False

        if is_high:
            swings.append(
                SwingPoint(
                    index=i,
                    price=values[i],
                    side="high",
                    left_bars=left,
                    right_bars=right,
                    confirmed=True,
                )
            )
        elif is_low:
            swings.append(
                SwingPoint(
                    index=i,
                    price=values[i],
                    side="low",
                    left_bars=left,
                    right_bars=right,
                    confirmed=True,
                )
            )

    return swings


def find_candidate_swing_highs_lows(values: list[float], left: int) -> list[SwingPoint]:
    """Find candidate swing highs/lows without right bars.

    These are quick signals that may change as new candles arrive.
    Uses only left side for detection.
    """
    swings: list[SwingPoint] = []

    for i in range(left, len(values)):
        is_high = True
        is_low = True

        for j in range(i - left, i):
            if values[j] >= values[i]:
                is_high = False
            if values[j] <= values[i]:
                is_low = False

        if is_high:
            swings.append(
                SwingPoint(
                    index=i,
                    price=values[i],
                    side="high",
                    left_bars=left,
                    right_bars=0,
                    confirmed=False,
                )
            )
        elif is_low:
            swings.append(
                SwingPoint(
                    index=i,
                    price=values[i],
                    side="low",
                    left_bars=left,
                    right_bars=0,
                    confirmed=False,
                )
            )

    return swings


def classify_structure(swings: list[SwingPoint], idx: int) -> StructureFeature:
    """Classify market structure based on swing points up to idx.

    Uses confirmed swings within range to detect HH/HL/LH/LL.
    """
    # Filter swings up to idx
    relevant = [s for s in swings if s.index <= idx and s.confirmed]

    if len(relevant) < 4:
        return StructureFeature(
            structure=MarketStructure.UNKNOWN,
            hh=False,
            hl=False,
            lh=False,
            ll=False,
            swing_high=None,
            swing_low=None,
            valid=False,
        )

    # Find latest swing high and low before or at idx
    swing_highs = [s for s in relevant if s.side == "high"]
    swing_lows = [s for s in relevant if s.side == "low"]

    # Get last two swing highs and lows
    last_high = swing_highs[-1] if swing_highs else None
    prev_high = swing_highs[-2] if len(swing_highs) >= 2 else None
    last_low = swing_lows[-1] if swing_lows else None
    prev_low = swing_lows[-2] if len(swing_lows) >= 2 else None

    hh = last_high is not None and prev_high is not None and last_high.price > prev_high.price
    lh = last_high is not None and prev_high is not None and last_high.price < prev_high.price
    hl = last_low is not None and prev_low is not None and last_low.price > prev_low.price
    ll = last_low is not None and prev_low is not None and last_low.price < prev_low.price

    # Classify: bullish if HH and HL, bearish if LL and LH, ranging if neither
    if hh and hl:
        structure = MarketStructure.BULLISH
    elif ll and lh:
        structure = MarketStructure.BEARISH
    elif hh or hl or lh or ll:
        # Mixed signals — check dominant pattern
        if sum([hh, hl]) >= sum([lh, ll]):
            structure = MarketStructure.BULLISH
        elif sum([lh, ll]) >= sum([hh, hl]):
            structure = MarketStructure.BEARISH
        else:
            structure = MarketStructure.RANGING
    else:
        structure = MarketStructure.RANGING

    # Find nearest swing points around idx
    nearest_high = None
    nearest_low = None
    for s in reversed(relevant):
        if s.side == "high" and nearest_high is None:
            nearest_high = s
        if s.side == "low" and nearest_low is None:
            nearest_low = s
        if nearest_high is not None and nearest_low is not None:
            break

    return StructureFeature(
        structure=structure,
        hh=hh,
        hl=hl,
        lh=lh,
        ll=ll,
        swing_high=nearest_high,
        swing_low=nearest_low,
        valid=True,
    )


def compute_all_features(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    ema_short: int = 9,
    ema_long: int = 21,
    atr_period: int = 14,
    adx_period: int = 14,
    swing_left: int = 2,
    swing_right: int = 2,
) -> list[CandleFeatures]:
    """Compute all features for all candles.

    Returns list of CandleFeatures, one per candle.
    Features are None/invalid until warmup periods are met.
    """
    n = len(highs)
    # Find max warmup period
    # Calculate all indicators
    ema_short_vals = calc_ema(closes, ema_short)
    ema_long_vals = calc_ema(closes, ema_long)
    atr_vals = calc_atr(highs, lows, closes, atr_period)
    adx_vals = calc_adx(highs, lows, closes, adx_period)

    # Find swings (confirmed)
    swings = find_swing_highs_lows(closes, swing_left, swing_right)

    features: list[CandleFeatures] = []
    for i in range(n):
        sf = classify_structure(swings, i)

        features.append(
            CandleFeatures(
                index=i,
                ema_9=EMAFeature(
                    ema_value=ema_short_vals[i] or 0.0,
                    period=ema_short,
                    valid=(i >= ema_short - 1),
                ),
                ema_21=EMAFeature(
                    ema_value=ema_long_vals[i] or 0.0,
                    period=ema_long,
                    valid=(i >= ema_long - 1),
                ),
                atr_14=ATRFeature(
                    atr_value=atr_vals[i] or 0.0,
                    period=atr_period,
                    valid=(i >= atr_period),
                ),
                adx_14=ADXFeature(
                    adx_value=(adx_vals[i].get("adx") or 0.0) if i < len(adx_vals) else 0.0,
                    period=adx_period,
                    plus_di=(adx_vals[i].get("plus_di") or 0.0) if i < len(adx_vals) else 0.0,
                    minus_di=(adx_vals[i].get("minus_di") or 0.0) if i < len(adx_vals) else 0.0,
                    valid=(i >= adx_period + 2 and adx_vals[i].get("adx") is not None),
                ),
                structure=sf,
            )
        )

    return features
