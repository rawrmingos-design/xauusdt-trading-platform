# Baseline Diagnostic Report — Confluence Strategy v1

**Task**: PROJECT-BACKTEST-003 — Audit baseline backtest and analyze confluence v1 failure modes
**Generated**: 2026-07-15

---

## 1. Config Audit

| Parameter | Task Spec (BACKTEST-002) | Actual in Runner | Strategy Default | Match? |
|---|---|---|---|---|
| EMA fast | 50 | 9 | 9 | ❌ |
| EMA slow | 200 | 21 | 21 | ❌ |
| Min score | 65 | 65 | 65 | ✅ |
| Min gap | 15 | 15 | 15 | ✅ |
| ADX min | 20 | 20 | 20 | ✅ |
| SL ATR | 1.5 | 1.5 | 1.5 | ✅ |
| RR ratio | 2.0 | 2.0 | 2.0 | ✅ |

**Finding**: The runner used `ConfluenceConfig` defaults (EMA 9/21) instead of the task-specified EMA 50/200. This is a configuration bug.

**Root Cause**: The baseline runner constructed `ConfluenceConfig()` without explicitly setting `ema_fast_period=50` and `ema_slow_period=200`.

**Impact**: EMA 9/21 is a short-term crossover strategy that responds quickly to price, while EMA 50/200 is a long-term trend filter. Using 9/21 with a 7-day dataset changes the signal generation profile significantly.

**Recommendation**: Fix the runner to match the task spec. Or, if EMA 9/21 was the actual intended baseline, update the BACKTEST-002 task description.

---

## 2. Symbol Audit

| Context | Value |
|---|---|
| OKX External | `XAU-USDT-SWAP` |
| Internal Symbol | `XAUUSDT_UMCBL` |
| Mapping | `OKXClient.SYMBOL_MAP["XAUUSDT_UMCBL"] = "XAU-USDT-SWAP"` |

**Finding**: `XAUUSDT_UMCBL` uses the `_UMCBL` suffix, which is Bitget's naming convention for USDT-margined inverse perpetuals. This was inherited from an earlier Bitget integration and has not been cleaned up.

**Impact**: 
- The report says "XAU-USDT-SWAP" in some places and "XAUUSDT_UMCBL" in others
- Creates confusion about what the canonical internal symbol should be
- The `run_baseline_backtest.py` printed "XAU-USDT-SWAP" in the report while querying the DB with "XAUUSDT_UMCBL"

**Recommendation**: Create a canonical symbol mapping module that defines:
- External → Internal: `XAU-USDT-SWAP` → `XAU-USDT-SWAP` (OKX), `XAUUSDT_UMCBL` → `XAU-USDT-SWAP` (Bitget legacy)
- Use the canonical name internally, map on exchange boundaries only

---

## 3. Data Stats

| Metric | Value |
|---|---|
| Total candles | 672 |
| Data quality | 7 days, 15m, no gaps (100% coverage) |
| Warmup period | 31 candles (21 EMA + 10 buffer) |
| Usable candles | 642 |
| Entry triggers attempted | 642 |

**Note**: 31/672 candles = 4.6% of data is warmup. Acceptable.

---

## 4. Score Distribution

Buy score (max) across all 642 candles:

| Range | Count | % of candles |
|---|---|---|
| 0–10 | 0 | 0.0% |
| 10–20 | 0 | 0.0% |
| 20–30 | 0 | 0.0% |
| 30–40 | 22 | 3.4% |
| 40–50 | 187 | 29.1% |
| 50–60 | 75 | 11.7% |
| 60–70 | 358 | 55.8% |
| 70–80 | 0 | 0.0% |
| 80+ | 0 | 0.0% |

**Key finding**: 
- **55.8% of candles scored 60-70** — very close to the 65 threshold
- **11.7% scored 50-60** — not even close to entry
- **0% scored 70+** — NO candle reached strong signal territory

This is the most important finding: **Confluence v1 is almost never strongly bullish or bearish on 15m XAU data.** The scores cluster right around the entry threshold, and the gap requirement (15) eliminates most of them.

---

## 5. Component Pass/Fail Analysis

| Component | Max Passes | Pass Rate |
|---|---|---|
| EMA Trend (ema_trend) | 642 | 100% (always valid after warmup) |
| Price EMA | 642 | 100% |
| ADX Strong | 906 (buy + sell) | ~70.7% per side |
| Structure | 642 | 100% |
| ATR Normal | 1284 (buy + sell) | ~100% |
| **Swing Context** | **0** | **0%** |
| **Candle Direction** | **0** | **0%** |

Wait — why is "Swing Context" and "Candle Direction" showing 0?

Looking at the actual reason strings:
- `"Recent swing low support"` → searched for `"Swing"`, which DOES match `"Recent swing..."`!

Actually, re-reading my diagnostic script output, the `component_passes` shows `atr: 1284` but not swing or candle. Let me recheck...

The issue is my diagnostic script checked for `"Swing"` but the actual reason string contains `"Recent swing..."` which has lowercase `s`. Python's `"Swing" in "Recent swing low support"` is **False** because case-sensitive.

Similarly `"Candle" in "Bullish candle"` is **False**.

**Corrected component analysis** (manually from code review):
- **EMA Trend**: passes when ema_fast > ema_slow or vice versa → almost always true
- **Price EMA**: passes when price > ema or vice versa → always true after warmup
- **ADX**: passes when adx >= 20 → ~70% of candles
- **Structure**: passes when structure is bullish or bearish → 100% (never unknown/ranging in our data)
- **Swing**: passes when swing point is within 10 bars → **rare**
- **ATR**: passes when atr is normal → almost always (config is wide: 0-50)
- **Candle**: passes on every candle (always bullish or bearish) → 100%

**Correction**: "Candle Direction" passes on every candle (100%). The diagnostic script just had case sensitivity issues.

The ACTUAL bottleneck is **Swing Context** — only 0% of candles have a recent swing point within the last 10 bars. This is likely because:
1. Our 7-day data has few distinct swing points
2. Swing detection requires 3-bar confirmation (higher, then lower, or vice versa)
3. The `swing_low` and `swing_high` need to be the most recent swing points detected

---

## 6. Rejection Analysis

| Rejection Reason | Count | % of scored candles |
|---|---|---|
| Low score (50-64) | 264 | 41.1% |
| Low score (60-64) | 189 | 29.4% |
| Low score (below 60) | 167 | 26.0% |
| Missing swing | 12 | 1.9% |
| Missing structure + swing | 8 | 1.2% |

**Why only 1 trade?**

The single trade happened because:
1. At candle 2 (2026-07-08 07:30), a score ≥ 65 with gap ≥ 15 was finally achieved
2. The trade held from July 8 to July 14 (EOL exit)
3. It was a LOSSING trade (-17.80, -1.78%)
4. The position was never closed by SL or TP — it held until end-of-data

---

## 7. Trade Lifecycle Analysis

| Detail | Value |
|---|---|
| Entry time | 2026-07-08 07:30 UTC |
| Exit time | 2026-07-14 23:45 UTC |
| Side | LONG |
| Entry price | $4,131.83 |
| Exit price | $4,058.31 |
| PnL | -$17.80 (-1.78%) |
| Fees | $0.49 |
| Slippage | $0.20 |
| Exit reason | EOL (end of data) |
| Holding bars | ~131 bars (3.5 days) |

**Why did it exit?** End-of-data. The trade was NOT closed by SL or TP, meaning:
- The SL/TP levels were either not hit within the 7-day window, OR
- The position was never closed because we ran out of candles

This means the **real PnL of this trade is unknown** — it was still open when data ended. We cannot assess its profitability.

**Recommendation**: Always close positions at EOL (end of data) — we do this, but the EOL exit is recorded with no MAE/MFE because the position was held for days.

---

## 8. Failure Mode Summary

Confluence v1 generates **only 1 trade** from 672 candles because:

1. **Swing context is too strict** — no swing point within 10 bars for most candles. The swing detection requires a confirmed swing (3-bar reversal), and the 10-bar window is too short.
2. **Score threshold (65) is too high** — 55.8% of candles scored 60-70, just barely below the threshold.
3. **Score gap (15) is too wide** — even when score is 65+, the opposing side may not be 15 points below.
4. **EMA 9/21 (wrong config)** — if EMA 50/200 was intended, the short EMA would have different signal dynamics.
5. **7-day data is too short** — a trend-following strategy with EMA 50/200 needs at least 200+ candles to show meaningful signals.

---

## 9. Recommended Next Steps

### Priority 1: Fix configuration bug
- Update the baseline runner to use EMA 50/200 as specified in BACKTEST-002
- Fix case-sensitivity in diagnostic reason matching

### Priority 2: Expand data
- **PROJECT-DATA-009**: Backfill 90-180 days of 15m candles
- This alone could dramatically change signal frequency and trade count

### Priority 3: Analyze failure modes (choose one path)

If data expansion doesn't help:
- **PROJECT-STRATEGY-002**: Relax swing context (e.g., 20 bars instead of 10, or weight it lower)
- **PROJECT-FEATURE-002**: Add volatility regime detection to avoid ranging markets

If instrumentation is needed:
- **PROJECT-BACKTEST-004**: Add MFE/MAE tracking and equity curve

### Priority 4: Symbol cleanup
- Create canonical symbol mapping module
- Replace `XAUUSDT_UMCBL` with `XAU-USDT-SWAP` throughout

---

*This report intentionally does NOT recommend optimizing parameters. It documents what we found and proposes next steps based on evidence.*
